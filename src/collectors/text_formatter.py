"""Convert CollectedPost objects into raw text that preparse.py can parse.

Each format function produces text matching what a user would paste from
the browser for that platform, so the existing split_url_blocks() and
platform-specific splitters work without modification.
"""

from __future__ import annotations

from .base import CollectedPost


def format_post(post: CollectedPost) -> str:
    sns = post.sns_type
    if sns == "PTT":
        return _format_ptt(post)
    if sns == "DCard":
        return _format_dcard(post)
    if sns == "Threads":
        return _format_threads(post)
    return _format_generic(post)


def format_all(posts: list[CollectedPost]) -> str:
    blocks = [format_post(p) for p in posts]
    return "\n\n".join(blocks)


# ── PTT ──────────────────────────────────────────────────────────────────────


def _format_ptt(post: CollectedPost) -> str:
    lines: list[str] = []
    lines.append(post.url)
    lines.append("")
    lines.append(f"批踢踢實業坊›看板 {post.board}關於我們聯絡資訊")
    lines.append("返回看板")
    lines.append(f"作者{post.author}")
    lines.append(f"看板{post.board}")
    lines.append(f"標題{post.title}")
    lines.append(f"時間{post.post_time}")
    lines.append("")
    lines.append(post.body)
    lines.append("")
    lines.append("--")
    lines.append(f"※ 發信站: 批踢踢實業坊(ptt.cc)")
    lines.append(f"※ 文章網址: {post.url}")

    for c in post.comments:
        prefix = c.prefix or "推"
        # Reproduce PTT comment format: {prefix} {author}: {body}{IP}{time}
        lines.append(f"{prefix} {c.author}: {c.body} {c.time_text}")

    return "\n".join(lines)


# ── DCard ────────────────────────────────────────────────────────────────────


def _format_dcard(post: CollectedPost) -> str:
    lines: list[str] = []
    lines.append(post.url)
    lines.append("")
    lines.append(post.body)

    if post.comments:
        lines.append("")
        lines.append("All comments")
        lines.append("")
        for c in post.comments:
            # Put author and body on consecutive lines (no blank between them)
            # so they form a single multi-line segment in _dcard_segments().
            # This avoids the _looks_like_dcard_author() heuristic misfiring on
            # short comment bodies, because the check only triggers on len(seg)==1.
            author = c.author if c.author else "匿名"
            lines.append(author)
            lines.append(c.body)
            lines.append("")

    return "\n".join(lines)


# ── Threads ──────────────────────────────────────────────────────────────────


def _format_threads(post: CollectedPost) -> str:
    lines: list[str] = []
    lines.append(post.url)
    lines.append("")
    lines.append(post.author)
    lines.append(post.post_time)
    lines.append(post.body)

    for c in post.comments:
        lines.append(c.author)
        lines.append(c.time_text)
        lines.append(c.body)

    return "\n".join(lines)


# ── Generic (YouTube, Mobile01, etc.) ────────────────────────────────────────


def _format_generic(post: CollectedPost) -> str:
    lines: list[str] = []
    lines.append(post.url)
    lines.append("")
    if post.title:
        lines.append(post.title)
    lines.append(post.body)

    for c in post.comments:
        lines.append("---")
        lines.append(c.body)

    return "\n".join(lines)
