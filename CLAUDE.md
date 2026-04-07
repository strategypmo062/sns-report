# SNS Reaction Automation

SNS 반응(댓글/게시물)을 자동 분류·요약하는 파이프라인.
Google Sheets 기반, Anthropic Claude LLM 사용.

## 핵심 규칙 (절대 위반 금지)

1. **approve 전 반드시 사람 검토** — `--confirm-reviewed` 플래그 없이 approve 실행 금지
2. **기능-기능 금지** — `메인 카테고리=기능`이면 `서브 카테고리`는 상세 항목만 허용
3. **Premium 불명확 → -/-/-** — LINE Premium/유료 문맥이 불분명하면 메인/서브/감정 모두 `-`
4. **C_pivot 기본 무차트** — `PIVOT_CREATE_CHARTS=0`이 기본
5. **.xlsx export는 차트 포함** — `run_export_c_sheet_xlsx.py`는 항상 차트 3종 생성
6. **D_summary KO/JA 분리** — 한국어 키워드 추출 → 일본어 번역, 별도 열
7. **DoD 정수 반올림** — 전일 대비 변화율은 소수점 없이 반올림

## 웹앱 실행

```bash
# FastAPI 서버 시작 (포트 8000)
/usr/local/bin/python3.13 -m uvicorn main:app --app-dir api --port 8000
# 브라우저: http://localhost:8000
```

## 실행 명령어 (CLI)

```bash
# SNS 수집+직접 파싱 (권장 — 텍스트 변환 우회)
python3 src/run_collect_and_parse.py \
  --keywords "LINE,LINE Premium" \
  --date-from YYYY-MM-DD --date-to YYYY-MM-DD \
  --platforms ptt,dcard \
  --ptt-boards "Gossiping,MobileComm"

# SNS 수집만 (→ raw text 파일)
python3 src/run_collect_sns.py \
  --keywords "LINE,LINE Premium" \
  --date-from YYYY-MM-DD --date-to YYYY-MM-DD \
  --platforms ptt \
  --ptt-boards "Gossiping,MobileComm"

# 수동 파싱 (raw text → A 시트)
python3 src/run_parse_to_a.py [입력파일.txt]

# 승인 (A → B 누적, 반드시 플래그 필요!)
python3 src/run_approve_to_b.py --confirm-reviewed

# 피벗 생성 (B → C 시트)
python3 src/run_generate_pivot.py [분석일] [트렌드시작일]

# D 요약 생성 (B → D 시트)
python3 src/run_generate_d_summary.py [분석일]

# xlsx 다운로드 (C → 파일)
python3 src/run_export_c_sheet_xlsx.py [C_pivot_날짜]

```

## .env 필수 항목

- `ANTHROPIC_API_KEY`
- `SPREADSHEET_ID`
- `GOOGLE_APPLICATION_CREDENTIALS`

## .env 권장 항목 (rate limit 방어)

- `PARSE_CONCURRENCY=2` (기본 2, 이전 3)
- `D_SUMMARY_CONCURRENCY=3` (기본 3, 이전 4)
- `LLM_INTER_CALL_DELAY_SEC=1.0` — LLM 호출 성공 후 쿨다운 (초)

## 웹앱 구조

- `api/main.py` — FastAPI 앱 진입점
- `api/jobs.py` — 잡 상태 관리
- `api/pipeline_adapter.py` — src/ 모듈 래핑
- `api/routes/pipeline.py` — 수집+파싱 API
- `api/routes/analyze.py` — 피벗/요약/xlsx API
- `api/static/index.html` — Alpine.js + Tailwind SPA

## 핵심 규칙 (추가)

8. **코드/기능/정책 변경 시 즉시** `docs/PROJECT_REFERENCE.md` 작업 기록 업데이트

## 상세 문서

`docs/PROJECT_REFERENCE.md` — 전체 정책, 작업 기록, 세부 사양
