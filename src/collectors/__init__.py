"""SNS data collectors registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseCollector

_REGISTRY: dict[str, type[BaseCollector]] = {}


def _ensure_registered() -> None:
    if _REGISTRY:
        return
    from .ptt import PTTCollector
    from .dcard import DCardCollector
    from .threads import ThreadsCollector
    from .youtube import YouTubeCollector
    from .mobile01 import Mobile01Collector

    for cls in (PTTCollector, DCardCollector, ThreadsCollector, YouTubeCollector, Mobile01Collector):
        _REGISTRY[cls.platform_name()] = cls


def get_collector(name: str) -> BaseCollector:
    _ensure_registered()
    name_lower = name.lower()
    for key, cls in _REGISTRY.items():
        if key.lower() == name_lower:
            return cls()
    raise ValueError(f"Unknown platform: {name}. Available: {list(_REGISTRY.keys())}")


def available_platforms() -> list[str]:
    _ensure_registered()
    return list(_REGISTRY.keys())
