"""Threads (threads.net) scraping collector using DrissionPage.

Threads는 로그인 없이 검색 페이지가 접근 가능하지만 JavaScript SPA이므로
실제 브라우저(DrissionPage)로 렌더링 후 DOM에서 데이터를 추출한다.

Threads API의 keyword_search는 앱 심사 통과 전까지 본인 계정 게시물만
검색 가능하므로, 내부 분석용으로 스크래핑 방식을 채택.

Install: pip install DrissionPage
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from urllib.parse import quote

from .base import BaseCollector, CollectedComment, CollectedPost
from .rate_limiter import RateLimiter

_SEARCH_URL = "https://www.threads.com/search?q={q}&serp_type=default&filter=recent"
_POST_URL_RE = re.compile(r"https://www\.threads\.(?:net|com)/@([^/]+)/post/([^/?#]+)")


class ThreadsCollector(BaseCollector):
    def __init__(self, env: dict | None = None):
        self._tab = None
        self._browser = None
        self._limiter = RateLimiter(2.0)

    @staticmethod
    def platform_name() -> str:
        return "Threads"

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

        print("  [Threads] _start_browser() 호출 전", flush=True)
        self._start_browser()
        print("  [Threads] _start_browser() 완료", flush=True)

        all_posts: list[CollectedPost] = []
        seen_urls: set[str] = set()

        try:
            for keyword in keywords:
                if len(all_posts) >= max_posts:
                    break
                url_entries = self._search_keyword(keyword, max_posts * 2)
                print(f"  [Threads] '{keyword}' 검색 결과: {len(url_entries)}개 후보", flush=True)

                for post_url, ts_iso in url_entries:
                    if len(all_posts) >= max_posts:
                        break
                    if post_url in seen_urls:
                        continue
                    seen_urls.add(post_url)

                    # 날짜 필터
                    post_date = self._parse_iso_date(ts_iso)
                    if post_date and (post_date < d_from or post_date > d_to):
                        continue

                    post = self._fetch_post(post_url, ts_iso)
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
        co.auto_port()
        co.set_argument("--window-position=-2400,-2400")  # 화면 밖

        self._browser = Chromium(co)
        self._tab = self._browser.latest_tab
        print("  [Threads] Browser 준비 완료", flush=True)

    def _stop_browser(self) -> None:
        if self._browser:
            try:
                self._browser.quit()
            except Exception:
                pass
            self._browser = None
            self._tab = None

    # ── search ──────────────────────────────────────────────────────────────

    def _search_keyword(
        self, keyword: str, target_count: int
    ) -> list[tuple[str, str]]:
        """검색 페이지에서 (post_url, iso_timestamp) 리스트 반환."""
        self._limiter.wait()
        url = _SEARCH_URL.format(q=quote(keyword))
        self._tab.get(url)
        time.sleep(8)  # JS 렌더링 대기

        # 더 많은 결과 로드를 위해 스크롤
        prev_count = 0
        for _ in range(10):  # 최대 10회 스크롤
            count = self._tab.run_js(
                "return document.querySelectorAll('a[href*=\"/post/\"]').length;"
            )
            if count >= target_count or count == prev_count:
                break
            prev_count = count
            self._tab.run_js("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(4)

        # post URL과 가장 가까운 time 태그를 매칭
        raw = self._tab.run_js("""
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[href*="/post/"]');
            for (const link of links) {
                const href = link.href;
                if (seen.has(href)) continue;
                // /@username/post/ID 형태만 허용 (미디어/좋아요 등 서브 경로 제외)
                if (!href.match(/\/@[^\/]+\/post\/[^\/\?#]+$/)) continue;
                seen.add(href);

                // 같은 게시물 카드 안의 time 태그 찾기 (위로 8레벨까지 탐색)
                let container = link;
                let timeEl = null;
                for (let i = 0; i < 8 && container; i++) {
                    timeEl = container.querySelector('time');
                    if (timeEl) break;
                    container = container.parentElement;
                }
                results.push({
                    href: href,
                    datetime: timeEl ? timeEl.getAttribute('datetime') : '',
                });
            }
            return JSON.stringify(results);
        """)

        try:
            entries = json.loads(raw or "[]")
        except Exception:
            entries = []

        return [(e["href"], e.get("datetime", "")) for e in entries if e.get("href")]

    # ── post fetch ──────────────────────────────────────────────────────────

    def _fetch_post(self, post_url: str, ts_hint: str) -> CollectedPost | None:
        """개별 게시물 페이지에서 본문 + 댓글 추출."""
        self._limiter.wait()

        m = _POST_URL_RE.search(post_url)
        if not m:
            return None
        author = m.group(1)

        try:
            self._tab.get(post_url)
            time.sleep(5)
            # Related threads 섹션 lazy loading 트리거 (스크롤 후 복귀)
            self._tab.run_js("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            self._tab.run_js("window.scrollTo(0, 0);")
        except Exception as e:
            print(f"  [Threads] 페이지 로드 실패 {post_url}: {e}")
            return None

        # JavaScript로 게시물 카드들을 구조화 추출
        # 각 <time> 태그마다 "그 time만 포함하는 가장 큰 ancestor"를 카드로 잡는다.
        # 이렇게 해야 본문/댓글/답글이 각각 독립된 카드로 분리된다.
        # "Related threads" 섹션 이후의 카드는 제외한다.
        raw = self._tab.run_js("""
            const items = [];

            // "Related threads" 경계 요소 탐색
            const relatedEl = document.querySelector('[data-pagelet^="threads_logged_out_related_posts_"]');

            const times = document.querySelectorAll('time');
            for (const t of times) {
                // "Related threads" 이후의 time 태그는 건너뜀
                if (relatedEl) {
                    if (relatedEl.compareDocumentPosition(t) & 4) {
                        break;
                    }
                }

                // 위로 한 단계씩 올라가면서, 다른 <time>이 포함되지 않는
                // "가장 큰" ancestor를 찾는다 (= 그 time 전용 카드)
                let card = null;
                let c = t.parentElement;
                while (c && c !== document.body) {
                    // Related threads 섹션을 포함하는 컨테이너는 무효
                    if (relatedEl && c.contains(relatedEl)) break;
                    // 다른 time을 포함하면 너무 넓은 것 — 멈춤
                    if (c.querySelectorAll('time').length > 1) break;
                    // userLink가 있어야 유효한 카드
                    if (c.querySelector('a[href^="/@"]')) {
                        card = c;  // 갱신하면서 계속 위로 (가장 큰 것 유지)
                    }
                    c = c.parentElement;
                }

                if (card && card.innerText.trim().length > 10) {
                    const userLink = card.querySelector('a[href^="/@"]');
                    if (userLink) {
                        const username = userLink.getAttribute('href').replace('/@', '').split('/')[0];
                        items.push({
                            username: username,
                            datetime: t.getAttribute('datetime') || '',
                            text: card.innerText,
                        });
                    }
                }
            }
            return JSON.stringify(items);
        """)

        try:
            items = json.loads(raw or "[]")
        except Exception:
            items = []

        # 중복 제거 (같은 카드가 여러 time 태그로 잡힐 수 있음)
        deduped: list[dict] = []
        seen_keys: set[tuple] = set()
        for it in items:
            # 같은 username이 여러 댓글을 달 수 있으므로 text 첫 80자도 키에 포함
            key = (it.get("username", ""), it.get("datetime", ""), (it.get("text", "") or "")[:80])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(it)

        if not deduped:
            print(f"  [Threads] 본문 추출 실패: {post_url}")
            return None

        # 메인 게시물 = URL의 author와 일치하는 첫 항목
        main = None
        main_idx = -1
        for i, it in enumerate(deduped):
            if it.get("username", "").lower() == author.lower():
                main = it
                main_idx = i
                break

        if not main:
            # fallback: 첫 항목을 메인으로
            main = deduped[0]
            main_idx = 0

        main_body = self._clean_text(main.get("text", ""), main.get("username", ""))
        post_time = main.get("datetime") or ts_hint

        # 메인 이후의 항목들을 댓글로 (Related threads 등 노이즈 차단)
        comments: list[CollectedComment] = []
        for it in deduped[main_idx + 1:]:
            body = self._clean_text(it.get("text", ""), it.get("username", ""))
            if not body:
                continue
            # "Post not available" 같은 placeholder 제외
            if "not available" in body.lower() and len(body) < 30:
                continue
            comments.append(CollectedComment(
                author=it.get("username", ""),
                body=body,
                time_text=it.get("datetime", ""),
            ))

        print(f"  [Threads] @{author}: 본문 {len(main_body)}자, 댓글 {len(comments)}개")

        return CollectedPost(
            url=post_url,
            sns_type="Threads",
            title="",
            author=author,
            post_time=post_time,
            body=main_body,
            board="",
            comments=comments,
        )

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_text(raw: str, username: str) -> str:
        """카드의 innerText에서 username/시간/노이즈 제거 후 본문만 추출."""
        if not raw:
            return ""
        lines = [ln.strip() for ln in raw.splitlines()]
        # 제거 패턴: username 라인, 시간 표시(1h, 2d, 3m, 03/11/26 등), Translate, 숫자만
        time_re = re.compile(r"^(\d+[smhd]|\d{1,2}/\d{1,2}/\d{2,4}|\d+분 전|\d+시간 전|어제|방금)$")
        cleaned: list[str] = []
        for ln in lines:
            if not ln:
                continue
            if ln == username:
                continue
            if time_re.match(ln):
                continue
            if ln in ("Translate", "번역", "Post not available"):
                continue
            if ln == "·":  # Threads UI 구분자 중점
                continue
            if re.match(r"^·?\s*(Author|작성자)$", ln):  # 작성자 레이블 (단독 또는 "· Author")
                continue
            if re.match(r"^\d+$", ln):  # 좋아요/댓글 카운트 숫자만
                continue
            cleaned.append(ln)
        return "\n".join(cleaned).strip()

    @staticmethod
    def _parse_iso_date(iso_str: str) -> date | None:
        if not iso_str:
            return None
        try:
            return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).date()
        except ValueError:
            return None
