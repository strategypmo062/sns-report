from __future__ import annotations

from google.oauth2 import service_account
from googleapiclient.discovery import build


SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)


def build_sheets_service(credentials_path: str):
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=credentials)


def get_sheet_titles(service, spreadsheet_id: str) -> list[str]:
    res = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = res.get("sheets", [])
    return [s["properties"]["title"] for s in sheets]


def get_sheet_id(service, spreadsheet_id: str, title: str) -> int | None:
    res = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in res.get("sheets", []):
        props = s.get("properties", {})
        if props.get("title") == title:
            return props.get("sheetId")
    return None


def clear_charts_in_sheet(service, spreadsheet_id: str, sheet_id: int) -> int:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(charts(chartId,position))",
    ).execute()

    delete_requests = []
    for sheet in meta.get("sheets", []):
        for chart in sheet.get("charts", []):
            pos = chart.get("position", {})
            overlay = pos.get("overlayPosition", {})
            anchor = overlay.get("anchorCell", {})
            if anchor.get("sheetId") == sheet_id:
                delete_requests.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})

    if delete_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": delete_requests},
        ).execute()
    return len(delete_requests)


def add_charts(service, spreadsheet_id: str, requests: list[dict]) -> None:
    if not requests:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def ensure_sheet_exists(service, spreadsheet_id: str, title: str) -> None:
    titles = set(get_sheet_titles(service, spreadsheet_id))
    if title in titles:
        return

    body = {
        "requests": [
            {
                "addSheet": {
                    "properties": {
                        "title": title,
                    }
                }
            }
        ]
    }
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body=body
    ).execute()


def overwrite_header_row(service, spreadsheet_id: str, sheet_name: str, headers: list[str]) -> None:
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()


def read_rows(service, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:Z",
    ).execute()
    return res.get("values", [])


def overwrite_sheet_with_rows(
    service,
    spreadsheet_id: str,
    sheet_name: str,
    headers: list[str],
    rows: list[list[str]],
) -> None:
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:Z",
        body={},
    ).execute()

    values = ([headers] + rows) if headers else rows
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()


def append_rows(
    service,
    spreadsheet_id: str,
    sheet_name: str,
    rows: list[list[str]],
) -> None:
    if not rows:
        return

    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
