from __future__ import annotations

import argparse
from pathlib import Path

from config import SheetConfig
from dedupe import dedupe_key
from env_loader import load_env_file
from sheets_client import append_rows, build_sheets_service, ensure_sheet_exists, read_rows
from sheet_row_mapper import sheet_rows_to_records
from transform_to_sheet import records_to_sheet_rows


def _strip_header(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    first = rows[0]
    if first and first[0] == "원문":
        return rows[1:]
    return rows


def _preview_text(s: str, limit: int = 80) -> str:
    one_line = s.replace("\n", " ").strip()
    if len(one_line) <= limit:
        return one_line
    return one_line[:limit] + "..."


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append reviewed A_AI_정리 rows into B_누적_raw."
    )
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="Required safety flag. Run only after manual review of A_AI_정리 is completed.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.confirm_reviewed:
        print("STOP: approve blocked (missing --confirm-reviewed)")
        print("Review A_AI_정리 first, then run:")
        print("  python3 src/run_approve_to_b.py --confirm-reviewed")
        return 1

    project_root = Path(__file__).resolve().parent.parent
    env = load_env_file(str(project_root / ".env"))

    spreadsheet_id = env.get("SPREADSHEET_ID")
    credentials_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not spreadsheet_id:
        print("ERROR: SPREADSHEET_ID is missing in .env")
        return 1
    if not credentials_path:
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS is missing in .env")
        return 1

    config = SheetConfig()
    service = build_sheets_service(credentials_path)
    ensure_sheet_exists(service, spreadsheet_id, config.sheet_a_name)
    ensure_sheet_exists(service, spreadsheet_id, config.sheet_b_name)

    a_rows = _strip_header(read_rows(service, spreadsheet_id, config.sheet_a_name))
    b_rows = _strip_header(read_rows(service, spreadsheet_id, config.sheet_b_name))

    a_records = sheet_rows_to_records(a_rows)
    b_records = sheet_rows_to_records(b_rows)

    existing_keys = {dedupe_key(r.url, r.original_text) for r in b_records}
    to_append = []
    skipped_duplicates = 0
    skipped_main_dash = 0
    duplicate_examples: list[tuple[str, str]] = []
    for r in a_records:
        if r.main_category.strip() == "-":
            skipped_main_dash += 1
            continue

        k = dedupe_key(r.url, r.original_text)
        if k in existing_keys:
            skipped_duplicates += 1
            duplicate_examples.append((r.original_text, r.url))
            continue
        to_append.append(r)
        existing_keys.add(k)

    if to_append:
        append_rows(
            service=service,
            spreadsheet_id=spreadsheet_id,
            sheet_name=config.sheet_b_name,
            rows=records_to_sheet_rows(to_append),
        )

    print("SUCCESS: approve -> B_누적_raw append complete")
    print(f"approved_count={len(to_append)}")
    print(f"skipped_duplicates={skipped_duplicates}")
    print(f"skipped_main_category_dash={skipped_main_dash}")
    if duplicate_examples:
        print("duplicate_examples:")
        for i, (original_text, url) in enumerate(duplicate_examples[:20], 1):
            print(f"  {i}. 원문: {_preview_text(original_text)}")
            print(f"     URL: {url}")
        if len(duplicate_examples) > 20:
            print(f"  ... and {len(duplicate_examples) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
