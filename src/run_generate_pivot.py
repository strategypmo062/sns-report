from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

from config import SheetConfig
from env_loader import load_env_file
from sheet_row_mapper import sheet_rows_to_records
from sheets_client import (
    add_charts,
    build_sheets_service,
    clear_charts_in_sheet,
    ensure_sheet_exists,
    get_sheet_id,
    overwrite_sheet_with_rows,
    read_rows,
)


TARGET_SENTIMENTS_KO = ("부정", "긍정", "중립")
TARGET_SENTIMENTS_EN = ("Negative", "Positive", "Neutral")
SENTIMENT_KO_TO_EN = {"부정": "Negative", "긍정": "Positive", "중립": "Neutral"}

# User-requested palette
NEGATIVE_HEX = "0000AE"
POSITIVE_HEX = "1626D1"
NEUTRAL_HEX = "4E67C8"


def _strip_header(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    first = rows[0]
    if first and first[0] == "원문":
        return rows[1:]
    return rows


def _parse_date_yyyy_mm_dd(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "0%"
    return f"{round((n / d) * 100):.0f}%"


def _fmt_month_day_kor(d: date) -> str:
    return f"{d.month}월 {d.day}일"


def _fmt_m_d(d: date) -> str:
    return f"{d.month}/{d.day}"


def _hex_to_rgb_obj(hex_color: str) -> dict:
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"invalid hex color: {hex_color}")
    r = int(h[0:2], 16) / 255
    g = int(h[2:4], 16) / 255
    b = int(h[4:6], 16) / 255
    return {"red": r, "green": g, "blue": b}


def _build_rows_for_sheet(
    records,
    analysis_date: date,
    trend_start_date: date,
) -> tuple[list[list[str]], dict]:
    # 1) 당일 감성 집계
    day_records = [
        r
        for r in records
        if r.date == analysis_date.isoformat() and r.sentiment in TARGET_SENTIMENTS_KO
    ]
    day_sent_counts_ko = defaultdict(int)
    for r in day_records:
        day_sent_counts_ko[r.sentiment] += 1
    day_sent_counts_en = {
        SENTIMENT_KO_TO_EN[k]: day_sent_counts_ko[k] for k in TARGET_SENTIMENTS_KO
    }
    day_total = sum(day_sent_counts_en.values())

    # 2) 당일 카테고리 x 감성
    cat_sent_counts = defaultdict(lambda: defaultdict(int))
    for r in day_records:
        cat = r.main_category
        sent_en = SENTIMENT_KO_TO_EN[r.sentiment]
        cat_sent_counts[cat][sent_en] += 1

    cat_rows = []
    for cat, m in cat_sent_counts.items():
        neg = m["Negative"]
        pos = m["Positive"]
        neu = m["Neutral"]
        total = neg + pos + neu
        cat_rows.append((cat, neg, pos, neu, total))
    cat_rows.sort(key=lambda x: (-x[4], x[0]))

    # 3) 전체기간 트렌드
    trend_counts = defaultdict(int)
    end = analysis_date
    d = trend_start_date
    while d <= end:
        trend_counts[d.isoformat()] = 0
        d += timedelta(days=1)

    for r in records:
        rd = _parse_date_yyyy_mm_dd(r.date)
        if not rd:
            continue
        if trend_start_date <= rd <= analysis_date and r.sentiment in TARGET_SENTIMENTS_KO:
            trend_counts[rd.isoformat()] += 1

    # Compose output rows
    rows: list[list[str]] = []
    layout: dict[str, int] = {}

    # Block 1: Sentimental Analysis (원형)
    rows.append(["Sentimental Analysis (원형)", "", "", _fmt_month_day_kor(analysis_date)])
    layout["pie_title_row"] = 0
    rows.append(["Negative", day_sent_counts_en["Negative"]])
    rows.append(["Positive", day_sent_counts_en["Positive"]])
    rows.append(["Neutral", day_sent_counts_en["Neutral"]])
    layout["pie_data_start_row"] = 1
    layout["pie_data_end_row_exclusive"] = 4
    rows.append(["합계", day_total])
    rows.append([])
    rows.append([])

    # Block 2: Sentimental Analysis (막대 그래프)
    rows.append(
        [
            "Sentimental Analysis (막대 그래프)",
            "",
            "",
            f"{_fmt_month_day_kor(analysis_date)} 언급 많은 순서대로",
        ]
    )
    layout["bar_title_row"] = len(rows) - 1
    rows.append(["", "Negative", "Positive", "Neutral", "합계"])
    layout["bar_header_row"] = len(rows) - 1
    layout["bar_data_start_row"] = len(rows)
    for cat, neg, pos, neu, total in cat_rows:
        rows.append([cat, neg, pos, neu, total])
    layout["bar_data_end_row_exclusive"] = len(rows)
    rows.append(
        [
            "합계",
            sum(x[1] for x in cat_rows),
            sum(x[2] for x in cat_rows),
            sum(x[3] for x in cat_rows),
            sum(x[4] for x in cat_rows),
        ]
    )
    rows.append([])

    rows.append(["비율 계산", "Negative", "Positive", "Neutral"])
    for cat, neg, pos, neu, total in cat_rows:
        rows.append([cat, _pct(neg, total), _pct(pos, total), _pct(neu, total)])
    rows.append([])

    rows.append(["차트 표시용", "Negative", "Positive", "Neutral"])
    layout["bar_label_header_row"] = len(rows) - 1
    layout["bar_label_start_row"] = len(rows)
    for cat, neg, pos, neu, total in cat_rows:
        rows.append(
            [
                cat,
                f"{neg} ({_pct(neg, total)})",
                f"{pos} ({_pct(pos, total)})",
                f"{neu} ({_pct(neu, total)})",
            ]
        )
    layout["bar_label_end_row_exclusive"] = len(rows)
    rows.append([])
    rows.append([])

    # Block 3: Posting Volume Trend
    rows.append(
        [
            "Posting Volume Trend",
            "",
            "",
            f"{_fmt_m_d(trend_start_date)}-{_fmt_m_d(analysis_date)}",
        ]
    )
    layout["trend_title_row"] = len(rows) - 1
    layout["trend_data_start_row"] = len(rows)
    d = trend_start_date
    while d <= analysis_date:
        rows.append([_fmt_m_d(d), trend_counts[d.isoformat()]])
        d += timedelta(days=1)
    layout["trend_data_end_row_exclusive"] = len(rows)

    return rows, layout


def _grid_range(
    sheet_id: int,
    row_start: int,
    row_end_exclusive: int,
    col_start: int,
    col_end_exclusive: int,
) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": row_start,
        "endRowIndex": row_end_exclusive,
        "startColumnIndex": col_start,
        "endColumnIndex": col_end_exclusive,
    }


def _add_chart_request(spec: dict, sheet_id: int, row: int, col: int, width: int, height: int) -> dict:
    return {
        "addChart": {
            "chart": {
                "spec": spec,
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": row,
                            "columnIndex": col,
                        },
                        "widthPixels": width,
                        "heightPixels": height,
                    }
                },
            }
        }
    }


def _build_chart_requests(sheet_id: int, layout: dict) -> list[dict]:
    requests: list[dict] = []

    # 1) Pie chart: Sentimental Analysis (원형)
    pie_spec = {
        "pieChart": {
            "legendPosition": "LABELED_LEGEND",
            "domain": {
                "sourceRange": {
                    "sources": [
                        _grid_range(
                            sheet_id,
                            layout["pie_data_start_row"],
                            layout["pie_data_end_row_exclusive"],
                            0,
                            1,
                        )
                    ]
                }
            },
            "series": {
                "sourceRange": {
                    "sources": [
                        _grid_range(
                            sheet_id,
                            layout["pie_data_start_row"],
                            layout["pie_data_end_row_exclusive"],
                            1,
                            2,
                        )
                    ]
                }
            },
        },
    }
    requests.append(_add_chart_request(pie_spec, sheet_id, row=0, col=6, width=820, height=460))

    # 2) Stacked column: category x sentiment
    bar_spec = {
        "basicChart": {
            "chartType": "COLUMN",
            "legendPosition": "TOP_LEGEND",
            "stackedType": "STACKED",
            "axis": [
                {"position": "BOTTOM_AXIS", "title": ""},
                {"position": "LEFT_AXIS", "title": ""},
            ],
            "domains": [
                {
                    "domain": {
                        "sourceRange": {
                            "sources": [
                                _grid_range(
                                    sheet_id,
                                    layout["bar_header_row"],
                                    layout["bar_data_end_row_exclusive"],
                                    0,
                                    1,
                                )
                            ]
                        }
                    }
                }
            ],
            "series": [
                {
                    "series": {
                        "sourceRange": {
                            "sources": [
                                _grid_range(
                                    sheet_id,
                                    layout["bar_header_row"],
                                    layout["bar_data_end_row_exclusive"],
                                    1,
                                    2,
                                )
                            ]
                        }
                    },
                    "targetAxis": "LEFT_AXIS",
                    "colorStyle": {"rgbColor": _hex_to_rgb_obj(NEGATIVE_HEX)},
                    "dataLabel": {
                        "type": "CUSTOM",
                        "placement": "CENTER",
                        "customLabelData": {
                            "sourceRange": {
                                "sources": [
                                    _grid_range(
                                        sheet_id,
                                        layout["bar_label_start_row"],
                                        layout["bar_label_end_row_exclusive"],
                                        1,
                                        2,
                                    )
                                ]
                            }
                        },
                    },
                },
                {
                    "series": {
                        "sourceRange": {
                            "sources": [
                                _grid_range(
                                    sheet_id,
                                    layout["bar_header_row"],
                                    layout["bar_data_end_row_exclusive"],
                                    2,
                                    3,
                                )
                            ]
                        }
                    },
                    "targetAxis": "LEFT_AXIS",
                    "colorStyle": {"rgbColor": _hex_to_rgb_obj(POSITIVE_HEX)},
                    "dataLabel": {
                        "type": "CUSTOM",
                        "placement": "CENTER",
                        "customLabelData": {
                            "sourceRange": {
                                "sources": [
                                    _grid_range(
                                        sheet_id,
                                        layout["bar_label_start_row"],
                                        layout["bar_label_end_row_exclusive"],
                                        2,
                                        3,
                                    )
                                ]
                            }
                        },
                    },
                },
                {
                    "series": {
                        "sourceRange": {
                            "sources": [
                                _grid_range(
                                    sheet_id,
                                    layout["bar_header_row"],
                                    layout["bar_data_end_row_exclusive"],
                                    3,
                                    4,
                                )
                            ]
                        }
                    },
                    "targetAxis": "LEFT_AXIS",
                    "colorStyle": {"rgbColor": _hex_to_rgb_obj(NEUTRAL_HEX)},
                    "dataLabel": {
                        "type": "CUSTOM",
                        "placement": "CENTER",
                        "customLabelData": {
                            "sourceRange": {
                                "sources": [
                                    _grid_range(
                                        sheet_id,
                                        layout["bar_label_start_row"],
                                        layout["bar_label_end_row_exclusive"],
                                        3,
                                        4,
                                    )
                                ]
                            }
                        },
                    },
                },
            ],
            "headerCount": 1,
        },
    }
    requests.append(_add_chart_request(bar_spec, sheet_id, row=20, col=6, width=1180, height=560))

    # 3) Posting Volume Trend
    trend_spec = {
        "basicChart": {
            "chartType": "COLUMN",
            "legendPosition": "NO_LEGEND",
            "axis": [
                {"position": "BOTTOM_AXIS", "title": ""},
                {
                    "position": "LEFT_AXIS",
                    "title": "",
                    "format": {
                        "foregroundColorStyle": {
                            "rgbColor": {"red": 1, "green": 1, "blue": 1}
                        }
                    },
                },
            ],
            "domains": [
                {
                    "domain": {
                        "sourceRange": {
                            "sources": [
                                _grid_range(
                                    sheet_id,
                                    layout["trend_data_start_row"],
                                    layout["trend_data_end_row_exclusive"],
                                    0,
                                    1,
                                )
                            ]
                        }
                    }
                }
            ],
            "series": [
                {
                    "series": {
                        "sourceRange": {
                            "sources": [
                                _grid_range(
                                    sheet_id,
                                    layout["trend_data_start_row"],
                                    layout["trend_data_end_row_exclusive"],
                                    1,
                                    2,
                                )
                            ]
                        }
                    },
                    "targetAxis": "LEFT_AXIS",
                    "colorStyle": {"rgbColor": _hex_to_rgb_obj(NEGATIVE_HEX)},
                    "dataLabel": {
                        "type": "DATA",
                        "placement": "OUTSIDE_END",
                    },
                }
            ],
            "headerCount": 0,
        },
    }
    requests.append(_add_chart_request(trend_spec, sheet_id, row=52, col=6, width=840, height=380))

    return requests


def main() -> int:
    # Usage: python3 src/run_generate_pivot.py 2026-03-31
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
    create_charts = env.get("PIVOT_CREATE_CHARTS", "0").strip() == "1"
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
    sheet_rows, layout = _build_rows_for_sheet(records, analysis_date, trend_start_date)
    out_sheet = f"C_pivot_{analysis_date.isoformat()}"
    ensure_sheet_exists(service, spreadsheet_id, out_sheet)
    overwrite_sheet_with_rows(
        service=service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=out_sheet,
        headers=[],
        rows=sheet_rows,
    )

    sheet_id = get_sheet_id(service, spreadsheet_id, out_sheet)
    if sheet_id is None:
        print(f"ERROR: failed to resolve sheetId for {out_sheet}")
        return 1

    deleted = clear_charts_in_sheet(service, spreadsheet_id, sheet_id)
    charts_added = 0
    if create_charts:
        chart_requests = _build_chart_requests(sheet_id, layout)
        add_charts(service, spreadsheet_id, chart_requests)
        charts_added = len(chart_requests)

    print("SUCCESS: pivot sheet generated")
    print(f"sheet={out_sheet}")
    print(f"analysis_date={analysis_date.isoformat()}")
    print(f"trend_start={trend_start_date.isoformat()}")
    print(f"source_rows={len(records)}")
    print(f"charts_enabled={create_charts}")
    print(f"charts_deleted={deleted}")
    print(f"charts_added={charts_added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
