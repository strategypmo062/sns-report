from __future__ import annotations

from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from env_loader import load_env_file


SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
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

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=SCOPES,
    )
    session = AuthorizedSession(credentials)

    export_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=xlsx"
    resp = session.get(export_url, timeout=60)
    if resp.status_code != 200:
        print(f"ERROR: export failed: status={resp.status_code}")
        print(resp.text[:500])
        return 1

    out_dir = project_root / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"google_export_{ts}.xlsx"
    out_path.write_bytes(resp.content)

    print("SUCCESS: exported xlsx from Google Sheets")
    print(f"file={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

