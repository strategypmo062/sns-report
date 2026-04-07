from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s)]+")
PTT_COMMENT_PREFIX_RE = re.compile(r"^(推|噓|→)\s")

# Known SNS domains that should act as block separators in split_url_blocks.
# Any standalone URL line whose host does NOT contain one of these is treated
# as embedded content within the current block (not a new block start).
_SNS_BLOCK_DOMAINS = {
    "ptt.cc",
    "dcard.tw",
    "threads.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "mobile01.com",
    "play.google.com",
    "apps.apple.com",
}


def _is_sns_block_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(domain in host for domain in _SNS_BLOCK_DOMAINS)
PTT_COMMENT_RE = re.compile(r"^(推|噓|→)\s*(?P<author>[^\s:]+)\s*:\s*(?P<body>.*)$")
THREADS_RELATIVE_TIME_RE = re.compile(r"^\d+\s*(초|분|시간|일)$")
THREADS_UI_NOISE_RE = re.compile(r"^일부 추가 답글은 확인할 수 없습니다")
SEPARATOR_RE = re.compile(r"^[=\-]{2,}$")
PTT_TIME_HEADER_RE = re.compile(r"^時間\s*(?P<time>.+)$")
PTT_LINE_TIME_RE = re.compile(r"(?P<md>\d{2}/\d{2})\s+(?P<hm>\d{2}:\d{2})\s*$")
PTT_IP_RE = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}$")
PTT_NOISE_LINES = {"推文自動更新已關閉", "==="}

PTT_METADATA_PREFIXES = (
    "批踢踢實業坊",
    "返回看板",
    "作者",
    "看板",
    "標題",
    "時間",
    "--",
    "※ 發信站",
    "※ 文章網址",
    "※ 編輯",
)


def extract_first_url(raw_text: str) -> str | None:
    m = URL_RE.search(raw_text)
    return m.group(0) if m else None


def split_url_blocks(raw_text: str) -> list[tuple[str, str]]:
    """
    Split input text into URL-led blocks.
    Each block starts at a line whose entire content is a URL.
    """
    lines = raw_text.splitlines()
    blocks: list[tuple[str, str]] = []
    current_url: str | None = None
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        is_full_url_line = bool(re.fullmatch(r"https?://\S+", stripped))

        if is_full_url_line and _is_sns_block_url(stripped):
            if current_url is not None:
                blocks.append((current_url, "\n".join(current_lines).strip()))
            current_url = stripped
            current_lines = []
            continue

        if current_url is not None:
            current_lines.append(line.rstrip("\n"))

    if current_url is not None:
        blocks.append((current_url, "\n".join(current_lines).strip()))

    return blocks


def split_ptt_units(raw_text: str) -> tuple[str, list[str]]:
    post, _, comments = split_ptt_units_with_meta(raw_text)
    return post, [c["text"] for c in comments if c.get("text", "").strip()]


def _extract_ptt_anchor_time_text(lines: list[str]) -> str:
    for line in lines:
        m = PTT_TIME_HEADER_RE.match(line.strip())
        if not m:
            continue
        raw = " ".join(m.group("time").split())
        try:
            dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y")
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            continue
    return ""


def _strip_tail_time_and_ip(body: str) -> tuple[str, str]:
    s = body.rstrip()
    m = PTT_LINE_TIME_RE.search(s)
    if not m:
        return s, ""

    ts = f"{m.group('md')} {m.group('hm')}"
    head = s[: m.start()].rstrip()
    if PTT_IP_RE.search(head):
        head = PTT_IP_RE.sub("", head).rstrip()
    return head, ts


def split_ptt_units_with_meta(raw_text: str) -> tuple[str, str, list[dict[str, str]]]:
    """
    Returns:
      post_unit: single post text block (1 row)
      comment_units: list of one-line comment units (1 comment = 1 row)
    """
    lines = raw_text.splitlines()
    anchor_time_text = _extract_ptt_anchor_time_text(lines)
    comment_units: list[dict[str, str]] = []
    last_seen_time_text = anchor_time_text

    first_comment_idx: int | None = None
    for i, line in enumerate(lines):
        if PTT_COMMENT_PREFIX_RE.match(line.strip()):
            first_comment_idx = i
            break

    if first_comment_idx is None:
        pre_comment_lines = lines
    else:
        pre_comment_lines = lines[:first_comment_idx]
        tail_lines = lines[first_comment_idx:]

        for line in tail_lines:
            s = line.rstrip()
            stripped = s.strip()

            if not stripped:
                continue

            if stripped in PTT_NOISE_LINES:
                continue

            if stripped.startswith(PTT_METADATA_PREFIXES):
                continue

            if stripped.startswith("http://") or stripped.startswith("https://"):
                continue

            m = PTT_COMMENT_RE.match(stripped)
            if m:
                author = m.group("author").strip()
                body = m.group("body").strip()
                body, line_time = _strip_tail_time_and_ip(body)
                time_text = line_time or last_seen_time_text or anchor_time_text
                if not body:
                    body = stripped

                if comment_units and comment_units[-1]["author"] == author:
                    if body:
                        prev_text = comment_units[-1]["text"]
                        comment_units[-1]["text"] = (
                            f"{prev_text}\n{body}" if prev_text else body
                        )
                    if line_time:
                        comment_units[-1]["time_text"] = line_time
                        last_seen_time_text = line_time
                else:
                    comment_units.append(
                        {
                            "author": author,
                            "time_text": time_text,
                            "text": body,
                        }
                    )
                    if time_text:
                        last_seen_time_text = time_text
                continue

            # Non-prefixed lines after comment start are usually wrapped continuation
            # lines; attach them to the latest parsed comment.
            if comment_units:
                prev = comment_units[-1]["text"]
                comment_units[-1]["text"] = f"{prev}\n{s}" if prev else s

    post_lines: list[str] = []
    for line in pre_comment_lines:
        s = line.rstrip()
        if not s:
            post_lines.append("")
            continue

        if s.startswith(PTT_METADATA_PREFIXES):
            continue

        # Keep URL line in post body only when it appears as actual content.
        if s.startswith("http://") or s.startswith("https://"):
            continue

        post_lines.append(s)

    # Trim excessive leading/trailing blank lines while preserving inner breaks.
    while post_lines and post_lines[0] == "":
        post_lines.pop(0)
    while post_lines and post_lines[-1] == "":
        post_lines.pop()

    post_unit = "\n".join(post_lines)
    for c in comment_units:
        c["text"] = c["text"].strip()
        if not c["time_text"]:
            c["time_text"] = anchor_time_text
    return post_unit, anchor_time_text, [c for c in comment_units if c.get("text", "").strip()]


_DCARD_NOISE_RE = re.compile(
    r"^(Heart|\d+|thumbnail|閒聊|reactions?|comments?|Check out .+|More posts from .+)$",
    re.IGNORECASE,
)


def _is_dcard_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _DCARD_NOISE_RE.match(s):
        return True
    if re.match(r"^\d+\s*(reactions?|comments?)", s, re.IGNORECASE):
        return True
    return False


def _dcard_segments(lines: list[str]) -> list[list[str]]:
    """Split lines into non-empty segments separated by blank lines."""
    segments: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(s)
    if current:
        segments.append(current)
    return segments


def _looks_like_dcard_author(s: str) -> bool:
    """Short single line with no sentence-ending punctuation = likely an author name."""
    if len(s) > 30:
        return False
    if re.search(r"[。！？…]", s):
        return False
    if re.match(r"^\d+$", s):
        return False
    return True


def split_dcard_units(raw_text: str) -> tuple[str, list[str]]:
    """
    Split a DCard block into (post, [comments]).
    Uses 'All comments' as the divider between post body and comment section.
    If absent, the whole block is treated as the post.
    """
    lines = raw_text.splitlines()

    divider_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "All comments":
            divider_idx = i
            break

    # ── Post section ──────────────────────────────────────────────────────────
    post_src = lines[:divider_idx] if divider_idx is not None else lines
    post_lines: list[str] = []
    for line in post_src:
        s = line.rstrip()
        if s.strip().startswith("http://") or s.strip().startswith("https://"):
            continue
        if _is_dcard_noise(s):
            continue
        post_lines.append(s)
    while post_lines and not post_lines[0].strip():
        post_lines.pop(0)
    while post_lines and not post_lines[-1].strip():
        post_lines.pop()
    post = "\n".join(post_lines).strip()

    if divider_idx is None:
        return post, []

    # ── Comment section ───────────────────────────────────────────────────────
    comment_lines = lines[divider_idx + 1:]
    segments = _dcard_segments(comment_lines)

    comments: list[str] = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        # Single short line → likely an author name; skip it and consume like count
        if len(seg) == 1 and _looks_like_dcard_author(seg[0]):
            i += 1
            # Skip standalone like-count number
            if i < len(segments) and len(segments[i]) == 1 and re.match(r"^\d+$", segments[i][0]):
                i += 1
            # Collect comment body (may span multiple consecutive segments)
            body_parts: list[str] = []
            while i < len(segments):
                next_seg = segments[i]
                # Stop when we hit the next author line
                if len(next_seg) == 1 and _looks_like_dcard_author(next_seg[0]):
                    break
                body_parts.append("\n".join(next_seg))
                i += 1
            text = "\n".join(body_parts).strip()
            if text:
                comments.append(text)
        else:
            # No author prefix found — treat the segment as a standalone comment
            text = "\n".join(seg).strip()
            if text and not re.match(r"^\d+$", text):
                comments.append(text)
            i += 1

    return post, comments


def split_generic_units(raw_text: str) -> tuple[str, list[str]]:
    """
    Fallback splitter for non-PTT, non-DCard text blocks.
    Splits on manual '---' separator lines into post + comment units.
    If no separator exists, treats the whole block as one post unit.
    """
    segments: list[list[str]] = [[]]
    for line in raw_text.splitlines():
        s = line.rstrip()
        if s.startswith("http://") or s.startswith("https://"):
            continue
        if s.strip() in ("---", "===", "-----"):
            segments.append([])
        else:
            segments[-1].append(s)

    cleaned = ["\n".join(seg).strip() for seg in segments]
    cleaned = [c for c in cleaned if c]

    if not cleaned:
        return "", []

    return cleaned[0], cleaned[1:]


def _looks_like_threads_username(s: str) -> bool:
    stripped = s.strip()
    if not stripped:
        return False
    if " " in stripped:
        return False
    if stripped in {"·", "작성자"}:
        return False
    if THREADS_RELATIVE_TIME_RE.match(stripped):
        return False
    if SEPARATOR_RE.match(stripped):
        return False
    if URL_RE.search(stripped):
        return False
    if THREADS_UI_NOISE_RE.match(stripped):
        return False
    return True


def split_threads_units(block_text: str) -> list[dict[str, str]]:
    """
    Parse a Threads block body (without URL line) into units.
    Unit format:
      {"unit_type": "post|comment", "author": str, "time_text": str, "text": str}
    """
    raw_lines = block_text.splitlines()
    lines = [l.rstrip() for l in raw_lines]

    starts: list[int] = []
    for i in range(len(lines) - 1):
        if _looks_like_threads_username(lines[i]) and THREADS_RELATIVE_TIME_RE.match(lines[i + 1].strip()):
            starts.append(i)

    units: list[dict[str, str]] = []
    if not starts:
        text = block_text.strip()
        if text:
            units.append(
                {
                    "unit_type": "post",
                    "author": "",
                    "time_text": "",
                    "text": text,
                }
            )
        return units

    for idx, start in enumerate(starts):
        next_start = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        author = lines[start].strip()
        time_text = lines[start + 1].strip()

        content_lines: list[str] = []
        for j in range(start + 2, next_start):
            s = lines[j]
            stripped = s.strip()
            if not stripped:
                content_lines.append("")
                continue
            if stripped in {"·", "작성자"}:
                continue
            if THREADS_UI_NOISE_RE.match(stripped):
                continue
            if SEPARATOR_RE.match(stripped):
                continue
            content_lines.append(s)

        while content_lines and content_lines[0] == "":
            content_lines.pop(0)
        while content_lines and content_lines[-1] == "":
            content_lines.pop()

        text = "\n".join(content_lines).strip()
        if not text:
            continue

        units.append(
            {
                "unit_type": "post" if idx == 0 else "comment",
                "author": author,
                "time_text": time_text,
                "text": text,
            }
        )

    return units
