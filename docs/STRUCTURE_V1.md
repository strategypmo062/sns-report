# SNS Reaction Automation - Structure V1

## Goal
Reduce manual SNS reaction processing to a repeatable workflow:
1. Paste raw text once
2. Auto-parse into `A_AI_정리` (overwrite)
3. User review/edit
4. Approve to append into `B_누적_raw` (dedupe)

## Fixed Rules (confirmed)
- Input: single raw textarea (URL + post + comments pasted together)
- Spreadsheet: one URL, two tabs
  - `A_AI_정리`: overwrite every parse run
  - `B_누적_raw`: append approved rows only
- Output columns (fixed order, 9 columns):
  - 원문 | KO 번역 | 날짜 | 메인 카테고리 | 서브 카테고리 | 긍정/부정/중립 | SNS | URL | 비고
- `비고` is always blank by system
- Dedupe: remove duplicates before appending to B
- Date rule:
  - Convert relative times using parse run timestamp
  - If no usable date/time clue in comment, use parse run date
  - Store as `YYYY-MM-DD`
- SNS standard values:
  - DCard, PTT, Threads, Instagram, YouTube, Facebook, Mobile01,
  - Google Play Store Review (Sensor Tower),
  - Apple App Store Review (Sensor Tower)

## Parsing Strategy (Hybrid)
1. Deterministic pre-parse (no rewriting)
   - extract URL
   - detect SNS from domain and text pattern
   - split into 1 post row + N comment rows
2. LLM structuring
   - strict translation + category + sentiment + date normalization
   - return JSON records only
3. Validator
   - enforce allowed values and required fields
   - enforce `-` rules for non-LINE Premium content
4. Sheet writer
   - write 9 columns to A
   - on approve, append deduped rows to B

## API Shape (local MVP)
- `POST /api/parse`
  - input: `{ raw_text: string, run_at_iso: string }`
  - output: `{ batch_id, parsed_count, dropped_count, preview[] }`
  - side effect: overwrite `A_AI_정리`
- `POST /api/approve`
  - input: `{ batch_id: string }`
  - output: `{ approved_count, skipped_duplicates }`
  - side effect: append to `B_누적_raw`

## Dedupe Key
- default key: `URL + original_text_exact`
- key hash: `sha256(url + "\n" + original_text)`

## Data Quality Gate
Reject or mark as error when:
- sentiment not in `긍정|부정|중립|-`
- main category not in `전반|비용|기능|희망 기능|미적용 아쉬움|-`
- sub category invalid for `기능`
- date not parseable to `YYYY-MM-DD`

## Suggested Repository Layout
- `docs/STRUCTURE_V1.md`
- `prompts/sns_structuring_system_prompt.md`
- `schemas/llm_structured_output.schema.json`
- `src/contracts.py`
- `src/validator.py`
- `src/transform_to_sheet.py`
