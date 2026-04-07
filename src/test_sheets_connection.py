from __future__ import annotations

import sys
from pathlib import Path

from googleapiclient.errors import HttpError

from config import SheetConfig
from env_loader import load_env_file
from sheets_client import (
    build_sheets_service,
    ensure_sheet_exists,
    get_sheet_titles,
    overwrite_header_row,
)


def main() -> int:
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

    try:
        ensure_sheet_exists(service, spreadsheet_id, config.sheet_a_name)
        ensure_sheet_exists(service, spreadsheet_id, config.sheet_b_name)

        headers = list(config.output_headers)
        overwrite_header_row(service, spreadsheet_id, config.sheet_a_name, headers)
        overwrite_header_row(service, spreadsheet_id, config.sheet_b_name, headers)

        titles = get_sheet_titles(service, spreadsheet_id)
        print("SUCCESS: Google Sheets connection OK")
        print(f"Sheets found: {', '.join(titles)}")
        print(f"Headers written to: {config.sheet_a_name}, {config.sheet_b_name}")
        return 0
    except HttpError as e:
        body = str(e)
        if "SERVICE_DISABLED" in body or "has not been used in project" in body:
            print("ERROR: Google Sheets API is disabled for this GCP project.")
            print("Open this and click ENABLE:")
            print("https://console.developers.google.com/apis/api/sheets.googleapis.com/overview")
            print("Then wait 1-3 minutes and run this test again.")
            return 1
        if "PERMISSION_DENIED" in body or "The caller does not have permission" in body:
            print("ERROR: Permission denied.")
            print("Share your target spreadsheet with the service account email as Editor.")
            return 1
        print(f"ERROR: Google API call failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
