from __future__ import annotations

from typing import Any

from contracts import StructuredRecord


def parse_llm_output(data: dict[str, Any]) -> list[StructuredRecord]:
    records = data.get("records", [])
    result: list[StructuredRecord] = []

    for r in records:
        result.append(
            StructuredRecord(
                original_text=r["original_text"],
                ko_translation=r["ko_translation"],
                date=r["date"],
                main_category=r["main_category"],
                sub_category=r["sub_category"],
                sentiment=r["sentiment"],
                sns=r["sns"],
                url=r["url"],
            )
        )

    return result
