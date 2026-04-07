"""
Adapter layer: wraps existing src/ pipeline modules with progress callbacks
for use by the FastAPI backend. CLI scripts (main() functions) are unchanged.
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))
PROJECT_ROOT = Path(__file__).resolve().parent.parent

from collectors import available_platforms as _available_platforms
from collectors import get_collector
from collectors.text_formatter import format_all
from collectors.unit_converter import collected_posts_to_units
from config import SheetConfig
from contracts import StructuredRecord
from dedupe import dedupe_key
from env_loader import load_env_file
from llm_output_parser import parse_llm_output
from llm_structurer import DEFAULT_MODEL, load_system_prompt, structure_units
from preparse import split_url_blocks
from sheet_row_mapper import sheet_rows_to_records
from sheets_client import (
    append_rows,
    build_sheets_service,
    ensure_sheet_exists,
    get_sheet_titles,
    overwrite_sheet_with_rows,
    read_rows,
)
from sns_detector import detect_sns_from_url
from transform_to_sheet import records_to_sheet_rows
from validator import validate_record

# Import private helpers from the parse script (avoids duplication)
from run_parse_to_a import (
    _build_units,
    _chunked,
    _fix_untranslated_records,
    _normalize_record_for_sheet,
    _recover_records_by_single_unit_calls,
)


def _load_env() -> dict:
    import os
    # os.environ을 기본값으로 쓰고, .env 파일이 있으면 그 값으로 덮어씀.
    # Render처럼 .env 파일이 없는 환경에서는 os.environ만 사용.
    env = dict(os.environ)
    env.update(load_env_file(str(PROJECT_ROOT / ".env")))
    return env


# ── Public helpers ────────────────────────────────────────────────────────────

def get_available_platforms() -> list[str]:
    return _available_platforms()


def get_spreadsheet_url() -> str:
    env = _load_env()
    sid = env.get("SPREADSHEET_ID", "")
    return f"https://docs.google.com/spreadsheets/d/{sid}" if sid else ""


def get_sheet_a_stats() -> dict:
    env = _load_env()
    sid = env.get("SPREADSHEET_ID")
    creds = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not sid or not creds:
        raise RuntimeError("Missing SPREADSHEET_ID or GOOGLE_APPLICATION_CREDENTIALS in .env")

    config = SheetConfig()
    service = build_sheets_service(creds)
    rows = read_rows(service, sid, config.sheet_a_name)
    if rows and rows[0] and rows[0][0] == "원문":
        rows = rows[1:]

    records = sheet_rows_to_records(rows)

    sentiment_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    sns_counts: dict[str, int] = {}

    for r in records:
        sentiment_counts[r.sentiment] = sentiment_counts.get(r.sentiment, 0) + 1
        category_counts[r.main_category] = category_counts.get(r.main_category, 0) + 1
        sns_counts[r.sns] = sns_counts.get(r.sns, 0) + 1

    preview = [
        {
            "original": r.original_text[:80],
            "ko": r.ko_translation[:80],
            "date": r.date,
            "main_category": r.main_category,
            "sub_category": r.sub_category,
            "sentiment": r.sentiment,
            "sns": r.sns,
        }
        for r in records[:20]
    ]

    return {
        "total": len(records),
        "sentiment": sentiment_counts,
        "category": category_counts,
        "sns": sns_counts,
        "preview": preview,
        "sheet_url": get_spreadsheet_url(),
    }


def list_pivot_sheets() -> list[str]:
    env = _load_env()
    sid = env.get("SPREADSHEET_ID")
    creds = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not sid or not creds:
        return []
    service = build_sheets_service(creds)
    titles = get_sheet_titles(service, sid)
    pivot = [t for t in titles if t.startswith("C_pivot_")]
    pivot.sort(reverse=True)
    return pivot


# ── Collect + Parse ───────────────────────────────────────────────────────────

def collect_and_parse(
    *,
    keywords: list[str],
    date_from: str,
    date_to: str,
    platforms: list[str],
    ptt_boards: list[str],
    max_posts: int,
    paste_text: str,
    on_event: Callable[[dict], None],
    cancelled: threading.Event,
) -> None:
    """Collect from platforms, combine with paste text, parse via LLM → Sheet A."""
    env = _load_env()
    api_key = env.get("ANTHROPIC_API_KEY")
    spreadsheet_id = env.get("SPREADSHEET_ID")
    credentials_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    model = env.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    max_units_per_call = int(env.get("MAX_UNITS_PER_CALL", "40"))
    request_timeout_sec = float(env.get("LLM_REQUEST_TIMEOUT_SEC", "120"))
    parse_concurrency = max(1, int(env.get("PARSE_CONCURRENCY", "3")))

    if not api_key or not spreadsheet_id or not credentials_path:
        raise RuntimeError(
            "Missing required .env variables: ANTHROPIC_API_KEY, SPREADSHEET_ID, GOOGLE_APPLICATION_CREDENTIALS"
        )

    # ── Step 1: Collect ──────────────────────────────────────────────────────
    all_posts = []
    for pname in platforms:
        if cancelled.is_set():
            raise RuntimeError("cancelled")

        on_event({"type": "collect_start", "platform": pname})
        try:
            collector = get_collector(pname)
        except ValueError as e:
            on_event({"type": "collect_error", "platform": pname, "error": str(e)})
            continue

        extra: dict = {}
        if pname.lower() == "ptt":
            extra["boards"] = ptt_boards

        try:
            print(f"  [pipeline] {pname} collect() 시작", flush=True)
            posts = collector.collect(
                keywords=keywords,
                date_from=date_from,
                date_to=date_to,
                max_posts=max_posts,
                **extra,
            )
            print(f"  [pipeline] {pname} collect() 완료: {len(posts)}건", flush=True)
        except Exception as e:
            print(f"  [pipeline] {pname} collect() 예외: {e}", flush=True)
            on_event({"type": "collect_error", "platform": pname, "error": str(e)})
            continue

        all_posts.extend(posts)
        n_comments = sum(len(p.comments) for p in posts)
        on_event({
            "type": "collect_done",
            "platform": pname,
            "posts": len(posts),
            "comments": n_comments,
        })

    if not all_posts and not paste_text.strip():
        raise RuntimeError("No content to parse (nothing collected and paste text is empty)")

    # ── Step 2+3: Build parse jobs from two independent paths ────────────────
    run_at_iso = datetime.now().astimezone().isoformat()
    system_prompt = load_system_prompt(
        str(PROJECT_ROOT / "prompts" / "sns_structuring_system_prompt.md")
    )

    parse_jobs: list[dict] = []
    seq = 0
    total_units = 0
    block_counter = 0

    # Path A: Direct conversion for collected posts (no text intermediary)
    if all_posts:
        post_groups = collected_posts_to_units(all_posts)
        for url, sns, units in post_groups:
            if cancelled.is_set():
                raise RuntimeError("cancelled")
            if not units:
                continue
            block_counter += 1
            total_units += len(units)
            chunks = _chunked(units, max_units_per_call)
            for chunk_idx, chunk in enumerate(chunks, 1):
                parse_jobs.append({
                    "seq": seq,
                    "block_idx": block_counter,
                    "block_total": 0,  # filled after both paths
                    "chunk_idx": chunk_idx,
                    "chunk_total": len(chunks),
                    "url": url,
                    "sns": sns,
                    "units": chunk,
                })
                seq += 1

    # Path B: Text-based parsing for paste_text (manual web UI input)
    if paste_text.strip():
        blocks = split_url_blocks(paste_text.strip())
        for url, block_body in blocks:
            if cancelled.is_set():
                raise RuntimeError("cancelled")
            sns = detect_sns_from_url(url)
            if not sns:
                on_event({"type": "parse_warn", "msg": f"Skipped unsupported URL: {url}"})
                continue
            units = _build_units(sns, block_body)
            if not units:
                continue
            block_counter += 1
            total_units += len(units)
            chunks = _chunked(units, max_units_per_call)
            for chunk_idx, chunk in enumerate(chunks, 1):
                parse_jobs.append({
                    "seq": seq,
                    "block_idx": block_counter,
                    "block_total": 0,
                    "chunk_idx": chunk_idx,
                    "chunk_total": len(chunks),
                    "url": url,
                    "sns": sns,
                    "units": chunk,
                })
                seq += 1

    # Fill block_total now that both paths are done
    for job in parse_jobs:
        job["block_total"] = block_counter

    if not parse_jobs:
        raise RuntimeError("No parseable content found from collected posts or paste text.")

    on_event({
        "type": "parse_start",
        "total_blocks": block_counter,
        "total_units": total_units,
        "total_jobs": len(parse_jobs),
    })

    # ── Step 4: LLM parsing (parallel) ──────────────────────────────────────
    results_by_seq: dict[int, list[StructuredRecord]] = {}

    with ThreadPoolExecutor(max_workers=parse_concurrency) as executor:
        future_map = {}
        for job in parse_jobs:
            if cancelled.is_set():
                raise RuntimeError("cancelled")
            future = executor.submit(
                structure_units,
                api_key=api_key,
                system_prompt=system_prompt,
                model=model,
                run_at_iso=run_at_iso,
                url=job["url"],
                sns=job["sns"],
                units=job["units"],
                request_timeout_sec=request_timeout_sec,
            )
            future_map[future] = job

        completed = 0
        for future in as_completed(future_map):
            if cancelled.is_set():
                raise RuntimeError("cancelled")
            job = future_map[future]
            completed += 1
            try:
                try:
                    llm_json = future.result()
                    records = parse_llm_output(llm_json)
                except Exception:
                    records = _recover_records_by_single_unit_calls(
                        api_key=api_key,
                        system_prompt=system_prompt,
                        model=model,
                        run_at_iso=run_at_iso,
                        url=job["url"],
                        sns=job["sns"],
                        units=job["units"],
                        request_timeout_sec=request_timeout_sec,
                    )

                if len(records) != len(job["units"]):
                    records = _recover_records_by_single_unit_calls(
                        api_key=api_key,
                        system_prompt=system_prompt,
                        model=model,
                        run_at_iso=run_at_iso,
                        url=job["url"],
                        sns=job["sns"],
                        units=job["units"],
                        request_timeout_sec=request_timeout_sec,
                    )
            except Exception:
                # fallback도 실패하면 해당 청크는 건너뜀
                records = []

            results_by_seq[job["seq"]] = records
            records_done = sum(len(v) for v in results_by_seq.values())
            on_event({
                "type": "parse_chunk_done",
                "block_idx": job["block_idx"],
                "block_total": job["block_total"],
                "chunk_idx": job["chunk_idx"],
                "chunk_total": job["chunk_total"],
                "completed_jobs": completed,
                "total_jobs": len(parse_jobs),
                "records_done": records_done,
            })

    # ── Step 5: Post-processing ──────────────────────────────────────────────
    all_records: list[StructuredRecord] = []
    for job in sorted(parse_jobs, key=lambda x: x["seq"]):
        all_records.extend(results_by_seq[job["seq"]])

    all_records = _fix_untranslated_records(
        all_records,
        api_key=api_key,
        model=model,
        request_timeout_sec=request_timeout_sec,
    )

    typed_records: list[StructuredRecord] = []
    all_errors = []
    for idx, r in enumerate(all_records):
        r = _normalize_record_for_sheet(r)
        errs = validate_record(idx, r)
        if errs:
            all_errors.extend(errs)
        typed_records.append(r)

    if all_errors:
        raise RuntimeError(
            f"Validation failed ({len(all_errors)} errors). First: {all_errors[0].reason}"
        )

    # ── Step 6: Write to Sheet A ─────────────────────────────────────────────
    config = SheetConfig()
    service = build_sheets_service(credentials_path)
    ensure_sheet_exists(service, spreadsheet_id, config.sheet_a_name)
    ensure_sheet_exists(service, spreadsheet_id, config.sheet_b_name)

    rows = records_to_sheet_rows(typed_records)
    overwrite_sheet_with_rows(
        service=service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=config.sheet_a_name,
        headers=list(config.output_headers),
        rows=rows,
    )
    on_event({
        "type": "parse_done",
        "total_records": len(typed_records),
        "total_blocks": block_counter,
    })


# ── Approve ───────────────────────────────────────────────────────────────────

def run_approve() -> dict:
    """Read Sheet A (user-reviewed), deduplicate, append to Sheet B."""
    env = _load_env()
    sid = env.get("SPREADSHEET_ID")
    creds = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not sid or not creds:
        raise RuntimeError("Missing SPREADSHEET_ID or GOOGLE_APPLICATION_CREDENTIALS in .env")

    config = SheetConfig()
    service = build_sheets_service(creds)
    ensure_sheet_exists(service, sid, config.sheet_a_name)
    ensure_sheet_exists(service, sid, config.sheet_b_name)

    def _strip(rows: list[list[str]]) -> list[list[str]]:
        if rows and rows[0] and rows[0][0] == "원문":
            return rows[1:]
        return rows

    a_rows = _strip(read_rows(service, sid, config.sheet_a_name))
    b_rows = _strip(read_rows(service, sid, config.sheet_b_name))
    a_records = sheet_rows_to_records(a_rows)
    b_records = sheet_rows_to_records(b_rows)

    existing_keys = {dedupe_key(r.url, r.original_text) for r in b_records}
    to_append: list[StructuredRecord] = []
    skipped_dup = 0
    skipped_dash = 0

    for r in a_records:
        if r.main_category.strip() == "-":
            skipped_dash += 1
            continue
        k = dedupe_key(r.url, r.original_text)
        if k in existing_keys:
            skipped_dup += 1
            continue
        to_append.append(r)
        existing_keys.add(k)

    if to_append:
        append_rows(
            service=service,
            spreadsheet_id=sid,
            sheet_name=config.sheet_b_name,
            rows=records_to_sheet_rows(to_append),
        )

    return {
        "approved": len(to_append),
        "skipped_duplicates": skipped_dup,
        "skipped_dash": skipped_dash,
    }
