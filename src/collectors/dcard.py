"""DCard (dcard.tw) collector using patchright (stealth Playwright fork).

DCard is behind Cloudflare. patchright patches the key detection vectors
that vanilla Playwright leaks (Runtime.enable, console API, automation
flags, navigator.webdriver) and is recommended for CF bypass.

We use ``launch_persistent_context`` so that the user-data-dir caches
Cloudflare's cookies and profile state across runs — no manual cookie
caching required.

Install:
    pip install patchright
    patchright install chrome   # or: patchright install chromium
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path

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
        self._pw = None
        self._context = None
        self._page = None
        self._limiter = RateLimiter(2.0)

    @staticmethod
    def platform_name() -> str:
        return "DCard"

    def is_configured(self) -> bool:
        try:
            from patchright.sync_api import sync_playwright  # noqa: F401
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
        from patchright.sync_api import sync_playwright

        is_server = bool(os.environ.get("RENDER") or os.environ.get("DOCKER"))

        # Persistent context dir holds cookies + Chrome profile state across
        # runs, so we don't need a separate cookie cache file.
        default_dir = "/tmp/dcard-profile" if is_server else ".cache/dcard-profile"
        user_data_dir = os.environ.get("DCARD_USER_DATA_DIR", default_dir)
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        # patchright README's stealth recommendation: real Chrome channel,
        # headed mode (xvfb on server), no_viewport=True, no custom UA.
        args = [
            "--lang=zh-TW",
        ]
        if is_server:
            args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
        else:
            # Local: shove the headed window offscreen so it doesn't pop up.
            args.append("--window-position=-2400,-2400")

        try:
            self._pw = sync_playwright().start()
        except Exception as e:
            print(f"  [DCard] sync_playwright().start() failed: {e}")
            self._pw = None
            return

        # Try Chrome channel first, fall back to bundled chromium if Chrome
        # is missing on this host.
        last_err: Exception | None = None
        for channel in ("chrome", None):
            try:
                self._context = self._pw.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=channel,
                    headless=False,
                    no_viewport=True,
                    locale="zh-TW",
                    args=args,
                )
                if channel:
                    print(f"  [DCard] Launched patchright with channel={channel}")
                else:
                    print("  [DCard] Launched patchright with bundled chromium")
                break
            except Exception as e:
                last_err = e
                self._context = None
        if self._context is None:
            print(f"  [DCard] Failed to launch browser: {last_err}")
            self._stop_browser()
            return

        try:
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else self._context.new_page()
            )
        except Exception as e:
            print(f"  [DCard] Failed to open page: {e}")
            self._stop_browser()
            return

        if not self._ensure_clearance():
            print("  [DCard] Cloudflare clearance FAILED — collection may return empty")
            return

        try:
            print(f"  [DCard] Browser ready (title: {self._page.title()!r})")
        except Exception:
            print("  [DCard] Browser ready")

    def _stop_browser(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
        self._page = None

    # ── Cloudflare clearance ────────────────────────────────────────────────

    def _ensure_clearance(self) -> bool:
        """Try the persistent context first; if cookies are stale, do a fresh
        homepage load and poll until Cloudflare lets us through."""
        max_wait = float(os.environ.get("DCARD_CF_WAIT_SEC", "30"))

        # Fast path: persistent context may already have valid CF cookies.
        # Going straight to a lightweight API endpoint avoids re-running the
        # interstitial and is the cheapest possible probe.
        try:
            self._page.goto("https://www.dcard.tw/", wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"  [DCard] Initial homepage load error: {e}")

        if self._poll_clearance(max_wait):
            return True

        # Slow path: one explicit reload, longer wait.
        print("  [DCard] Persistent context didn't pass — retrying clearance")
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
        """Run fetch() inside the page without rate limiting (used by probes).

        Note: we keep ``isolated_context=True`` (patchright default) so the
        evaluation runs in an isolated world. This is what hides patchright
        from CDP-based detection — DO NOT pass ``isolated_context=False``.
        """
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
