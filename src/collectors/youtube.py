"""YouTube collector - stub (not yet implemented)."""
from .base import BaseCollector, CollectedPost

class YouTubeCollector(BaseCollector):
    @staticmethod
    def platform_name() -> str:
        return "YouTube"
    def is_configured(self) -> bool:
        import os
        return bool(os.environ.get("YOUTUBE_API_KEY", ""))
    def collect(self, keywords, date_from, date_to, max_posts=30, **kwargs):
        return []
