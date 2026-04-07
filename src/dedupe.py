from __future__ import annotations

import hashlib


def dedupe_key(url: str, original_text: str) -> str:
    payload = f"{url}\n{original_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def text_key(original_text: str) -> str:
    payload = original_text.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
