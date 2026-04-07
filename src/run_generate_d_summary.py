from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import time

from anthropic import Anthropic, RateLimitError

from config import SheetConfig
from env_loader import load_env_file
from llm_structurer import DEFAULT_MODEL, load_system_prompt
from sheet_row_mapper import sheet_rows_to_records
from sheets_client import build_sheets_service, ensure_sheet_exists, overwrite_sheet_with_rows, read_rows


TARGET_MAIN_CATEGORIES = ("비용", "기능", "전반", "미적용 아쉬움", "희망 기능")
TARGET_SENTIMENTS_KO = ("긍정", "부정", "중립")


@dataclass(frozen=True)
class RowDef:
    category: str
    sub_category: str


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


def _normalize_text(s: str) -> str:
    return " ".join((s or "").split()).strip()


def _strip_code_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return s


def _chunked(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def _dod_text(today_count: int, prev_count: int) -> str:
    if prev_count <= 0:
        return "N/A"
    pct = round(((today_count - prev_count) / prev_count) * 100)
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct}%"


def _is_target_record(r) -> bool:
    if r.main_category not in TARGET_MAIN_CATEGORIES:
        return False
    if r.sentiment not in TARGET_SENTIMENTS_KO:
        return False
    if not r.date:
        return False
    return True


def _effective_sub_category(r) -> str:
    sub = (r.sub_category or "").strip()
    if r.main_category == "기능":
        return sub if sub else "기능"
    return r.main_category


def _analysis_text(r) -> str:
    preferred = _normalize_text(r.ko_translation)
    if preferred:
        return preferred
    return _normalize_text(r.original_text)


def _build_rows(records, analysis_date: date) -> tuple[list[RowDef], dict[tuple[str, str], int]]:
    bucket_total: dict[tuple[str, str], int] = defaultdict(int)

    for r in records:
        if r.date != analysis_date.isoformat():
            continue
        key = (r.main_category, _effective_sub_category(r))
        bucket_total[key] += 1

    function_rows = [
        RowDef(category="기능", sub_category=sub)
        for (main, sub), total in sorted(
            bucket_total.items(),
            key=lambda x: (-x[1], x[0][1]),
        )
        if main == "기능" and total > 0
    ]
    if not function_rows:
        function_rows = [RowDef(category="기능", sub_category="-")]

    ordered: list[RowDef] = []
    ordered.append(RowDef(category="비용", sub_category="비용"))
    ordered.extend(function_rows)
    ordered.append(RowDef(category="전반", sub_category="전반"))
    ordered.append(RowDef(category="미적용 아쉬움", sub_category="미적용 아쉬움"))
    ordered.append(RowDef(category="희망 기능", sub_category="희망 기능"))
    return ordered, bucket_total


def _label_chunk(
    *,
    api_key: str,
    model: str,
    request_timeout_sec: float,
    ko_system_prompt: str,
    main_category: str,
    sub_category: str,
    sentiment: str,
    keyword_mode: str,
    texts: list[str],
    inter_call_delay_sec: float = 1.0,
) -> list[str]:
    client = Anthropic(api_key=api_key, timeout=request_timeout_sec, max_retries=1)

    last_error: Exception | None = None
    for attempt in range(1, 4):
        payload = {
            "main_category": main_category,
            "sub_category": sub_category,
            "sentiment": sentiment,
            "keyword_mode": keyword_mode,
            "expected_count": len(texts),
            "texts": texts,
        }
        if attempt > 1:
            payload["retry_notice"] = (
                "labels_ko 개수가 expected_count와 달랐음. "
                "이번에는 반드시 labels_ko 길이를 정확히 맞출 것."
            )

        try:
            message = client.messages.create(
                model=model or DEFAULT_MODEL,
                max_tokens=16384,
                temperature=0,
                system=ko_system_prompt,
                messages=[
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
            if inter_call_delay_sec > 0:
                time.sleep(inter_call_delay_sec)

            content = _strip_code_fence(message.content[0].text)
            parsed = json.loads(content)
            labels = parsed.get("labels_ko", parsed.get("labels", []))
            if not isinstance(labels, list):
                raise ValueError("labels_ko must be a list")
            if len(labels) != len(texts):
                raise ValueError(
                    "labels_ko length mismatch: "
                    f"expected={len(texts)}, got={len(labels)}"
                )
            return [str(x) for x in labels]
        except RateLimitError as e:
            wait = min(15 * attempt, 60)
            print(
                f"  [rate limit] _label_chunk 대기 {wait}초 후 재시도 ({attempt}/3)...",
                flush=True,
            )
            time.sleep(wait)
            last_error = e
            continue
        except Exception as e:
            last_error = e
            continue

    raise ValueError(f"failed to get valid labels_ko after retries: {last_error}")


def _translate_keywords_to_ja(
    *,
    api_key: str,
    model: str,
    request_timeout_sec: float,
    ja_system_prompt: str,
    main_category: str,
    sub_category: str,
    sentiment: str,
    ko_keywords: list[str],
    inter_call_delay_sec: float = 1.0,
) -> list[str]:
    if not ko_keywords:
        return []

    client = Anthropic(api_key=api_key, timeout=request_timeout_sec, max_retries=1)
    last_error: Exception | None = None
    for attempt in range(1, 4):
        payload = {
            "main_category": main_category,
            "sub_category": sub_category,
            "sentiment": sentiment,
            "expected_count": len(ko_keywords),
            "ko_keywords": ko_keywords,
        }
        if attempt > 1:
            payload["retry_notice"] = (
                "ja_keywords 길이가 expected_count와 달랐음. "
                "이번에는 반드시 같은 개수/순서로 번역할 것."
            )

        try:
            message = client.messages.create(
                model=model or DEFAULT_MODEL,
                max_tokens=16384,
                temperature=0,
                system=ja_system_prompt,
                messages=[
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
            if inter_call_delay_sec > 0:
                time.sleep(inter_call_delay_sec)
            content = _strip_code_fence(message.content[0].text)
            parsed = json.loads(content)
            ja_keywords = parsed.get("ja_keywords", [])
            if not isinstance(ja_keywords, list):
                raise ValueError("ja_keywords must be a list")
            if len(ja_keywords) != len(ko_keywords):
                raise ValueError(
                    "ja_keywords length mismatch: "
                    f"expected={len(ko_keywords)}, got={len(ja_keywords)}"
                )
            return [_normalize_text(str(x)).replace("/", " ") for x in ja_keywords]
        except RateLimitError as e:
            wait = min(15 * attempt, 60)
            print(
                f"  [rate limit] _translate_keywords_to_ja 대기 {wait}초 후 재시도 ({attempt}/3)...",
                flush=True,
            )
            time.sleep(wait)
            last_error = e
            continue
        except Exception as e:
            last_error = e
            continue

    raise ValueError(f"failed to get valid ja_keywords after retries: {last_error}")


def _summarize_keywords(
    *,
    api_key: str,
    model: str,
    request_timeout_sec: float,
    ko_system_prompt: str,
    main_category: str,
    sub_category: str,
    sentiment: str,
    texts: list[str],
    max_items_per_call: int,
    max_keywords: int = 3,
    inter_call_delay_sec: float = 1.0,
) -> list[str]:
    if not texts:
        return []

    keyword_mode = "feature_request" if main_category == "희망 기능" else "general"
    counts: Counter[str] = Counter()

    for chunk in _chunked(texts, max_items_per_call):
        labels = _label_chunk(
            api_key=api_key,
            model=model,
            request_timeout_sec=request_timeout_sec,
            ko_system_prompt=ko_system_prompt,
            main_category=main_category,
            sub_category=sub_category,
            sentiment=sentiment,
            keyword_mode=keyword_mode,
            texts=chunk,
            inter_call_delay_sec=inter_call_delay_sec,
        )
        for label in labels:
            cleaned = _normalize_text(label).replace("/", " ")
            if not cleaned or cleaned == "-":
                continue
            counts[cleaned] += 1

    if not counts:
        return []

    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [label for label, _ in ranked[:max_keywords]]


def _format_cell(labels: list[str], total_count: int, language: str) -> str:
    if total_count <= 0:
        return "-"
    if not labels:
        if language == "ja":
            return f"単純意見共有 ({total_count})"
        return f"단순 의견 공유 ({total_count})"
    return f"{' / '.join(labels)} ({total_count})"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 src/run_generate_d_summary.py <analysis_date: YYYY-MM-DD>")
        return 1

    analysis_date_str = sys.argv[1].strip()
    analysis_date = _parse_date_yyyy_mm_dd(analysis_date_str)
    if not analysis_date:
        print(f"ERROR: invalid analysis date: {analysis_date_str}")
        return 1
    prev_date = analysis_date - timedelta(days=1)

    project_root = Path(__file__).resolve().parent.parent
    env = load_env_file(str(project_root / ".env"))

    api_key = env.get("ANTHROPIC_API_KEY")
    spreadsheet_id = env.get("SPREADSHEET_ID")
    credentials_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    model = env.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    request_timeout_sec = float(env.get("LLM_REQUEST_TIMEOUT_SEC", "120"))
    max_items_per_call = max(1, int(env.get("D_SUMMARY_MAX_ITEMS_PER_CALL", "80")))
    summary_concurrency = max(1, int(env.get("D_SUMMARY_CONCURRENCY", "3")))
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

    service = build_sheets_service(credentials_path)
    config = SheetConfig()
    ensure_sheet_exists(service, spreadsheet_id, config.sheet_b_name)

    b_rows = _strip_header(read_rows(service, spreadsheet_id, config.sheet_b_name))
    all_records = [r for r in sheet_rows_to_records(b_rows) if _is_target_record(r)]

    date_set = {analysis_date.isoformat(), prev_date.isoformat()}
    relevant_records = [r for r in all_records if r.date in date_set]

    day_counts = Counter(
        r.sentiment for r in relevant_records if r.date == analysis_date.isoformat()
    )
    prev_counts = Counter(
        r.sentiment for r in relevant_records if r.date == prev_date.isoformat()
    )

    row_defs, _ = _build_rows(relevant_records, analysis_date)

    day_bucket_texts: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for r in relevant_records:
        if r.date != analysis_date.isoformat():
            continue
        bucket = (r.main_category, _effective_sub_category(r), r.sentiment)
        t = _analysis_text(r)
        if t:
            day_bucket_texts[bucket].append(t)

    ko_system_prompt = load_system_prompt(
        str(project_root / "prompts" / "d_summary_keyword_system_prompt.md")
    )
    ja_system_prompt = load_system_prompt(
        str(project_root / "prompts" / "d_summary_ko_to_ja_system_prompt.md")
    )

    tasks: list[tuple[int, str, str, str, list[str]]] = []
    for row_idx, row_def in enumerate(row_defs):
        for sentiment in TARGET_SENTIMENTS_KO:
            texts = day_bucket_texts.get(
                (row_def.category, row_def.sub_category, sentiment), []
            )
            if not texts:
                continue
            tasks.append(
                (
                    row_idx,
                    row_def.category,
                    row_def.sub_category,
                    sentiment,
                    texts,
                )
            )

    print(
        f"Running D summary KO keyword extraction: tasks={len(tasks)}, "
        f"concurrency={summary_concurrency}",
        flush=True,
    )

    cell_labels_ko: dict[tuple[int, str], list[str]] = {}
    with ThreadPoolExecutor(max_workers=summary_concurrency) as executor:
        future_map = {}
        for task in tasks:
            row_idx, main_category, sub_category, sentiment, texts = task
            future = executor.submit(
                _summarize_keywords,
                api_key=api_key,
                model=model,
                request_timeout_sec=request_timeout_sec,
                ko_system_prompt=ko_system_prompt,
                main_category=main_category,
                sub_category=sub_category,
                sentiment=sentiment,
                texts=texts,
                max_items_per_call=max_items_per_call,
                max_keywords=3,
                inter_call_delay_sec=inter_call_delay_sec,
            )
            future_map[future] = task

        done = 0
        for future in as_completed(future_map):
            task = future_map[future]
            done += 1
            row_idx, main_category, sub_category, sentiment, _ = task
            try:
                labels = future.result()
            except Exception as e:
                print(
                    "ERROR: keyword extraction failed at "
                    f"row={row_idx}, category={main_category}, sub={sub_category}, "
                    f"sentiment={sentiment}, language=ko: {e}"
                )
                return 1
            cell_labels_ko[(row_idx, sentiment)] = labels
            print(
                f"  - done {done}/{len(tasks)} "
                f"(category={main_category}, sub={sub_category}, "
                f"sentiment={sentiment}, language=ko)",
                flush=True,
            )

    translation_tasks: list[tuple[int, str, str, str, list[str]]] = []
    for row_idx, row_def in enumerate(row_defs):
        for sentiment in TARGET_SENTIMENTS_KO:
            ko_labels = cell_labels_ko.get((row_idx, sentiment), [])
            if not ko_labels:
                continue
            translation_tasks.append(
                (
                    row_idx,
                    row_def.category,
                    row_def.sub_category,
                    sentiment,
                    ko_labels,
                )
            )

    print(
        f"Running D summary KO->JA translation: tasks={len(translation_tasks)}, "
        f"concurrency={summary_concurrency}",
        flush=True,
    )

    cell_labels_ja: dict[tuple[int, str], list[str]] = {}
    with ThreadPoolExecutor(max_workers=summary_concurrency) as executor:
        future_map = {}
        for task in translation_tasks:
            row_idx, main_category, sub_category, sentiment, ko_labels = task
            future = executor.submit(
                _translate_keywords_to_ja,
                api_key=api_key,
                model=model,
                request_timeout_sec=request_timeout_sec,
                ja_system_prompt=ja_system_prompt,
                main_category=main_category,
                sub_category=sub_category,
                sentiment=sentiment,
                ko_keywords=ko_labels,
                inter_call_delay_sec=inter_call_delay_sec,
            )
            future_map[future] = task

        done = 0
        for future in as_completed(future_map):
            task = future_map[future]
            done += 1
            row_idx, main_category, sub_category, sentiment, ko_labels = task
            try:
                ja_labels = future.result()
            except Exception as e:
                print(
                    "ERROR: KO->JA translation failed at "
                    f"row={row_idx}, category={main_category}, sub={sub_category}, "
                    f"sentiment={sentiment}: {e}"
                )
                return 1
            if len(ja_labels) != len(ko_labels):
                print(
                    "ERROR: translated keyword count mismatch at "
                    f"row={row_idx}, category={main_category}, sub={sub_category}, "
                    f"sentiment={sentiment}"
                )
                return 1
            cell_labels_ja[(row_idx, sentiment)] = ja_labels
            print(
                f"  - done {done}/{len(translation_tasks)} "
                f"(category={main_category}, sub={sub_category}, "
                f"sentiment={sentiment}, language=ja)",
                flush=True,
            )

    def _header_for(sent_ko: str, sent_en: str, lang_upper: str) -> str:
        n = day_counts[sent_ko]
        dod = _dod_text(n, prev_counts[sent_ko])
        return f"{lang_upper}_{sent_en} ({n}, DoD {dod})"

    headers = [
        "Category",
        "Sub Category",
        _header_for("긍정", "Positive", "KO"),
        _header_for("긍정", "Positive", "JA"),
        _header_for("부정", "Negative", "KO"),
        _header_for("부정", "Negative", "JA"),
        _header_for("중립", "Neutral", "KO"),
        _header_for("중립", "Neutral", "JA"),
    ]

    output_rows: list[list[str]] = []
    for row_idx, row_def in enumerate(row_defs):
        row: list[str] = [row_def.category, row_def.sub_category]
        for sent_ko in TARGET_SENTIMENTS_KO:
            total_count = len(
                day_bucket_texts.get((row_def.category, row_def.sub_category, sent_ko), [])
            )
            ko_labels = cell_labels_ko.get((row_idx, sent_ko), [])
            ja_labels = cell_labels_ja.get((row_idx, sent_ko), [])
            row.append(_format_cell(ko_labels, total_count, "ko"))
            row.append(_format_cell(ja_labels, total_count, "ja"))
        output_rows.append(row)

    out_sheet = f"D_summary_{analysis_date.isoformat()}"
    ensure_sheet_exists(service, spreadsheet_id, out_sheet)
    overwrite_sheet_with_rows(
        service=service,
        spreadsheet_id=spreadsheet_id,
        sheet_name=out_sheet,
        headers=headers,
        rows=output_rows,
    )

    print("SUCCESS: D summary sheet generated")
    print(f"sheet={out_sheet}")
    print(f"analysis_date={analysis_date.isoformat()}")
    print(f"prev_date={prev_date.isoformat()}")
    print(f"source_rows={len(relevant_records)}")
    print(f"output_rows={len(output_rows)}")
    print(f"model={model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
