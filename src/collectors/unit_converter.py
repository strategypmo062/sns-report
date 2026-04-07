"""
Direct conversion from CollectedPost → units, bypassing text serialization.

This module provides the 'direct path' for automated collection:
  CollectedPost → units → LLM

The manual path (text_formatter → preparse) remains unchanged.
"""

from __future__ import annotations

from .base import CollectedPost


def collected_posts_to_units(
    posts: list[CollectedPost],
) -> list[tuple[str, str, list[dict[str, str]]]]:
    """Convert collected posts directly to units.

    Returns list of (url, sns_type, units) per post.
    Each unit: {"unit_type": "post"|"comment", "time_text": "...", "text": "..."}
    Threads units also include "author".
    """
    results: list[tuple[str, str, list[dict[str, str]]]] = []
    for post in posts:
        sns = post.sns_type
        converter = _CONVERTERS.get(sns.lower(), _generic_units)
        units = converter(post)
        if units:
            results.append((post.url, sns, units))
    return results


# ── Platform-specific converters ─────────────────────────────────────────────


def _ptt_units(post: CollectedPost) -> list[dict[str, str]]:
    """PTT: merge consecutive same-author comments (mirrors preparse logic)."""
    units: list[dict[str, str]] = []

    if post.body.strip():
        units.append({
            "unit_type": "post",
            "time_text": post.post_time,
            "text": post.body.strip(),
        })

    # Merge consecutive same-author comments (replicates preparse.py:171-189)
    merged: list[dict[str, str]] = []
    for c in post.comments:
        body = c.body.strip()
        if not body:
            continue
        if merged and merged[-1]["_author"] == c.author:
            prev_text = merged[-1]["text"]
            merged[-1]["text"] = f"{prev_text}\n{body}" if prev_text else body
            if c.time_text:
                merged[-1]["time_text"] = c.time_text
        else:
            merged.append({
                "_author": c.author,
                "unit_type": "comment",
                "time_text": c.time_text,
                "text": body,
            })

    for m in merged:
        del m["_author"]
        units.append(m)

    return units


def _dcard_units(post: CollectedPost) -> list[dict[str, str]]:
    """DCard: one unit per comment, preserving blank lines in body intact."""
    units: list[dict[str, str]] = []

    if post.body.strip():
        units.append({
            "unit_type": "post",
            "time_text": post.post_time,
            "text": post.body.strip(),
        })

    for c in post.comments:
        body = c.body.strip()
        if body:
            units.append({
                "unit_type": "comment",
                "time_text": c.time_text,
                "text": body,
            })

    return units


def _threads_units(post: CollectedPost) -> list[dict[str, str]]:
    """Threads: include author field in each unit."""
    units: list[dict[str, str]] = []

    if post.body.strip():
        units.append({
            "unit_type": "post",
            "author": post.author,
            "time_text": post.post_time,
            "text": post.body.strip(),
        })

    for c in post.comments:
        body = c.body.strip()
        if body:
            units.append({
                "unit_type": "comment",
                "author": c.author,
                "time_text": c.time_text,
                "text": body,
            })

    return units


def _generic_units(post: CollectedPost) -> list[dict[str, str]]:
    """Generic fallback for YouTube, Mobile01, etc."""
    units: list[dict[str, str]] = []

    body = post.body.strip()
    if post.title and body:
        text = f"{post.title}\n{body}"
    elif post.title:
        text = post.title
    else:
        text = body

    if text:
        units.append({
            "unit_type": "post",
            "time_text": post.post_time,
            "text": text,
        })

    for c in post.comments:
        cbody = c.body.strip()
        if cbody:
            units.append({
                "unit_type": "comment",
                "time_text": c.time_text,
                "text": cbody,
            })

    return units


_CONVERTERS = {
    "ptt": _ptt_units,
    "dcard": _dcard_units,
    "threads": _threads_units,
}
