# Implementation Next (Local MVP)

## 1) Parse endpoint
- Input: `raw_text`, `run_at_iso`
- Steps:
  1. Extract first URL
  2. Detect SNS from URL
  3. Split into post/comment units (PTT first)
  4. Send units to LLM with `prompts/sns_structuring_system_prompt.md`
  5. Validate output against `schemas/llm_structured_output.schema.json`
  6. Transform to 9-column rows (add blank note)
  7. Overwrite `A_AI_정리`

## 2) Approve endpoint
- Read current rows from `A_AI_정리`
- Build dedupe key: `sha256(url + "\n" + original_text)`
- Append non-duplicate rows to `B_누적_raw`

## 3) Error handling
- LLM invalid JSON: retry 1 time
- Schema mismatch: fail parse run and show row-level errors
- Sheet write failure: no partial approval commit

## 4) First integration target
- Local web form with a single textarea and two buttons:
  - Parse
  - Approve
