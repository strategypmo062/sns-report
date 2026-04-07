from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
from datetime import datetime
from pathlib import Path

from config import SheetConfig
from contracts import StructuredRecord
from env_loader import load_env_file
from llm_output_parser import parse_llm_output
from llm_structurer import DEFAULT_MODEL, _call_with_retry, load_system_prompt, structure_units
from preparse import (
    split_dcard_units,
    split_generic_units,
    split_ptt_units_with_meta,
    split_threads_units,
    split_url_blocks,
)
from sheets_client import build_sheets_service, ensure_sheet_exists, overwrite_sheet_with_rows
from sns_detector import detect_sns_from_url
from transform_to_sheet import records_to_sheet_rows
from validator import validate_record


def _chunked(items: list, size: int) -> list[list]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def _build_units(sns: str, raw_text: str) -> list[dict[str, str]]:
    if sns == "PTT":
        post, post_time_text, comments = split_ptt_units_with_meta(raw_text)
        units: list[dict[str, str]] = []
        if post.strip():
            units.append({"unit_type": "post", "time_text": post_time_text, "text": post})
        units.extend({
            "unit_type": "comment",
            "time_text": c.get("time_text", ""),
            "text": c.get("text", ""),
        } for c in comments if c.get("text", "").strip())
        return units

    if sns == "Threads":
        return split_threads_units(raw_text)

    if sns == "DCard":
        post, comments = split_dcard_units(raw_text)
    else:
        post, comments = split_generic_units(raw_text)

    units: list[dict[str, str]] = []
    if post.strip():
        units.append({"unit_type": "post", "time_text": "", "text": post})
    units.extend(
        {"unit_type": "comment", "time_text": "", "text": c} for c in comments if c.strip()
    )
    return units


def _normalize_record_for_sheet(r: StructuredRecord) -> StructuredRecord:
    main_map = {
        "功能": "기능",
        "功能類": "기능",
        "希望功能": "희망 기능",
        "費用": "비용",
        "成本": "비용",
        "價格": "비용",
        "整體": "전반",
        "总体": "전반",
        "整體評價": "전반",
        "未適用遺憾": "미적용 아쉬움",
        "未 적용 아쉬움": "미적용 아쉬움",
    }
    sentiment_map = {
        "正面": "긍정",
        "負面": "부정",
        "负面": "부정",
        "中性": "중립",
    }
    sub_map = {
        "功能": "기능",
        "費用": "비용",
        "整體": "전반",
        "希望功能": "희망 기능",
        "未適用遺憾": "미적용 아쉬움",
    }

    r.main_category = main_map.get(r.main_category, r.main_category)
    r.sub_category = sub_map.get(r.sub_category, r.sub_category)
    r.sentiment = sentiment_map.get(r.sentiment, r.sentiment)

    # Fallback: if LLM returned an unrecognized value, treat as non-relevant.
    _allowed_main = {"전반", "비용", "기능", "희망 기능", "미적용 아쉬움", "-"}
    _allowed_sentiment = {"긍정", "부정", "중립", "-"}
    if r.main_category not in _allowed_main:
        r.main_category = "-"
    if r.sentiment not in _allowed_sentiment:
        r.sentiment = "-"

    # If any one field marks non-LINE relevance, align all three.
    if "-" in {r.main_category, r.sub_category, r.sentiment}:
        r.main_category = "-"
        r.sub_category = "-"
        r.sentiment = "-"
        return r

    # 기능인데 서브카테고리가 허용 목록 밖이면 비관련으로 처리.
    _allowed_feature_sub = {"Unsend", "Message Backup", "Album", "Font", "Sub Profile", "LINE family 서비스 혜택"}
    if r.main_category == "기능" and r.sub_category not in _allowed_feature_sub:
        r.main_category = "-"
        r.sub_category = "-"
        r.sentiment = "-"
        return r

    # For non-functional main categories, enforce sub == main.
    if r.main_category not in {"기능", "-"}:
        r.sub_category = r.main_category
    return r


_CJK_RE = __import__("re").compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _has_chinese(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _fix_untranslated_records(
    records: list[StructuredRecord],
    *,
    api_key: str,
    model: str,
    request_timeout_sec: float,
    inter_call_delay_sec: float = 1.0,
) -> list[StructuredRecord]:
    """Re-translate records where ko_translation still contains Chinese characters."""
    from anthropic import Anthropic

    targets = [(i, r) for i, r in enumerate(records) if _has_chinese(r.ko_translation)]
    if not targets:
        return records

    print(f"  [번역 후처리] 중국어 남아있는 행 {len(targets)}개 재번역 중...", flush=True)

    client = Anthropic(api_key=api_key, timeout=request_timeout_sec, max_retries=1)
    system = (
        "너는 번역가다. 입력으로 JSON 배열을 받는다. "
        "각 항목의 'text' 값을 한국어로 직역해서 'ko' 필드에 담아 반환한다. "
        "배열 길이와 순서를 유지한다. JSON만 출력한다."
    )

    # Batch in groups of 30
    for batch_start in range(0, len(targets), 30):
        batch = targets[batch_start : batch_start + 30]
        payload = json.dumps(
            [{"id": i, "text": r.ko_translation} for i, r in batch],
            ensure_ascii=False,
        )
        try:
            resp = _call_with_retry(
                client,
                model,
                system,
                payload,
                inter_call_delay_sec=inter_call_delay_sec,
            )
            raw = resp.content[0].text
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.splitlines()[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            translated = json.loads(raw)
            # Support both [{id, ko}, ...] and [{ko}, ...] (index-based fallback)
            for pos, item in enumerate(translated):
                rec_idx = item.get("id", batch[pos][0])
                ko = item.get("ko", item.get("translation", ""))
                if ko:
                    records[rec_idx].ko_translation = ko
        except Exception as e:
            print(f"  [번역 후처리] 오류 (스킵): {e}", flush=True)

    return records


def _recover_records_by_single_unit_calls(
    *,
    api_key: str,
    system_prompt: str,
    model: str,
    run_at_iso: str,
    url: str,
    sns: str,
    units: list[dict[str, str]],
    request_timeout_sec: float,
    inter_call_delay_sec: float = 1.0,
) -> list[StructuredRecord]:
    recovered: list[StructuredRecord] = []
    for idx, unit in enumerate(units, 1):
        llm_json = structure_units(
            api_key=api_key,
            system_prompt=system_prompt,
            model=model,
            run_at_iso=run_at_iso,
            url=url,
            sns=sns,
            units=[unit],
            request_timeout_sec=request_timeout_sec,
            inter_call_delay_sec=inter_call_delay_sec,
        )
        records = parse_llm_output(llm_json)
        if len(records) < 1:
            raise RuntimeError(
                "single-unit recovery failed: "
                f"expected >=1 record, got 0 at unit {idx}/{len(units)}"
            )
        if len(records) > 1:
            print(
                f"  [recovery] unit {idx}/{len(units)}: LLM returned {len(records)} records "
                f"(multi-part post detected, accepting all)",
                flush=True,
            )
        recovered.extend(records)
    return recovered


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 src/run_parse_to_a.py <raw_input_text_file>")
        return 1

    input_file = Path(sys.argv[1])
    if not input_file.exists():
        print(f"ERROR: input file not found: {input_file}")
        return 1

    raw_text = input_file.read_text(encoding="utf-8")
    run_at_iso = datetime.now().astimezone().isoformat()

    project_root = Path(__file__).resolve().parent.parent
    env = load_env_file(str(project_root / ".env"))

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

    blocks = split_url_blocks(raw_text)
    if not blocks:
        print("ERROR: no URL-led blocks found in input text")
        return 1

    system_prompt = load_system_prompt(str(project_root / "prompts" / "sns_structuring_system_prompt.md"))

    jobs: list[dict] = []
    seq = 0
    total_units = 0
    for block_idx, (url, block_body) in enumerate(blocks, 1):
        sns = detect_sns_from_url(url)
        if not sns:
            print(f"ERROR: unsupported SNS domain in URL (block {block_idx}): {url}")
            return 1

        units = _build_units(sns, block_body)
        if not units:
            print(f"WARNING: no units found in block {block_idx}, url={url}")
            continue

        total_units += len(units)
        unit_chunks = _chunked(units, max_units_per_call)
        print(
            f"[block {block_idx}/{len(blocks)}] sns={sns}, url={url}, "
            f"units={len(units)}, chunks={len(unit_chunks)}",
            flush=True,
        )

        for chunk_idx, units_chunk in enumerate(unit_chunks, 1):
            jobs.append(
                {
                    "seq": seq,
                    "block_idx": block_idx,
                    "block_total": len(blocks),
                    "chunk_idx": chunk_idx,
                    "chunk_total": len(unit_chunks),
                    "url": url,
                    "sns": sns,
                    "units": units_chunk,
                }
            )
            seq += 1

    print(
        f"Running LLM parsing in parallel: jobs={len(jobs)}, concurrency={parse_concurrency}",
        flush=True,
    )

    results_by_seq: dict[int, list[StructuredRecord]] = {}
    with ThreadPoolExecutor(max_workers=parse_concurrency) as executor:
        future_map = {}
        for job in jobs:
            print(
                f"  - queue block {job['block_idx']}/{job['block_total']} "
                f"chunk {job['chunk_idx']}/{job['chunk_total']} "
                f"(size={len(job['units'])})",
                flush=True,
            )
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
                    f"ERROR: LLM call failed at block {job['block_idx']}/{job['block_total']}, "
                    f"chunk {job['chunk_idx']}/{job['chunk_total']}: {e}"
                )
                return 1

            try:
                records = parse_llm_output(llm_json)
            except Exception as e:
                print(
                    "ERROR: failed to parse LLM JSON at block "
                    f"{job['block_idx']}, chunk {job['chunk_idx']}: {e}"
                )
                print("Raw LLM output:")
                print(json.dumps(llm_json, ensure_ascii=False, indent=2))
                return 1

            if len(records) != len(job["units"]):
                print(
                    "WARNING: output count mismatch at block "
                    f"{job['block_idx']}, chunk {job['chunk_idx']}. "
                    f"units={len(job['units'])}, records={len(records)}"
                )
                print("  -> retrying this chunk with single-unit calls for strict integrity")
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
                        "ERROR: single-unit recovery failed at block "
                        f"{job['block_idx']}, chunk {job['chunk_idx']}: {e}"
                    )
                    return 1

                if len(records) < len(job["units"]):
                    print(
                        "ERROR: output count mismatch remains after recovery at block "
                        f"{job['block_idx']}, chunk {job['chunk_idx']}. "
                        f"units={len(job['units'])}, records={len(records)}"
                    )
                    return 1

            results_by_seq[job["seq"]] = records
            print(
                f"  - done {completed}/{len(jobs)} "
                f"(block {job['block_idx']} chunk {job['chunk_idx']}, records={len(records)})",
                flush=True,
            )

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
    print("SUCCESS: parse -> A_AI_정리 overwrite complete")
    print(f"model={model}")
    print(f"parse_concurrency={parse_concurrency}")
    print(f"blocks={len(blocks)}")
    print(f"units={total_units}, rows_written={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
