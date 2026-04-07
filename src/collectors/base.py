"""Base collector interface and data structures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CollectedComment:
    author: str
    body: str
    time_text: str
    prefix: str = ""  # PTT: 推/噓/→


@dataclass
class CollectedPost:
    url: str
    sns_type: str  # Must match config.py SNS_DOMAIN_MAP values
    title: str
    author: str
    post_time: str  # Platform-native time format
    body: str
    board: str = ""  # PTT board name, DCard forum name, etc.
    comments: list[CollectedComment] = field(default_factory=list)


class BaseCollector(ABC):
    @staticmethod
    @abstractmethod
    def platform_name() -> str:
        ...

    @abstractmethod
    def collect(
        self,
        keywords: list[str],
        date_from: str,
        date_to: str,
        max_posts: int = 30,
        **kwargs,
    ) -> list[CollectedPost]:
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        ...
