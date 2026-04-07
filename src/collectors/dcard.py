"""DCard (dcard.tw) collector using DrissionPage.

DCard is behind Cloudflare Turnstile. DrissionPage launches real Chrome,
passes the challenge, then uses in-page JavaScript fetch() to call
DCard's internal API—sharing the browser's valid Cloudflare cookies.

Install: pip install DrissionPage
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime

from .base import BaseCollector, CollectedPost, CollectedComment
from .rate_limiter import RateLimiter

_URL_LINE_RE = __import__("re").compile(r"^\s*https?://\S+\s*$")

_API_BASE = "/service/api/v2"
_SEARCH_PATH = f"{_API_BASE}/search/all"
_POST_PATH = f"{_API_BASE}/posts/{{post_id}}"
_COMMENTS_PATH = f"{_API_BASE}/posts/{{post_id}}/comments"


class DCardCollector(BaseCollector):
    def __init__(self):
        self._tab = None
        self._browser = None
        self._limiter = RateLimiter(2.0)

    @staticmethod
    def platform_name() -> str:
        return "DCard"

    def is_configured(self) -> bool:
        try:
            from DrissionPage import Chromium  # noqa: F401
            return True
        except ImportError:
            return False

    # ── public ──────────────────────────────────────────────────────────────

    def collect(
        self,
        keywords: list[str],
        date_from: str,
        date_to: str,
        max_posts: int = 30,
        **kwargs,
    ) -> list[CollectedPost]:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)

        self._start_browser()

        all_posts: list[CollectedPost] = []
        seen_ids: set[int] = set()

        try:
            for keyword in keywords:
                if len(all_posts) >= max_posts:
                    break
                results = self._search_keyword(keyword)
                for post_id, forum_alias in results:
                    if len(all_posts) >= max_posts:
                        break
                    if post_id in seen_ids:
                        continue
                    seen_ids.add(post_id)

                    post = self._fetch_post(post_id, forum_alias, d_from, d_to)
                    if post:
                        all_posts.append(post)
        finally:
            self._stop_browser()

        return all_posts

    # ── browser lifecycle ───────────────────────────────────────────────────

    def _start_browser(self) -> None:
        from DrissionPage import Chromium, ChromiumOptions

        co = ChromiumOptions()
        co.set_argument("--no-first-run")
        co.set_argument("--lang=zh-TW")
        co.auto_port()
        # Headed mode required — Cloudflare detects headless Chrome
        # Window is minimized to stay out of the way
        co.set_argument("--window-position=-2400,-2400")

        self._browser = Chromium(co)
        self._tab = self._browser.latest_tab

        # Load homepage to obtain Cloudflare clearance cookies
        print("  [DCard] Loading homepage for Cloudflare clearance...")
        self._tab.get("https://www.dcard.tw/")
        time.sleep(10)
        print(f"  [DCard] Browser ready (title: {self._tab.title})")

    def _stop_browser(self) -> None:
        if self._browser:
            try:
                self._browser.quit()
            except Exception:
                pass
            self._browser = None
            self._tab = None

    # ── API helpers ─────────────────────────────────────────────────────────

    def _js_fetch(self, path: str) -> dict | list | None:
        """Execute fetch() inside the page and return parsed JSON."""
        self._limiter.wait()
        js = f"""
        return fetch('{path}')
            .then(r => r.ok ? r.json() : Promise.reject(r.status))
            .then(data => JSON.stringify(data))
            .catch(e => JSON.stringify({{"__error": String(e)}}));
        """
        try:
            raw = self._tab.run_js(js)
            time.sleep(0.5)
            if not raw:
                return None
            data = json.loads(raw)
            if isinstance(data, dict) and "__error" in data:
                print(f"  [DCard] API error on {path}: {data['__error']}")
                return None
            return data
        except Exception as e:
            print(f"  [DCard] JS fetch failed for {path}: {e}")
            return None

    # ── search & fetch ──────────────────────────────────────────────────────

    def _search_keyword(self, keyword: str) -> list[tuple[int, str]]:
        """Search DCard for posts matching *keyword*."""
        import urllib.parse
        encoded = urllib.parse.quote(keyword)
        data = self._js_fetch(
            f"{_SEARCH_PATH}?query={encoded}&field=all&sort=latest&country=TW&nsfw=false&platform=web"
        )

        # Unwrap common envelope shapes e.g. {"posts": [...], "data": [...]}
        if isinstance(data, dict):
            for key in ("posts", "data", "items"):
                if isinstance(data.get(key), list):
                    print(f"  [DCard] Search response wrapped under key '{key}'")
                    data = data[key]
                    break
            else:
                print(f"  [DCard] Unexpected search response: dict with keys {list(data.keys())}")
                return []
        elif not isinstance(data, list):
            print(f"  [DCard] Unexpected search response type: {type(data).__name__}")
            return []

        results: list[tuple[int, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            # New API: items[n]["searchPost"]["post"] / ["forum"]
            sp = item.get("searchPost")
            if isinstance(sp, dict):
                post_data = sp.get("post") or {}
                forum_data = sp.get("forum") or {}
                post_id = post_data.get("id")
                forum_alias = forum_data.get("alias") or post_data.get("forumAlias") or "talk"
            else:
                # Legacy flat format
                post_id = item.get("id")
                forum_alias = item.get("forumAlias") or item.get("forumName") or "talk"
            if post_id:
                results.append((int(post_id), str(forum_alias)))

        print(f"  [DCard] Search '{keyword}': {len(results)} posts")
        return results

    def _fetch_post(
        self,
        post_id: int,
        forum_alias: str,
        d_from: date,
        d_to: date,
    ) -> CollectedPost | None:
        """Fetch a single DCard post with its comments."""
        data = self._js_fetch(_POST_PATH.format(post_id=post_id))
        if not isinstance(data, dict):
            return None

        # Date filter
        created_at = data.get("createdAt", "")
        post_date = self._parse_date(created_at)
        if post_date and (post_date < d_from or post_date > d_to):
            print(f"  [DCard] Post {post_id} date-filtered ({post_date} not in [{d_from}, {d_to}])")
            return None

        # Fetch comments
        comments_data = self._js_fetch(_COMMENTS_PATH.format(post_id=post_id))
        comments: list[CollectedComment] = []
        if isinstance(comments_data, list):
            for i, c in enumerate(comments_data, 1):
                body = self._strip_url_lines(c.get("content", ""))
                if body.strip():
                    comments.append(CollectedComment(
                        author=f"B{i}",
                        body=body,
                        time_text=c.get("createdAt", ""),
                    ))

        url = f"https://www.dcard.tw/f/{forum_alias}/p/{post_id}"
        print(f"  [DCard] Fetched {url} ({len(comments)} comments)")

        return CollectedPost(
            url=url,
            sns_type="DCard",
            title=data.get("title", ""),
            author="",
            post_time=data.get("createdAt", ""),
            body=self._strip_url_lines(data.get("content", "")),
            board=forum_alias,
            comments=comments,
        )

    @staticmethod
    def _strip_url_lines(text: str) -> str:
        """Remove standalone URL lines from body text.

        DCard post bodies may contain image URLs like
        ``https://megapx-assets.dcard.tw/images/...`` on their own line.
        These trip ``split_url_blocks()`` in preparse.py, splitting one
        post into multiple blocks. Stripping them here is safe because
        the post URL is already stored separately in CollectedPost.url.
        """
        return "\n".join(
            line for line in text.splitlines()
            if not _URL_LINE_RE.match(line)
        )

    @staticmethod
    def _parse_date(iso_str: str) -> date | None:
        if not iso_str:
            return None
        try:
            return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).date()
        except ValueError:
            return None
