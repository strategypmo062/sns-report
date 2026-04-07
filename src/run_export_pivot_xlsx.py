from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import xlsxwriter
from xlsxwriter.utility import xl_range, xl_rowcol_to_cell

from config import SheetConfig
from env_loader import load_env_file
from run_generate_pivot import _build_rows_for_sheet, _parse_date_yyyy_mm_dd
from sheet_row_mapper import sheet_rows_to_records
from sheets_client import build_sheets_service, ensure_sheet_exists, read_rows

NEGATIVE_HEX = "#0000AE"
POSITIVE_HEX = "#1626D1"
NEUTRAL_HEX = "#4E67C8"
CHART_STYLE_ID = 2


def _strip_header(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    first = rows[0]
    if first and first[0] == "원문":
        return rows[1:]
    return rows


def _write_rows(ws, rows: list[list]) -> None:
    for r_idx, row in enumerate(rows):
        for c_idx, value in enumerate(row):
            ws.write(r_idx, c_idx, value)


def _abs_cell_formula(sheet_name: str, row0: int, col0: int) -> str:
    # xlsxwriter row/col are 0-based
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
                # Reference format: category + percentage on separate lines.
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

    for idx, (name, value_col, color) in enumerate(series_defs):
        custom_labels = []
        for r in range(label_start, label_end + 1):
            custom_labels.append({"value": _abs_cell_formula(sheet_name, r, value_col)})

        chart.add_series(
            {
                "name": f"='{sheet_name}'!{xl_rowcol_to_cell(header, value_col)}",
                "categories": f"='{sheet_name}'!{xl_range(data_start, 0, data_end, 0)}",
                "values": f"='{sheet_name}'!{xl_range(data_start, value_col, data_end, value_col)}",
                "fill": {"color": color},
                "border": {"none": True},
                # Value from cells (chart 표시용) + leader lines
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
            "data_labels": {
                "value": True,
                "position": "outside_end",
            },
        }
    )
    chart.set_title({"none": True})
    chart.set_legend({"none": True})
    chart.set_x_axis({"label_position": "low"})
    chart.set_y_axis({"visible": False})
    worksheet.insert_chart("H52", chart, {"x_scale": 1.4, "y_scale": 1.3})


def main() -> int:
    # Usage: python3 src/run_export_pivot_xlsx.py 2026-03-31 2026-03-17
    analysis_date_str = sys.argv[1] if len(sys.argv) >= 2 else "2026-03-31"
    trend_start_str = sys.argv[2] if len(sys.argv) >= 3 else "2026-03-17"

    analysis_date = _parse_date_yyyy_mm_dd(analysis_date_str)
    trend_start_date = _parse_date_yyyy_mm_dd(trend_start_str)
    if not analysis_date:
        print(f"ERROR: invalid analysis date: {analysis_date_str}")
        return 1
    if not trend_start_date:
        print(f"ERROR: invalid trend start date: {trend_start_str}")
        return 1
    if trend_start_date > analysis_date:
        print("ERROR: trend start date cannot be after analysis date")
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

    service = build_sheets_service(credentials_path)
    config = SheetConfig()
    ensure_sheet_exists(service, spreadsheet_id, config.sheet_b_name)
    b_rows = _strip_header(read_rows(service, spreadsheet_id, config.sheet_b_name))
    records = sheet_rows_to_records(b_rows)

    rows, layout = _build_rows_for_sheet(records, analysis_date, trend_start_date)

    out_dir = project_root / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"pivot_{analysis_date.isoformat()}_{ts}.xlsx"

    wb = xlsxwriter.Workbook(str(out_path))
    sheet_name = f"C_pivot_{analysis_date.isoformat()}"
    ws = wb.add_worksheet(sheet_name)
    _write_rows(ws, rows)
    _add_pie_chart(wb, ws, sheet_name, layout)
    _add_stacked_bar_chart(wb, ws, sheet_name, layout)
    _add_trend_chart(wb, ws, sheet_name, layout)
    wb.close()

    print("SUCCESS: pivot workbook exported")
    print(f"file={out_path}")
    print(f"analysis_date={analysis_date.isoformat()}")
    print(f"trend_start={trend_start_date.isoformat()}")
    print(f"source_rows={len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
