#!/usr/bin/env python3
"""
SNS 수집 + 직접 파싱 CLI (텍스트 변환 우회).

자동 수집기가 이미 갖고 있는 구조화된 데이터(CollectedPost)에서
units를 직접 만들어 LLM → A 시트에 기록한다.
텍스트 포맷/파싱 과정을 건너뛰므로 DCard 빈 줄 버그가 발생하지 않는다.

Usage:
  python3 src/run_collect_and_parse.py \\
    --keywords "LINE Premium,LINE 收費" \\
    --date-from 2026-03-25 --date-to 2026-04-02 \\
    --platforms ptt,dcard,threads \\
    --max-posts 30 \\
    --ptt-boards Gossiping,MobileComm
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

# Allow imports from src/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collectors import get_collector, available_platforms
from collectors.text_formatter import format_all
from collectors.unit_converter import collected_posts_to_units
from config import SheetConfig
from contracts import StructuredRecord
from env_loader import load_env_file
from llm_output_parser import parse_llm_output
from llm_structurer import DEFAULT_MODEL, load_system_prompt, structure_units
from run_parse_to_a import (
    _chunked,
    _fix_untranslated_records,
    _normalize_record_for_sheet,
    _recover_records_by_single_unit_calls,
)
from sheets_client import build_sheets_service, ensure_sheet_exists, overwrite_sheet_with_rows
from transform_to_sheet import records_to_sheet_rows
from validator import validate_record


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SNS 수집 + 직접 파싱 (텍스트 변환 우회)"
    )

    parser.add_argument(
        "--keywords",
        required=True,
        help="검색 키워드 (쉼표 구분). 예: 'LINE Premium,LINE 收費'",
    )
    parser.add_argument(
        "--date-from",
        required=True,
        help="수집 시작일 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--date-to",
        required=True,
        help="수집 종료일 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--platforms",
        default=None,
        help="수집 대상 플랫폼 (쉼표 구분). 미지정 시 설정된 모든 플랫폼",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=30,
        help="플랫폼당 최대 게시글 수 (기본: 30)",
    )
    parser.add_argument(
        "--ptt-boards",
        default="Gossiping,MobileComm,Lifeismoney",
        help="PTT 게시판 (쉼표 구분)",
    )
    parser.add_argument(
        "--save-text",
        default=None,
        help="수집된 텍스트를 파일로도 저장 (디버깅용). 예: data/collected_20260402.txt",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 수집 없이 설정만 확인",
    )

    return parser.parse_args()


def _validate_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        print(f"[ERROR] Invalid date format: {s} (expected YYYY-MM-DD)")
        sys.exit(1)


def main() -> int:
    args = _parse_args()

    project_root = Path(__file__).resolve().parent.parent
    env = load_env_file(str(project_root / ".env"))
    os.environ.update(env)  # Collectors can now access env vars

    api_key = env.get("ANTHROPIC_API_KEY")
    spreadsheet_id = env.get("SPREADSHEET_ID")
    credentials_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    model = env.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    max_units_per_call = int(env.get("MAX_UNITS_PER_CALL", "40"))
    request_timeout_sec = float(env.get("LLM_REQUEST_TIMEOUT_SEC", "120"))
    parse_concurrency = max(1, int(env.get("PARSE_CONCURRENCY", "2")))
    inter_call_delay_sec = max(0.0, float(env.get("LLM_INTER_CALL_DELAY_SEC", "1.0")))

    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is missing in .env")
        return 1
    if not spreadsheet_id:
        print("ERROR: SPREADSHEET_ID is missing in .env")
        return 1
    if not credentials_path:
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS is missing in .env")
        return 1

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    date_from = _validate_date(args.date_from)
    date_to = _validate_date(args.date_to)

    if date_from > date_to:
        print("[ERROR] --date-from must be <= --date-to")
        return 1

    # Determine platforms
    all_platforms = available_platforms()
    if args.platforms:
        requested = [p.strip() for p in args.platforms.split(",")]
        platform_names = []
        for p in requested:
            matched = [ap for ap in all_platforms if ap.lower() == p.lower()]
            if not matched:
                print(f"[WARN] Unknown platform '{p}'. Available: {all_platforms}")
            else:
                platform_names.append(matched[0])
    else:
        platform_names = all_platforms

    print("=" * 60)
    print("SNS 수집 + 직접 파싱")
    print("=" * 60)
    print(f"  키워드:     {keywords}")
    print(f"  기간:       {date_from} ~ {date_to}")
    print(f"  플랫폼:     {platform_names}")
    print(f"  게시글/플랫폼: {args.max_posts}")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] 설정 확인 완료. 실제 수집은 수행하지 않습니다.")
        return 0

    # ── Step 1: Collect ──────────────────────────────────────────────────────
    all_posts = []
    for pname in platform_names:
        print(f"\n{'─' * 40}")
        print(f"[{pname}] 수집 시작...")

        try:
            collector = get_collector(pname)
        except ValueError as e:
            print(f"[{pname}] ERROR: {e}")
            continue

        if not collector.is_configured():
            print(f"[{pname}] SKIP: 인증 정보 미설정. .env 파일을 확인하세요.")
            continue

        extra_kwargs = {}
        if pname.lower() == "ptt":
            extra_kwargs["boards"] = [b.strip() for b in args.ptt_boards.split(",")]

        start_time = time.time()
        try:
            posts = collector.collect(
                keywords=keywords,
                date_from=str(date_from),
                date_to=str(date_to),
                max_posts=args.max_posts,
                **extra_kwargs,
            )
        except Exception as e:
            print(f"[{pname}] ERROR: {e}")
            continue

        elapsed = time.time() - start_time
        n_comments = sum(len(p.comments) for p in posts)
        all_posts.extend(posts)
        print(f"[{pname}] 완료: 게시글 {len(posts)}개, 댓글 {n_comments}개 ({elapsed:.1f}초)")

    if not all_posts:
        print("\n수집된 데이터 없음. 종료합니다.")
        return 1

    # Optional: save text for debugging
    if args.save_text:
        os.makedirs(os.path.dirname(args.save_text) or ".", exist_ok=True)
        raw_text = format_all(all_posts)
        with open(args.save_text, "w", encoding="utf-8") as f:
            f.write(raw_text)
        print(f"\n[디버깅] 텍스트 저장: {args.save_text}")

    # ── Step 2: Direct conversion to units ───────────────────────────────────
    post_groups = collected_posts_to_units(all_posts)
    if not post_groups:
        print("ERROR: no units generated from collected posts")
        return 1

    run_at_iso = datetime.now().astimezone().isoformat()
    system_prompt = load_system_prompt(
        str(project_root / "prompts" / "sns_structuring_system_prompt.md")
    )

    jobs: list[dict] = []
    seq = 0
    total_units = 0

    for group_idx, (url, sns, units) in enumerate(post_groups, 1):
        total_units += len(units)
        unit_chunks = _chunked(units, max_units_per_call)
        print(
            f"[group {group_idx}/{len(post_groups)}] sns={sns}, url={url}, "
            f"units={len(units)}, chunks={len(unit_chunks)}",
            flush=True,
        )

        for chunk_idx, units_chunk in enumerate(unit_chunks, 1):
            jobs.append({
                "seq": seq,
                "group_idx": group_idx,
                "group_total": len(post_groups),
                "chunk_idx": chunk_idx,
                "chunk_total": len(unit_chunks),
                "url": url,
                "sns": sns,
                "units": units_chunk,
            })
            seq += 1

    print(
        f"\nRunning LLM parsing: jobs={len(jobs)}, concurrency={parse_concurrency}",
        flush=True,
    )

    # ── Step 3: Parallel LLM calls ───────────────────────────────────────────
    results_by_seq: dict[int, list[StructuredRecord]] = {}

    with ThreadPoolExecutor(max_workers=parse_concurrency) as executor:
        future_map = {}
        for job in jobs:
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
                inter_call_delay_sec=inter_call_delay_sec,
            )
            future_map[future] = job

        completed = 0
        for future in as_completed(future_map):
            job = future_map[future]
            completed += 1
            try:
                llm_json = future.result()
            except Exception as e:
                print(
                    f"ERROR: LLM call failed at group {job['group_idx']}/{job['group_total']}, "
                    f"chunk {job['chunk_idx']}/{job['chunk_total']}: {e}"
                )
                return 1

            try:
                records = parse_llm_output(llm_json)
            except Exception as e:
                print(
                    f"ERROR: failed to parse LLM JSON at group "
                    f"{job['group_idx']}, chunk {job['chunk_idx']}: {e}"
                )
                print("Raw LLM output:")
                print(json.dumps(llm_json, ensure_ascii=False, indent=2))
                return 1

            if len(records) != len(job["units"]):
                print(
                    f"WARNING: output count mismatch at group "
                    f"{job['group_idx']}, chunk {job['chunk_idx']}. "
                    f"units={len(job['units'])}, records={len(records)}"
                )
                print("  -> retrying with single-unit calls")
                try:
                    records = _recover_records_by_single_unit_calls(
                        api_key=api_key,
                        system_prompt=system_prompt,
                        model=model,
                        run_at_iso=run_at_iso,
                        url=job["url"],
                        sns=job["sns"],
                        units=job["units"],
                        request_timeout_sec=request_timeout_sec,
                        inter_call_delay_sec=inter_call_delay_sec,
                    )
                except Exception as e:
                    print(
                        f"ERROR: single-unit recovery failed at group "
                        f"{job['group_idx']}, chunk {job['chunk_idx']}: {e}"
                    )
                    return 1

                if len(records) < len(job["units"]):
                    print(
                        f"ERROR: output count mismatch remains after recovery at group "
                        f"{job['group_idx']}, chunk {job['chunk_idx']}. "
                        f"units={len(job['units'])}, records={len(records)}"
                    )
                    return 1

            results_by_seq[job["seq"]] = records
            print(
                f"  - done {completed}/{len(jobs)} "
                f"(group {job['group_idx']} chunk {job['chunk_idx']}, records={len(records)})",
                flush=True,
            )

    # ── Step 4: Post-processing ──────────────────────────────────────────────
    all_records: list[StructuredRecord] = []
    for job in sorted(jobs, key=lambda x: x["seq"]):
        all_records.extend(results_by_seq[job["seq"]])

    all_records = _fix_untranslated_records(
        all_records,
        api_key=api_key,
        model=model,
        request_timeout_sec=request_timeout_sec,
        inter_call_delay_sec=inter_call_delay_sec,
    )

    all_errors = []
    typed_records: list[StructuredRecord] = []
    for idx, r in enumerate(all_records):
        r = _normalize_record_for_sheet(r)
        errs = validate_record(idx, r)
        if errs:
            all_errors.extend(errs)
        typed_records.append(r)

    if all_errors:
        print("ERROR: validation failed")
        for e in all_errors[:20]:
            print(f"- row {e.index + 1}: {e.reason}")
        if len(all_errors) > 20:
            print(f"... and {len(all_errors) - 20} more")
        return 1

    # ── Step 5: Write to Sheet A ─────────────────────────────────────────────
    service = build_sheets_service(credentials_path)
    config = SheetConfig()
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

    print(f"\nSUCCESS: collect + parse → A_AI_정리 overwrite complete")
    print(f"  model={model}")
    print(f"  parse_concurrency={parse_concurrency}")
    print(f"  groups={len(post_groups)}")
    print(f"  units={total_units}, rows_written={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
