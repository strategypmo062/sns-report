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
from datetime import date, datetime, timezone, timedelta
from urllib.parse import quote

from .base import BaseCollector, CollectedComment, CollectedPost
from .rate_limiter import RateLimiter

_SEARCH_URL = "https://www.threads.com/search?q={q}&serp_type=default&filter=recent"
_POST_URL_RE = re.compile(r"https://www\.threads\.(?:net|com)/@([^/]+)/post/([^/?#]+)")


class ThreadsCollector(BaseCollector):
    def __init__(self, env: dict | None = None):
        import os
        self._tab = None
        self._browser = None
        self._limiter = RateLimiter(2.0)
        env = env or {}
        self._username = env.get("THREADS_USERNAME") or os.environ.get("THREADS_USERNAME")
        self._password = env.get("THREADS_PASSWORD") or os.environ.get("THREADS_PASSWORD")
        self._logged_in = False

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

        if self._username and self._password:
            try:
                self._login()
            except Exception as e:
                print(f"  [Threads] 로그인 실패 (비로그인 모드로 진행): {e}", flush=True)
        else:
            print("  [Threads] THREADS_USERNAME/PASSWORD 미설정 — 비로그인 모드", flush=True)

        all_posts: list[CollectedPost] = []
        seen_urls: set[str] = set()

        try:
            for keyword in keywords:
                if len(all_posts) >= max_posts:
                    break
                url_entries = self._search_keyword(keyword, max_posts * 2, date_from=d_from)
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
                        print(f"  [Threads] 날짜 필터: {post_date} (범위 {d_from}~{d_to}) → 스킵", flush=True)
                        continue

                    post = self._fetch_post(post_url, ts_iso)
                    if post:
                        all_posts.append(post)
        finally:
            self._stop_browser()

        return all_posts

    # ── browser lifecycle ───────────────────────────────────────────────────

    def _start_browser(self) -> None:
        import os
        from DrissionPage import Chromium, ChromiumOptions

        co = ChromiumOptions()
        co.set_argument("--no-first-run")
        co.auto_port()

        # 서버(Render/Docker)에서는 샌드박스 해제 + shm 우회만 적용.
        # --headless=new 는 Render의 Chromium + DrissionPage 4.1.x 조합에서도
        # CDP 웹소켓 핸드셰이크 404를 유발하므로 제거.
        # Render는 xvfb 가상 디스플레이(CMD: xvfb-run)가 뜨므로 headed 모드로
        # 동작 가능하며 DCard 수집기도 동일 방식으로 동작 중.
        is_server = bool(os.environ.get("RENDER") or os.environ.get("DOCKER"))
        if is_server:
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            # Render 무료 티어(512MB) 메모리 절약 — Chromium이 CPU/메모리를 독점하면
            # uvicorn 이벤트 루프까지 스케줄 기회를 잃어 헬스체크도 무응답이 된다.
            co.set_argument("--disable-gpu")
            co.set_argument("--disable-extensions")
            co.set_argument("--disable-background-networking")
            co.set_argument("--disable-default-apps")
            co.set_argument("--renderer-process-limit=1")
            co.set_argument("--js-flags=--max-old-space-size=256")

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

    # ── login ───────────────────────────────────────────────────────────────

    def _login(self) -> None:
        """Threads 로그인 페이지에서 ID/PW로 로그인.

        비로그인 상태에서는 검색 결과가 약 10건으로 제한되므로 무한 스크롤이
        동작하지 않는다. Instagram 자격증명으로 로그인해야 정상 스크롤 가능.
        """
        self._limiter.wait()
        login_url = "https://www.threads.com/login"
        print(f"  [Threads] 로그인 페이지 로딩: {login_url}", flush=True)
        self._tab.get(login_url, timeout=30)
        time.sleep(5)  # JS 렌더링 대기

        # username/password 입력 필드 탐색 (Threads는 placeholder 한국어/영어 혼재)
        try:
            user_input = self._tab.ele("css:input[autocomplete='username']", timeout=10)
            pass_input = self._tab.ele("css:input[autocomplete='current-password']", timeout=10)
        except Exception:
            # 폴백: type 기반 셀렉터
            user_input = self._tab.ele("css:input[type='text']", timeout=10)
            pass_input = self._tab.ele("css:input[type='password']", timeout=10)

        if not user_input or not pass_input:
            raise RuntimeError("로그인 폼 필드를 찾지 못함")

        user_input.input(self._username)
        time.sleep(0.5)
        pass_input.input(self._password)
        time.sleep(0.5)

        # 제출 버튼 — type=submit 우선, 실패 시 Enter 키
        submit_btn = self._tab.ele("css:button[type='submit']", timeout=5)
        if submit_btn:
            submit_btn.click()
        else:
            pass_input.input("\n")

        print("  [Threads] 로그인 폼 제출, 응답 대기...", flush=True)
        time.sleep(8)  # 리다이렉트 + 세션 쿠키 발급 대기

        # 성공 판정: URL이 /login 이 아니거나, 로그인 후에만 보이는 요소 존재
        current_url = self._tab.url or ""
        if "/login" in current_url:
            # 챌린지/2FA/CAPTCHA 페이지 가능성
            raise RuntimeError(f"로그인 후에도 /login URL 유지됨: {current_url}")

        self._logged_in = True
        print(f"  [Threads] 로그인 성공: {current_url}", flush=True)

    # ── search ──────────────────────────────────────────────────────────────

    def _search_keyword(
        self, keyword: str, target_count: int, date_from: date | None = None
    ) -> list[tuple[str, str]]:
        """검색 페이지에서 (post_url, iso_timestamp) 리스트 반환.

        Threads는 가상 스크롤(virtual scroll)을 사용해 DOM에 항상 소수의
        글만 유지한다. 스크롤 후 마지막에 한 번에 수집하면 그 시점에 보이는
        글만 잡힌다. 따라서 스크롤마다 현재 DOM의 글을 수집해 누적한다.
        """
        self._limiter.wait()
        url = _SEARCH_URL.format(q=quote(keyword))
        print(f"  [Threads] 검색 페이지 로딩: {url}", flush=True)
        self._tab.get(url, timeout=30)
        print("  [Threads] 검색 페이지 로드 완료", flush=True)
        time.sleep(8)  # JS 렌더링 대기

        _COLLECT_JS = """
            const results = [];
            const links = document.querySelectorAll('a[href*="/post/"]');
            for (const link of links) {
                const href = link.href;
                if (!href.match(/\\/@[^\\/]+\\/post\\/[^\\/\\?#]+$/)) continue;
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
        """

        accumulated: dict[str, str] = {}  # href → datetime

        def _collect_visible() -> None:
            try:
                raw = self._tab.run_js(_COLLECT_JS)
                for e in json.loads(raw or "[]"):
                    href = e.get("href")
                    if href and href not in accumulated:
                        accumulated[href] = e.get("datetime", "")
            except Exception:
                pass

        # 첫 화면 수집
        _collect_visible()

        # 스크롤하며 누적 수집 (최대 50회)
        # 종료 조건 1: DOM에 새 URL이 3회 연속 늘지 않음 (페이지 바닥)
        # 종료 조건 2: 새로 추가된 글이 모두 date_from 이전이면 3회 연속 시 종료
        #             (Threads는 최신순 → 아래로 갈수록 오래된 글만 나옴)
        no_new_streak = 0
        past_range_streak = 0
        for _ in range(50):
            if len(accumulated) >= target_count:
                break
            prev_urls = set(accumulated.keys())
            self._tab.run_js("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)
            _collect_visible()
            new_urls = set(accumulated.keys()) - prev_urls

            if not new_urls:
                no_new_streak += 1
                past_range_streak = 0
                if no_new_streak >= 3:
                    print(f"  [Threads] '{keyword}' 스크롤 조기 종료: DOM 새 글 없음", flush=True)
                    break
            else:
                no_new_streak = 0
                if date_from is not None:
                    new_dates = [self._parse_iso_date(accumulated[u]) for u in new_urls]
                    valid = [d for d in new_dates if d is not None]
                    if valid and all(d < date_from for d in valid):
                        past_range_streak += 1
                        if past_range_streak >= 3:
                            print(f"  [Threads] '{keyword}' 스크롤 조기 종료: 수집 범위 이전 글만 노출", flush=True)
                            break
                    else:
                        past_range_streak = 0
                else:
                    past_range_streak = 0

        print(f"  [Threads] '{keyword}' 스크롤 수집 완료: {len(accumulated)}건", flush=True)
        return list(accumulated.items())

    # ── post fetch ──────────────────────────────────────────────────────────

    def _fetch_post(self, post_url: str, ts_hint: str) -> CollectedPost | None:
        """개별 게시물 페이지에서 본문 + 댓글 추출."""
        self._limiter.wait()

        m = _POST_URL_RE.search(post_url)
        if not m:
            return None
        author = m.group(1)

        try:
            print(f"  [Threads] 포스트 로딩: {post_url}", flush=True)
            self._tab.get(post_url, timeout=30)
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
        """Threads `<time datetime>`는 UTC ISO 타임스탬프.
        KST(Asia/Seoul, UTC+9)로 변환 후 날짜만 반환해야 사용자가 UI에서
        선택한 날짜 범위와 일치한다."""
        if not iso_str:
            return None
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            kst = timezone(timedelta(hours=9))
            return dt.astimezone(kst).date()
        except ValueError:
            return None
