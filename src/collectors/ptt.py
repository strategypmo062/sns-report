"""PTT (ptt.cc) web scraping collector."""

from __future__ import annotations

import re
from datetime import datetime, date
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .base import BaseCollector, CollectedPost, CollectedComment
from .rate_limiter import RateLimiter

_BASE = "https://www.ptt.cc"
_DEFAULT_BOARDS = ["Gossiping", "MobileComm", "Lifeismoney"]
_COOKIES = {"over18": "1"}
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

_PUSH_RE = re.compile(
    r"^(?P<prefix>推|噓|→)\s*(?P<author>[^\s:]+)\s*:\s*(?P<body>.*?)"
    r"\s*(?P<ip>(?:\d{1,3}\.){3}\d{1,3})?\s*(?P<time>\d{2}/\d{2}\s+\d{2}:\d{2})\s*$"
)
_PUSH_SIMPLE_RE = re.compile(
    r"^(?P<prefix>推|噓|→)\s*(?P<author>[^\s:]+)\s*:\s*(?P<body>.+)$"
)


class PTTCollector(BaseCollector):
    def __init__(self):
        self._limiter = RateLimiter(2.0)
        self._session = requests.Session()
        self._session.cookies.update(_COOKIES)
        self._session.headers.update(_HEADERS)

    @staticmethod
    def platform_name() -> str:
        return "PTT"

    def is_configured(self) -> bool:
        return True  # No API key needed

    def collect(
        self,
        keywords: list[str],
        date_from: str,
        date_to: str,
        max_posts: int = 30,
        **kwargs,
    ) -> list[CollectedPost]:
        boards = kwargs.get("boards", _DEFAULT_BOARDS)
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)

        all_posts: list[CollectedPost] = []
        seen_urls: set[str] = set()

        for board in boards:
            for keyword in keywords:
                if len(all_posts) >= max_posts:
                    break
                urls = self._search_board(board, keyword, d_from, d_to, max_posts - len(all_posts))
                for url in urls:
                    if len(all_posts) >= max_posts:
                        break
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    post = self._fetch_article(url, board, d_from, d_to)
                    if post:
                        all_posts.append(post)

        return all_posts

    def _search_board(
        self, board: str, keyword: str,
        d_from: date, d_to: date, max_posts: int,
    ) -> list[str]:
        urls: list[str] = []
        search_url = f"{_BASE}/bbs/{board}/search?q={quote(keyword)}"
        today = date.today()

        for page_num in range(1, 20):
            if len(urls) >= max_posts:
                break
            self._limiter.wait()
            page_url = search_url if page_num == 1 else f"{search_url}&page={page_num}"

            try:
                resp = self._session.get(page_url, timeout=15)
                if resp.status_code != 200:
                    print(f"  [PTT] Search {board} page {page_num}: HTTP {resp.status_code}")
                    break
            except requests.RequestException as e:
                print(f"  [PTT] Search error: {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            entries = soup.select("div.r-ent")
            if not entries:
                break

            oldest_date_on_page: date | None = None
            for entry in entries:
                title_link = entry.select_one("div.title a")
                if not title_link:
                    continue
                href = title_link.get("href", "")
                if not href:
                    continue

                date_el = entry.select_one("div.date")
                entry_date = self._parse_list_date(date_el.text.strip() if date_el else "", today)

                if entry_date is not None:
                    if oldest_date_on_page is None or entry_date < oldest_date_on_page:
                        oldest_date_on_page = entry_date
                    if entry_date > d_to or entry_date < d_from:
                        continue  # 날짜 범위 밖 — 열지 않고 건너뜀

                urls.append(f"{_BASE}{href}")

            # 이 페이지의 가장 오래된 글이 이미 d_from보다 이전이면 다음 페이지는 더 오래됨
            if oldest_date_on_page is not None and oldest_date_on_page < d_from:
                break

        return urls

    @staticmethod
    def _parse_list_date(date_str: str, today: date) -> date | None:
        """Parse PTT list date 'M/DD' → date. Year inferred from today."""
        try:
            m, d = date_str.strip().split("/")
            month, day = int(m), int(d)
            candidate = date(today.year, month, day)
            if candidate > today:
                candidate = date(today.year - 1, month, day)
            return candidate
        except (ValueError, AttributeError):
            return None

    def _fetch_article(
        self, url: str, board: str, d_from: date, d_to: date
    ) -> CollectedPost | None:
        self._limiter.wait()
        try:
            resp = self._session.get(url, timeout=15)
            if resp.status_code != 200:
                return None
        except requests.RequestException:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract metadata from article-metaline spans
        meta = {}
        for metaline in soup.select("div.article-metaline"):
            tag = metaline.select_one("span.article-meta-tag")
            value = metaline.select_one("span.article-meta-value")
            if tag and value:
                meta[tag.text.strip()] = value.text.strip()

        author = meta.get("作者", "")
        title = meta.get("標題", "")
        time_str = meta.get("時間", "")

        # Check date range
        post_date = self._parse_ptt_time(time_str)
        if post_date and (post_date < d_from or post_date > d_to):
            return None

        # Extract body: main-content div, before push section
        main_content = soup.select_one("div#main-content")
        if not main_content:
            return None

        # Get full text, then extract body portion
        body_lines: list[str] = []
        comments: list[CollectedComment] = []

        # Remove metaline divs and push divs first to isolate body
        for tag in main_content.select("div.article-metaline, div.article-metaline-right"):
            tag.decompose()

        pushes = main_content.select("div.push")
        for push in pushes:
            push.extract()

        # Body text: everything in main-content after removing metadata and pushes
        raw_body = main_content.get_text()
        # Clean up: remove signature block
        sig_marker = "--\n※ 發信站"
        if sig_marker in raw_body:
            raw_body = raw_body[:raw_body.index(sig_marker)]
        body = raw_body.strip()

        # Parse push (comment) divs
        for push in pushes:
            tag_span = push.select_one("span.push-tag")
            user_span = push.select_one("span.push-userid")
            content_span = push.select_one("span.push-content")
            ipdatetime_span = push.select_one("span.push-ipdatetime")

            if not (tag_span and user_span and content_span):
                continue

            prefix = tag_span.text.strip()
            c_author = user_span.text.strip()
            c_body = content_span.text.strip()
            if c_body.startswith(": "):
                c_body = c_body[2:]

            c_time = ""
            if ipdatetime_span:
                raw_dt = ipdatetime_span.text.strip()
                # Extract MM/DD HH:MM from the ipdatetime string
                m = re.search(r"(\d{2}/\d{2}\s+\d{2}:\d{2})", raw_dt)
                if m:
                    c_time = m.group(1)

            comments.append(CollectedComment(
                author=c_author,
                body=c_body,
                time_text=c_time,
                prefix=prefix,
            ))

        # Format post_time in PTT native format for text_formatter
        return CollectedPost(
            url=url,
            sns_type="PTT",
            title=title,
            author=author,
            post_time=time_str,
            body=body,
            board=board,
            comments=comments,
        )

    @staticmethod
    def _parse_ptt_time(time_str: str) -> date | None:
        """Parse PTT time format 'Wed Apr  2 12:16:03 2026' to date."""
        if not time_str:
            return None
        try:
            dt = datetime.strptime(time_str.strip(), "%a %b %d %H:%M:%S %Y")
            return dt.date()
        except ValueError:
            # Try alternate format with extra spaces
            try:
                parts = time_str.split()
                if len(parts) >= 5:
                    normalized = f"{parts[0]} {parts[1]} {parts[2]} {parts[3]} {parts[4]}"
                    dt = datetime.strptime(normalized, "%a %b %d %H:%M:%S %Y")
                    return dt.date()
            except (ValueError, IndexError):
                pass
        return None
