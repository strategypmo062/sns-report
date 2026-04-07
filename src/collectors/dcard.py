"""DCard (dcard.tw) collector using camoufox (Firefox stealth browser).

DCard is behind Cloudflare. camoufox ships a patched Firefox build that
hides automation fingerprints and is effective against CF Turnstile.

Install:
    pip install camoufox[geoip]
    python -m camoufox fetch
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime

from .base import BaseCollector, CollectedPost, CollectedComment
from .rate_limiter import RateLimiter

_URL_LINE_RE = __import__("re").compile(r"^\s*https?://\S+\s*$")

_API_BASE = "/service/api/v2"
_SEARCH_PATH = f"{_API_BASE}/search/all"
_POST_PATH = f"{_API_BASE}/posts/{{post_id}}"
_COMMENTS_PATH = f"{_API_BASE}/posts/{{post_id}}/comments"

_CF_TITLE_MARKERS = ("Just a moment", "Cloudflare", "Checking your browser")


class DCardCollector(BaseCollector):
    def __init__(self):
        self._cam = None       # Camoufox instance
        self._browser = None   # Playwright Browser
        self._context = None   # BrowserContext
        self._page = None
        self._limiter = RateLimiter(2.0)

    @staticmethod
    def platform_name() -> str:
        return "DCard"

    def is_configured(self) -> bool:
        try:
            import camoufox  # noqa: F401
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
        if self._page is None:
            print("  [DCard] Browser not ready — aborting collection")
            return []

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
        from camoufox.sync_api import Camoufox

        is_server = bool(os.environ.get("RENDER") or os.environ.get("DOCKER"))
        # "virtual" lets camoufox manage its own xvfb display on server.
        headless = "virtual" if is_server else False

        print(f"  [DCard] Starting camoufox (headless={headless!r}) ...")
        try:
            self._cam = Camoufox(
                headless=headless,
                locale="zh-TW",
            )
            print("  [DCard] Camoufox instance created, entering context ...")
            self._browser = self._cam.__enter__()
            print("  [DCard] Browser process launched ✓")
        except Exception as e:
            print(f"  [DCard] Camoufox launch failed: {e}")
            self._cam = None
            self._browser = None
            return

        print("  [DCard] Opening page ...")
        try:
            self._page = self._browser.new_page(
                locale="zh-TW",
                extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9"},
            )
            self._context = self._page.context
        except Exception as e:
            print(f"  [DCard] Failed to open page: {e}")
            self._stop_browser()
            return

        print("  [DCard] Launched camoufox (Firefox stealth)")

        if not self._ensure_clearance():
            print("  [DCard] Cloudflare clearance FAILED — collection may return empty")
            return

        try:
            print(f"  [DCard] Browser ready (title: {self._page.title()!r})")
        except Exception:
            print("  [DCard] Browser ready")

    def _stop_browser(self) -> None:
        self._page = None
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._cam is not None:
            try:
                self._cam.__exit__(None, None, None)
            except Exception:
                pass
            self._cam = None
            self._browser = None

    # ── Cloudflare clearance ────────────────────────────────────────────────

    def _ensure_clearance(self) -> bool:
        """Load the DCard homepage and poll until Cloudflare lets us through."""
        max_wait = float(os.environ.get("DCARD_CF_WAIT_SEC", "30"))

        try:
            self._page.goto("https://www.dcard.tw/", wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"  [DCard] Initial homepage load error: {e}")

        if self._poll_clearance(max_wait):
            return True

        # Slow path: one explicit reload, longer wait.
        print("  [DCard] CF not cleared — retrying with full page load")
        try:
            self._page.goto("https://www.dcard.tw/", wait_until="load", timeout=30000)
        except Exception as e:
            print(f"  [DCard] Retry homepage load error: {e}")
            return False
        return self._poll_clearance(max_wait)

    def _poll_clearance(self, max_wait: float) -> bool:
        """Poll until Cloudflare is out of the way.

        Success signal: page title is not a CF challenge marker AND a
        lightweight DCard API call through the browser returns JSON.
        """
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            time.sleep(1.0)
            try:
                title = self._page.title() or ""
            except Exception:
                title = ""
            if any(marker in title for marker in _CF_TITLE_MARKERS):
                continue
            probe = self._js_fetch_raw(
                f"{_SEARCH_PATH}?query=test&field=all&sort=latest&country=TW&nsfw=false&platform=web"
            )
            if probe is not None:
                return True
        return False

    # ── API helpers ─────────────────────────────────────────────────────────

    def _js_fetch_raw(self, path: str) -> dict | list | None:
        """Run fetch() inside the page without rate limiting (used by probes)."""
        js = """
        (path) => fetch(path)
            .then(r => r.ok ? r.json() : Promise.reject(r.status))
            .then(data => JSON.stringify(data))
            .catch(e => JSON.stringify({"__error": String(e)}))
        """
        try:
            raw = self._page.evaluate(js, path)
            if not raw:
                return None
            data = json.loads(raw)
            if isinstance(data, dict) and "__error" in data:
                return None
            return data
        except Exception:
            return None

    def _js_fetch(self, path: str) -> dict | list | None:
        """Rate-limited variant for actual data calls."""
        self._limiter.wait()
        js = """
        (path) => fetch(path)
            .then(r => r.ok ? r.json() : Promise.reject(r.status))
            .then(data => JSON.stringify(data))
            .catch(e => JSON.stringify({"__error": String(e)}))
        """
        try:
            raw = self._page.evaluate(js, path)
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
