# Run Local Parse (to A Sheet)

## Prerequisites
- `.env` contains:
  - `ANTHROPIC_API_KEY`
  - `SPREADSHEET_ID`
  - `GOOGLE_APPLICATION_CREDENTIALS`
- Google Sheets API enabled
- Spreadsheet shared with service account (Editor)

## 1) Prepare input file
Save your pasted raw block into a text file, for example:
- `/Users/xxx/Documents/LINE_prepare/sample_input.txt`

## 2) Run parser
```bash
cd /Users/xxx/Documents/LINE_prepare
python3 src/run_parse_to_a.py sample_input.txt
```

## 3) Expected result
- `A_AI_정리` sheet is overwritten with:
  - 원문 | KO 번역 | 날짜 | 메인 카테고리 | 서브 카테고리 | 긍정/부정/중립 | SNS | URL | 비고
- `비고` is blank

## Notes
- Default model is `claude-haiku-4-5-20251001`.
- You can set custom model via `.env`:
  - `ANTHROPIC_MODEL=...`
- Parallel parse workers:
  - `PARSE_CONCURRENCY=3`
