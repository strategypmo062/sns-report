"""Mobile01 collector - stub (not yet implemented)."""
from .base import BaseCollector, CollectedPost

class Mobile01Collector(BaseCollector):
    @staticmethod
    def platform_name() -> str:
        return "Mobile01"
    def is_configured(self) -> bool:
        return True
    def collect(self, keywords, date_from, date_to, max_posts=30, **kwargs):
        return []
