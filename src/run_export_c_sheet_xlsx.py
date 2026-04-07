from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sys

import xlsxwriter
from xlsxwriter.utility import xl_range, xl_rowcol_to_cell

from env_loader import load_env_file
from sheets_client import build_sheets_service, get_sheet_titles, read_rows


NEGATIVE_HEX = "#0000AE"
POSITIVE_HEX = "#1626D1"
NEUTRAL_HEX = "#4E67C8"
CHART_STYLE_ID = 2


def _normalize_rows(rows: list[list[str]]) -> list[list]:
    normalized: list[list] = []
    for row in rows:
        out_row: list = []
        for v in row:
            if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
                out_row.append(int(v.strip()))
            elif isinstance(v, str) and re.fullmatch(r"-?\d+\.\d+", v.strip()):
                out_row.append(float(v.strip()))
            else:
                out_row.append(v)
        normalized.append(out_row)
    return normalized


def _write_rows(ws, rows: list[list]) -> None:
    for r_idx, row in enumerate(rows):
        for c_idx, value in enumerate(row):
            ws.write(r_idx, c_idx, value)


def _first_col(rows: list[list], idx: int) -> str:
    if idx < 0 or idx >= len(rows):
        return ""
    row = rows[idx]
    if not row:
        return ""
    return str(row[0]).strip() if row[0] is not None else ""


def _find_row_index(rows: list[list], label: str) -> int:
    for i, row in enumerate(rows):
        if row and str(row[0]).strip() == label:
            return i
    return -1


def _find_layout_from_rows(rows: list[list]) -> dict:
    layout: dict[str, int] = {}

    pie_title = _find_row_index(rows, "Sentimental Analysis (원형)")
    if pie_title < 0:
        raise ValueError("could not find pie title row in C sheet")
    layout["pie_data_start_row"] = pie_title + 1
    layout["pie_data_end_row_exclusive"] = pie_title + 4

    bar_title = _find_row_index(rows, "Sentimental Analysis (막대 그래프)")
    if bar_title < 0:
        raise ValueError("could not find bar title row in C sheet")

    bar_header = bar_title + 1
    layout["bar_header_row"] = bar_header
    layout["bar_data_start_row"] = bar_header + 1
    i = layout["bar_data_start_row"]
    while i < len(rows):
        c0 = _first_col(rows, i)
        if c0 in ("", "합계"):
            break
        i += 1
    layout["bar_data_end_row_exclusive"] = i

    label_header = _find_row_index(rows, "차트 표시용")
    if label_header < 0:
        raise ValueError("could not find '차트 표시용' row in C sheet")
    layout["bar_label_header_row"] = label_header
    layout["bar_label_start_row"] = label_header + 1
    j = layout["bar_label_start_row"]
    while j < len(rows):
        c0 = _first_col(rows, j)
        if c0 == "":
            break
        j += 1
    layout["bar_label_end_row_exclusive"] = j

    trend_title = _find_row_index(rows, "Posting Volume Trend")
    if trend_title < 0:
        raise ValueError("could not find trend title row in C sheet")
    layout["trend_data_start_row"] = trend_title + 1
    k = layout["trend_data_start_row"]
    while k < len(rows):
        c0 = _first_col(rows, k)
        if c0 == "":
            break
        k += 1
    layout["trend_data_end_row_exclusive"] = k
    return layout


def _abs_cell_formula(sheet_name: str, row0: int, col0: int) -> str:
    cell = xl_rowcol_to_cell(row0, col0, row_abs=True, col_abs=True)
    return f"='{sheet_name}'!{cell}"


def _add_pie_chart(workbook, worksheet, sheet_name: str, layout: dict) -> None:
    start = layout["pie_data_start_row"]
    end = layout["pie_data_end_row_exclusive"] - 1

    chart = workbook.add_chart({"type": "pie"})
    chart.set_style(CHART_STYLE_ID)
    chart.add_series(
        {
            "categories": f"='{sheet_name}'!{xl_range(start, 0, end, 0)}",
            "values": f"='{sheet_name}'!{xl_range(start, 1, end, 1)}",
            "points": [
                {"fill": {"color": NEGATIVE_HEX}},
                {"fill": {"color": POSITIVE_HEX}},
                {"fill": {"color": NEUTRAL_HEX}},
            ],
            "data_labels": {
                "category": True,
                "percentage": True,
                "leader_lines": True,
                "position": "center",
                "separator": "\n",
            },
        }
    )
    chart.set_title({"none": True})
    chart.set_legend({"none": True})
    worksheet.insert_chart("H2", chart, {"x_scale": 1.35, "y_scale": 1.35})


def _add_stacked_bar_chart(workbook, worksheet, sheet_name: str, layout: dict) -> None:
    header = layout["bar_header_row"]
    data_start = layout["bar_data_start_row"]
    data_end = layout["bar_data_end_row_exclusive"] - 1
    label_start = layout["bar_label_start_row"]
    label_end = layout["bar_label_end_row_exclusive"] - 1

    chart = workbook.add_chart({"type": "column", "subtype": "stacked"})
    chart.set_style(CHART_STYLE_ID)

    series_defs = [
        ("Negative", 1, NEGATIVE_HEX),
        ("Positive", 2, POSITIVE_HEX),
        ("Neutral", 3, NEUTRAL_HEX),
    ]

    for _, value_col, color in series_defs:
        custom_labels = [{"value": _abs_cell_formula(sheet_name, r, value_col)} for r in range(label_start, label_end + 1)]
        chart.add_series(
            {
                "name": f"='{sheet_name}'!{xl_rowcol_to_cell(header, value_col)}",
                "categories": f"='{sheet_name}'!{xl_range(data_start, 0, data_end, 0)}",
                "values": f"='{sheet_name}'!{xl_range(data_start, value_col, data_end, value_col)}",
                "fill": {"color": color},
                "border": {"none": True},
                "data_labels": {
                    "value": False,
                    "category": False,
                    "series_name": False,
                    "custom": custom_labels,
                    "leader_lines": True,
                    "position": "center",
                },
            }
        )

    chart.set_title({"none": True})
    chart.set_legend({"position": "top"})
    chart.set_x_axis({"label_position": "low"})
    chart.set_y_axis({"visible": False})
    worksheet.insert_chart("H20", chart, {"x_scale": 1.65, "y_scale": 1.45})


def _add_trend_chart(workbook, worksheet, sheet_name: str, layout: dict) -> None:
    data_start = layout["trend_data_start_row"]
    data_end = layout["trend_data_end_row_exclusive"] - 1

    chart = workbook.add_chart({"type": "column"})
    chart.set_style(CHART_STYLE_ID)
    chart.add_series(
        {
            "categories": f"='{sheet_name}'!{xl_range(data_start, 0, data_end, 0)}",
            "values": f"='{sheet_name}'!{xl_range(data_start, 1, data_end, 1)}",
            "fill": {"color": NEGATIVE_HEX},
            "border": {"none": True},
            "data_labels": {"value": True, "position": "outside_end"},
        }
    )
    chart.set_title({"none": True})
    chart.set_legend({"none": True})
    chart.set_x_axis({"label_position": "low"})
    chart.set_y_axis({"visible": False})
    worksheet.insert_chart("H52", chart, {"x_scale": 1.4, "y_scale": 1.3})


def _pick_source_sheet(titles: list[str], user_arg: str | None) -> str:
    if user_arg:
        if user_arg in titles:
            return user_arg
        candidate = f"C_pivot_{user_arg}"
        if candidate in titles:
            return candidate
        raise ValueError(f"sheet not found: {user_arg}")

    dated: list[tuple[str, str]] = []
    for t in titles:
        m = re.fullmatch(r"C_pivot_(\d{4}-\d{2}-\d{2})", t)
        if m:
            dated.append((m.group(1), t))
    if not dated:
        raise ValueError("no C_pivot_YYYY-MM-DD sheet found")
    dated.sort(key=lambda x: x[0], reverse=True)
    return dated[0][1]


def main() -> int:
    # Usage:
    # python3 src/run_export_c_sheet_xlsx.py
    # python3 src/run_export_c_sheet_xlsx.py C_pivot_2026-03-31
    # python3 src/run_export_c_sheet_xlsx.py 2026-03-31
    source_arg = sys.argv[1] if len(sys.argv) >= 2 else None

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

    service = build_sheets_service(credentials_path)
    titles = get_sheet_titles(service, spreadsheet_id)
    source_sheet = _pick_source_sheet(titles, source_arg)
    source_rows = read_rows(service, spreadsheet_id, source_sheet)
    rows = _normalize_rows(source_rows)
    layout = _find_layout_from_rows(rows)

    out_dir = project_root / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"pivot_from_c_{source_sheet}_{ts}.xlsx"

    wb = xlsxwriter.Workbook(str(out_path))
    ws = wb.add_worksheet(source_sheet)
    _write_rows(ws, rows)
    _add_pie_chart(wb, ws, source_sheet, layout)
    _add_stacked_bar_chart(wb, ws, source_sheet, layout)
    _add_trend_chart(wb, ws, source_sheet, layout)
    wb.close()

    print("SUCCESS: C sheet based pivot workbook exported")
    print(f"file={out_path}")
    print(f"source_sheet={source_sheet}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

