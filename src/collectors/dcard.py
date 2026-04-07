"""DCard (dcard.tw) collector using DrissionPage.

DCard is behind Cloudflare Turnstile. DrissionPage launches real Chrome,
passes the challenge, then uses in-page JavaScript fetch() to call
DCard's internal API—sharing the browser's valid Cloudflare cookies.

Clearance cookies (cf_clearance) are cached on disk so that subsequent
runs can skip the homepage wait as long as the cookie is still valid.

Install: pip install DrissionPage
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

# Cookies we want to persist across runs to skip the CF challenge.
_CF_COOKIE_NAMES = {"cf_clearance", "__cf_bm", "__cflb", "_cfuvid"}
_CF_TITLE_MARKERS = ("Just a moment", "Cloudflare", "Checking your browser")
_COOKIE_CACHE_MAX_AGE_SEC = 24 * 60 * 60  # 24h


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
        if self._tab is None:
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
        from DrissionPage import Chromium, ChromiumOptions

        is_server = bool(os.environ.get("RENDER") or os.environ.get("DOCKER"))

        co = ChromiumOptions()
        co.set_argument("--no-first-run")
        co.set_argument("--lang=zh-TW")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.auto_port()
        if is_server:
            # Render/Docker: required for Linux container environments.
            # --headless=new is intentionally NOT added — same reasoning as
            # threads.py (commit 83cd34a): CDP websocket handshake 404 on
            # DrissionPage 4.1.x + Chrome 14x, plus Cloudflare easily
            # detects headless. Render runs under xvfb virtual display.
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
        else:
            # Local: minimize the window offscreen so headed Chrome doesn't
            # get in the way.
            co.set_argument("--window-position=-2400,-2400")

        try:
            self._browser = Chromium(co)
            self._tab = self._browser.latest_tab
        except Exception as e:
            print(f"  [DCard] Failed to launch Chromium: {e}")
            self._browser = None
            self._tab = None
            return

        # Reduce webdriver fingerprint (best-effort).
        try:
            self._tab.run_js(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        except Exception:
            pass

        # Try cached cookies first — may let us skip the CF wait entirely.
        if self._try_cached_clearance():
            print("  [DCard] Using cached Cloudflare clearance")
            return

        # Fresh clearance path.
        if not self._perform_clearance():
            print("  [DCard] Cloudflare clearance FAILED — collection may return empty")
            return

        # Persist new cookies for next run.
        self._save_clearance_cookies()
        print(f"  [DCard] Browser ready (title: {self._tab.title})")

    def _stop_browser(self) -> None:
        if self._browser:
            try:
                self._browser.quit()
            except Exception:
                pass
            self._browser = None
            self._tab = None

    # ── Cloudflare clearance ────────────────────────────────────────────────

    def _perform_clearance(self) -> bool:
        """Load dcard.tw and wait until Cloudflare clearance completes.

        Returns True if clearance appears to have succeeded.
        """
        max_wait = float(os.environ.get("DCARD_CF_WAIT_SEC", "30"))
        max_attempts = 2

        for attempt in range(1, max_attempts + 1):
            print(
                f"  [DCard] Loading homepage for Cloudflare clearance "
                f"(attempt {attempt}/{max_attempts}, max {max_wait:.0f}s)..."
            )
            try:
                self._tab.get("https://www.dcard.tw/")
            except Exception as e:
                print(f"  [DCard] Homepage load error: {e}")
                time.sleep(2.0)
                continue

            if self._poll_clearance(max_wait):
                return True

            title = ""
            try:
                title = self._tab.title or ""
            except Exception:
                pass
            print(f"  [DCard] Clearance attempt {attempt} failed (title: {title!r})")
            time.sleep(2.0)

        return False

    def _poll_clearance(self, max_wait: float) -> bool:
        """Poll until Cloudflare is out of the way.

        Success signal: page title is not a CF challenge marker AND a
        lightweight DCard API call through the browser returns JSON.
        We can't rely on cf_clearance cookie alone — DCard's CF
        configuration often uses only __cf_bm or a JS-only challenge
        without any persistent cookie.
        """
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            time.sleep(1.0)
            try:
                title = self._tab.title or ""
            except Exception:
                title = ""
            if any(marker in title for marker in _CF_TITLE_MARKERS):
                continue
            # Title looks OK — probe the API to confirm CF isn't intercepting.
            probe = self._js_fetch_raw(
                f"{_SEARCH_PATH}?query=test&field=all&sort=latest&country=TW&nsfw=false&platform=web"
            )
            if probe is not None:
                return True
        return False

    def _try_cached_clearance(self) -> bool:
        """Load cached cookies from disk and verify they still work."""
        path = self._cookie_cache_path()
        if not path.exists():
            return False
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return False
        if age > _COOKIE_CACHE_MAX_AGE_SEC:
            return False

        try:
            with path.open("r", encoding="utf-8") as f:
                cookies = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(cookies, list) or not cookies:
            return False

        # Navigate to the domain first so cookies stick to the right origin.
        try:
            self._tab.get("https://www.dcard.tw/")
        except Exception:
            return False

        # Inject cached cookies.
        try:
            self._tab.set.cookies(cookies)
        except Exception as e:
            print(f"  [DCard] Cached cookie injection failed: {e}")
            return False

        # Verify by hitting a lightweight API; if Cloudflare is happy, this
        # returns JSON without being intercepted.
        probe = self._js_fetch_raw(f"{_SEARCH_PATH}?query=test&field=all&sort=latest&country=TW&nsfw=false&platform=web")
        if probe is None:
            return False
        return True

    def _save_clearance_cookies(self) -> None:
        path = self._cookie_cache_path()
        try:
            all_cookies = self._tab.cookies(all_domains=False, all_info=True)
        except TypeError:
            # Older signature fallback.
            try:
                all_cookies = self._tab.cookies()
            except Exception as e:
                print(f"  [DCard] Could not read cookies: {e}")
                return
        except Exception as e:
            print(f"  [DCard] Could not read cookies: {e}")
            return

        # Normalise to plain dicts; DrissionPage sometimes returns a custom
        # list-like where items already behave as dicts.
        cf_cookies: list[dict] = []
        for c in all_cookies or []:
            try:
                d = dict(c)
            except Exception:
                continue
            name = d.get("name")
            if name in _CF_COOKIE_NAMES:
                cf_cookies.append(d)

        if not cf_cookies:
            # JS-only CF challenge — nothing persistent to cache, which is
            # fine. The next run will just re-probe the homepage.
            print("  [DCard] No persistent CF cookies — skipping cache (JS-only challenge)")
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(cf_cookies, f, ensure_ascii=False)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            print(f"  [DCard] Cached {len(cf_cookies)} CF cookies → {path}")
        except OSError as e:
            print(f"  [DCard] Could not write cookie cache: {e}")

    @staticmethod
    def _cookie_cache_path() -> Path:
        raw = os.environ.get("DCARD_COOKIE_CACHE_PATH", ".cache/dcard_cookies.json")
        return Path(raw).expanduser()

    # ── API helpers ─────────────────────────────────────────────────────────

    def _js_fetch_raw(self, path: str) -> dict | list | None:
        """Like _js_fetch but without rate-limiting (used during probes)."""
        js = f"""
        return fetch('{path}')
            .then(r => r.ok ? r.json() : Promise.reject(r.status))
            .then(data => JSON.stringify(data))
            .catch(e => JSON.stringify({{"__error": String(e)}}));
        """
        try:
            raw = self._tab.run_js(js)
            if not raw:
                return None
            data = json.loads(raw)
            if isinstance(data, dict) and "__error" in data:
                return None
            return data
        except Exception:
            return None

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
