from __future__ import annotations

from contracts import StructuredRecord


def _safe_get(row: list[str], idx: int) -> str:
    return row[idx] if idx < len(row) else ""


def sheet_rows_to_records(rows: list[list[str]]) -> list[StructuredRecord]:
    records: list[StructuredRecord] = []
    for row in rows:
        # Skip empty rows
        if not any((c or "").strip() for c in row):
            continue

        records.append(
            StructuredRecord(
                original_text=_safe_get(row, 0),
                ko_translation=_safe_get(row, 1),
                date=_safe_get(row, 2),
                main_category=_safe_get(row, 3),
                sub_category=_safe_get(row, 4),
                sentiment=_safe_get(row, 5),
                sns=_safe_get(row, 6),
                url=_safe_get(row, 7),
            )
        )
    return records

