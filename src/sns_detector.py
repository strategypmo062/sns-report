from __future__ import annotations

from urllib.parse import urlparse

from config import SNS_DOMAIN_MAP


def detect_sns_from_url(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None

    for domain, sns in SNS_DOMAIN_MAP.items():
        if domain in host:
            return sns
    return None
