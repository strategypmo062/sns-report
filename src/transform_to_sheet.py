from __future__ import annotations

from contracts import StructuredRecord


def records_to_sheet_rows(records: list[StructuredRecord]) -> list[list[str]]:
    return [r.to_sheet_row() for r in records]
