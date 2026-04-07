# SNS Reaction Automation - 프로젝트 통합 레퍼런스

Last Updated: 2026-04-07 v3 (Asia/Seoul)

---

## 1. 목표

현재 3~4시간 소요되는 SNS 반응 수집/분석 업무를 1시간 내 처리 가능하도록 자동화한다.
장기적으로 웹 배포하여 팀원들이 공통으로 사용 가능하게 한다.

---

## 2. 실행 플로우

1. **파싱** — raw text → `A_AI_정리` 시트에 구조화 저장
2. **사람이 검토** — `A_AI_정리`를 눈으로 확인/수정
3. **승인** — 검토 끝난 A → `B_누적_raw`에 누적 (`--confirm-reviewed` 필수)
4. **피벗 생성** — B 데이터 → `C_pivot_YYYY-MM-DD` 통계 탭 (기본 무차트)
5. **D 요약 생성** — B 데이터 → `D_summary_YYYY-MM-DD` 키워드 요약 (KO/JA 분리)
6. **xlsx 다운로드** — C 탭 기반 `.xlsx` 파일 생성 (차트 포함)

---

## 3. 확정된 운영 정책

### 3-1. 시트 구조
- 스프레드시트 URL 1개, 기본 탭 2개: `A_AI_정리` + `B_누적_raw`
- 컬럼 9개 고정:
  `원문 | KO 번역 | 날짜 | 메인 카테고리 | 서브 카테고리 | 긍정/부정/중립 | SNS | URL | 비고`
- `비고`는 시스템이 빈칸으로 저장, 사용자가 직접 수정 가능

### 3-2. A 시트 정책
- 승인 후 유지 (삭제 안 함)
- 새 파싱 실행 시 덮어쓰기

### 3-3. B 누적 정책
- `메인 카테고리 != "-"` 행만 누적
- 중복 키: `sha256(URL + "\n" + 원문)` — 중복 행은 스킵
- 승인 시 사용자 수정 사항을 학습해서 `data/user_corrections.json`에 저장

### 3-4. 카테고리 규칙
- **기능-기능 금지**: `메인 카테고리 == 기능`이면 `서브 카테고리`는 상세 항목만 허용
  - 허용 상세 항목: Unsend, Message Backup, Album, Font, Sub Profile, LINE family 서비스 혜택
- 기능이 아닌 경우: `서브 카테고리 = 메인 카테고리`
- LINE Premium/유료 문맥이 불명확한 일반 LINE 이슈: `-/-/-` 처리

### 3-5. C 피벗 정책
- `C_pivot_YYYY-MM-DD` 형식, 재실행 시 덮어쓰기
- **기본: 시트 차트 미생성** (`PIVOT_CREATE_CHARTS=0`)
- `.xlsx` export 시에만 차트 포함

### 3-6. D 요약 정책
- `D_summary_YYYY-MM-DD` 형식, 재실행 시 덮어쓰기
- 헤더: `KO_Positive (N, DoD ±nn%)` / `JA_Positive (N, DoD ±nn%)` 등
- DoD(전일 대비 변화율): 정수 반올림, 전일 0건이면 N/A
- 2단계 프롬프트:
  1. KO 키워드 추출 → `prompts/d_summary_keyword_system_prompt.md`
  2. KO→JA 1:1 번역 → `prompts/d_summary_ko_to_ja_system_prompt.md`

### 3-7. SNS 표준값
DCard, PTT, Threads, Instagram, YouTube, Facebook, Mobile01,
Google Play Store Review (Sensor Tower), Apple App Store Review (Sensor Tower)

### 3-8. 승인 안전장치
- `run_approve_to_b.py`는 반드시 `--confirm-reviewed` 플래그 필요
- 없으면 실행 즉시 중단됨

---

## 4. 파싱/분류 세부 정책

### 4-1. 입력
- **수동 경로**: URL + 원글 + 댓글을 RAW 텍스트로 수동 붙여넣기 → `run_parse_to_a.py`
- **자동 경로**: `run_collect_and_parse.py` — CollectedPost에서 units 직접 변환 (텍스트 우회)

### 4-2. 단위
- 원글 1행 + 댓글 기본 1행
- PTT: 연속 동일 작성자 댓글을 1행으로 병합

### 4-3. 날짜 처리
- 상대시간 → 파싱 실행시각 기준 절대날짜(`YYYY-MM-DD`)로 변환
- PTT는 게시글 시간(anchor) + 댓글 시간 텍스트 우선
- 시간 없는 연속 댓글 → 직전 댓글 시간 상속
- 단서 전혀 없을 때만 실행일 사용

### 4-4. PTT 전처리 강화
- 댓글 꼬리 IP/시각 텍스트 제거
- 동일 작성자 연속 댓글 자동 병합
- 게시글 anchor/직전 댓글 시간 상속 → 날짜 튐 최소화

### 4-5. LLM 응답 불일치 복구
- 응답 건수 ≠ 입력 건수일 때 → 개별 단위(single-unit) 재호출로 복구

---

## 5. 스크립트 맵 및 명령어

### 5-1. 스크립트 목록

| 기능 | 스크립트/경로 |
|------|---------|
| **웹앱 서버** | **`api/`** (FastAPI + Alpine.js SPA) |
| SNS 자동 수집 (수집만) | `src/run_collect_sns.py` |
| SNS 수집+직접 파싱 | `src/run_collect_and_parse.py` |
| 파싱 (수동 텍스트) | `src/run_parse_to_a.py` |
| 승인 | `src/run_approve_to_b.py` |
| 피벗 생성 | `src/run_generate_pivot.py` |
| D 요약 생성 | `src/run_generate_d_summary.py` |
| C 기반 xlsx 다운로드 | `src/run_export_c_sheet_xlsx.py` |
| 파싱 무결성 검증 | `src/run_verify_last_parse_integrity.py` |
| (보조) B 기반 xlsx | `src/run_export_pivot_xlsx.py` |

### 5-2. 웹앱 실행

```bash
# FastAPI 서버 시작 (포트 8000)
/usr/local/bin/python3.13 -m uvicorn main:app --app-dir api --port 8000
# 브라우저: http://localhost:8000
# .claude/launch.json 설정 완료 — Claude Code 프리뷰에서 자동 실행 가능
```

웹앱 구조:
- `api/main.py` — FastAPI 앱 진입점
- `api/jobs.py` — in-memory 잡 상태 관리
- `api/pipeline_adapter.py` — src/ 모듈 래핑, SSE 진행 콜백
- `api/routes/pipeline.py` — 수집+파싱 시작/스트림/취소/승인 엔드포인트
- `api/routes/analyze.py` — 피벗/요약 생성, xlsx 다운로드 엔드포인트
- `api/static/index.html` — Alpine.js + Tailwind CDN SPA (2탭: 수집&파싱 / 분석)

### 5-3. 표준 명령어 (CLI)

```bash
# 0-a) SNS 수집+직접 파싱 (권장 — 텍스트 변환 우회)
python3 src/run_collect_and_parse.py \
  --keywords "LINE,LINE Premium,付費版的LINE,Line收費" \
  --date-from 2026-04-02 --date-to 2026-04-02 \
  --platforms ptt,dcard \
  --ptt-boards "Gossiping,MobileComm"

# 0-b) SNS 수집만 (텍스트 파일 출력)
python3 src/run_collect_sns.py \
  --keywords "LINE,LINE Premium,付費版的LINE,Line收費" \
  --date-from 2026-04-02 --date-to 2026-04-02 \
  --platforms ptt \
  --ptt-boards "Gossiping,MobileComm" \
  --output data/collected_2026-04-02.txt

# 1) 수동 파싱 (수동 복붙 텍스트용)
python3 src/run_parse_to_a.py sample_input_user.txt

# 2) 승인 (반드시 --confirm-reviewed 필요!)
python3 src/run_approve_to_b.py --confirm-reviewed

# 3) 피벗 생성 (분석일, 트렌드 시작일)
python3 src/run_generate_pivot.py 2026-03-31 2026-03-17

# 4) D 요약 생성
python3 src/run_generate_d_summary.py 2026-04-02

# 5) xlsx 다운로드 (최신 C_pivot 자동 선택)
python3 src/run_export_c_sheet_xlsx.py

# 5-1) 특정 C 탭 지정
python3 src/run_export_c_sheet_xlsx.py C_pivot_2026-03-31

# (선택) 파싱 무결성 점검
python3 src/run_verify_last_parse_integrity.py input_file.txt
```

---

## 6. 모델/환경 설정

- 기본 모델: `claude-haiku-4-5-20251001` (속도/비용 최적)
- `.env` 설정 항목:
  - `ANTHROPIC_API_KEY` — Anthropic API 키
  - `ANTHROPIC_MODEL` — 모델 변경 (예: `claude-sonnet-4-6`)
  - `SPREADSHEET_ID` — Google Sheets 문서 ID
  - `GOOGLE_APPLICATION_CREDENTIALS` — 서비스 계정 키 경로
  - `PARSE_CONCURRENCY` — 병렬 파싱 워커 수 (권장 **2**, 이전 3)
  - `D_SUMMARY_CONCURRENCY` — D 요약 병렬 워커 수 (권장 **3**, 이전 4)
  - `LLM_INTER_CALL_DELAY_SEC` — LLM 호출 성공 후 쿨다운 (권장 **1.0**)
  - `PIVOT_CREATE_CHARTS` — C 시트 차트 생성 여부 (0=미생성, 1=생성)

---

## 7. 차트 색상 (xlsx 내보내기 기준)

| 감정 | 색상 코드 |
|------|----------|
| Negative | `#0000AE` |
| Positive | `#1626D1` |
| Neutral | `#4E67C8` |

---

## 8. 다음 작업 (우선순위)

1. 수집 자동화 나머지 플랫폼 구현 (~~Threads~~ 완료 → YouTube → Mobile01)
2. ~~DCard 수집 대안 검토 (Cloudflare Turnstile 차단 중)~~ → 완료 (DrissionPage 전환)
3. **LLM 번역 파이프라인 2단계 분리 검토**
   - 현재: 1회 호출로 분류+번역+구조화 → 중국어→한국어 번역 누락 빈발 (후처리 `_fix_untranslated_records`로 보완 중)
   - 개선안(B): 1단계 번역(중→한) → 2단계 분류/구조화로 분리하면 번역 누락 원천 차단
   - 트레이드오프: API 호출 2배(비용/시간 증가) vs 번역 정확도 대폭 향상
4. ~~웹 폼 구현~~ → 완료 (FastAPI 웹앱 `api/` 디렉토리)
5. ~~API 엔드포인트 래핑~~ → 완료
6. 사용자 수정 학습 품질 개선
7. **배포 방식 검토 (미확정)**
   - DCard 수집기가 실제 Chrome + 가상 디스플레이를 요구하므로 Vercel(서버리스) 불가
   - 유력 후보: Docker (Xvfb + Chrome + Python) — AWS/GCP/사내 서버 어디든 가능
   - PTT 등 다른 수집기는 단순 HTTP라 배포 환경 제약 없음
   - 배포 인프라 확정 후 DCard 수집 전략 재검토 필요

---

## 9. 작업 기록 (변경 이력)

### 2026-04-07 (시스템 프롬프트 개선 — 분류 정확도 향상)
- **`prompts/sns_structuring_system_prompt.md` 수정** — Claude Chat 튜닝 결과 반영.
  - 추가: URL/게시글 맥락 활용 규칙 (5-2) — 본문에 유료 언급 없어도 게시글이 Premium 토론이면 분류 가능
  - 추가: 복수 기능 동시 언급 → 전반 규칙 (7) — 두 개 이상 기능 언급 시 전반/전반
  - 추가: 희망 기능 vs 미적용 아쉬움 구분 기준 (8) — 기능 미출시=희망기능, 미노출=미적용아쉬움
  - 추가: 미적용 아쉬움 감정 기준 (9) — "갖고 싶은데 안 됨"=긍정, "못 써서 짜증"=부정
  - 추가: Album vs Message Backup 구분 기준 (6) — 무압축전송=Album, 채팅백업/기기이전=Message Backup
  - 추가: `App Icon` 서브 카테고리 신규 추가
  - 개선: 감정 복합 케이스 처리 기준 명시 (11)
- **`schemas/llm_structured_output.schema.json` 수정** — `App Icon` 서브 카테고리 enum 추가
- **근거**: `docs/prompt_eval_cases.md` 25개 케이스 기반 튜닝

### 2026-04-07 (프롬프트 평가 케이스 셋 작성)
- **`docs/prompt_eval_cases.md` 신규** — 프롬프트 튜닝 전 회귀 확인용 평가 케이스 25개 작성.
  - 영역 1: Premium 경계 판단 (10케이스) — 유료 맥락 있음/없음/애매한 케이스
  - 영역 2: 기능 서브 카테고리 (7케이스) — Unsend/Font/Message Backup/미적용 아쉬움 등
  - 영역 3: 카테고리 전반 (8케이스) — 비용/희망기능/-/-/- 판단 기준
  - 🔴 요검토 케이스 9개 별도 표로 정리 (URL 맥락 인정 여부, 복수 기능 언급 등 정책 미확정 항목)
- **용도**: Claude Chat에서 프롬프트 튜닝 시 이 케이스들로 before/after 비교. 프롬프트 변경 후 회귀 확인 기준.

### 2026-04-07 (Render 배포 준비 — Docker + Xvfb)
- **`Dockerfile` 신규** — `python:3.13-slim` 기반, `chromium` + `chromium-driver` + `xvfb` + `xauth` + `fonts-noto-cjk` + `fonts-noto-color-emoji` 설치. CMD에서 `xvfb-run -a --server-args='-screen 0 1280x1024x24' uvicorn main:app --app-dir api --host 0.0.0.0 --port ${PORT}` 실행하여 가상 디스플레이 안에서 웹앱 + DrissionPage Chromium 동작.
- **`render.yaml` 신규** — Render Blueprint. `runtime: docker`, `plan: free` (메모리 부족 시 starter→standard로 변경), `region: singapore`, `healthCheckPath: /`. 환경변수 정의:
  - `sync: false` (대시보드에서 입력): `ANTHROPIC_API_KEY`, `SPREADSHEET_ID`, `GOOGLE_APPLICATION_CREDENTIALS_JSON` (서비스 계정 JSON 전체 내용)
  - 고정값: `GOOGLE_APPLICATION_CREDENTIALS=/tmp/sa.json`, `PARSE_CONCURRENCY=2`, `D_SUMMARY_CONCURRENCY=3`, `LLM_INTER_CALL_DELAY_SEC=1.0`, `PIVOT_CREATE_CHARTS=0`
- **`.dockerignore` 신규** — `.git`, `.venv`, `__pycache__`, `.env`, `keys/`, `*.xlsx`, `*.txt`, `docs/`, `.DS_Store`, `.claude` 등 빌드 컨텍스트에서 제외하여 비밀 파일 유출 방지.
- **`api/main.py` 수정** — `_materialize_google_credentials()` 부트스트랩 함수 추가. `GOOGLE_APPLICATION_CREDENTIALS_JSON` 환경변수가 있으면 그 내용을 `GOOGLE_APPLICATION_CREDENTIALS` 경로(기본 `/tmp/sa.json`)에 파일로 기록 후 권한 0600 설정. 환경변수가 없으면 no-op이라 로컬 Mac 워크플로우와 완전 호환.
- **알려진 위험**: ① Render 무료/Starter 플랜 메모리 512MB 한계 — Chromium 부팅 시 OOM 가능성, 발생 시 `plan: standard`로 업그레이드. ② Cloudflare Turnstile이 클라우드 IP 대역을 차단하는 경향 — DCard 수집은 Render에서 실패할 가능성 있음. 실패 시 DCard는 로컬 Mac에서 수동 수집으로 처리(사용자 결정 사항).
- **인증**: 미구현(보류). Render URL을 팀 외부에 공유 금지. 후속 작업 후보: HTTP Basic Auth 또는 Cloudflare Tunnel + Cloudflare Access.
- **검증 절차**: 빌드 성공 → `/` 200 → Sheets 호출 1회 → Threads 수집 1회 → DCard 1회(베스트 에포트) → Render Metrics에서 RAM 모니터링.
- **실제 배포 후 확인된 사항 (2026-04-07)**:
  - `api/pipeline_adapter.py::_load_env()` — `os.environ` 우선 읽도록 수정 (Render 환경변수 인식 안 되던 문제 해결)
  - `.dockerignore`에서 `*.txt` → 구체적 패턴으로 변경 (`requirements.txt` 빌드 컨텍스트 제외 버그 수정)
  - `src/collectors/dcard.py`, `src/collectors/threads.py` — `--no-sandbox`, `--disable-dev-shm-usage` 추가 (Linux 컨테이너 Chrome 실행 필수)
  - `src/collectors/threads.py` — `--headless=new` 추가 (Xvfb 없이 안정적 실행, Threads는 Cloudflare 불필요)
  - **DCard 확정 불가**: Cloudflare가 Render 클라우드 IP 차단 → Mac 수동 수집으로 운영
  - **Render 무료 플랜 메모리 한계**: Chrome + LLM 동시 실행 시 OOM 발생. Standard($25/월) 또는 Cloudflare Tunnel + Mac 전환 검토 중

### 2026-04-07 (Threads 수집 품질 개선)
- **`src/collectors/threads.py` — `_clean_text()`** UI 노이즈 필터 추가
  - `·` (middle dot) 단독 라인 제거 — Threads UI가 작성자 구분자로 삽입
  - `Author` / `작성자` 레이블 제거 (`· Author` 형태 포함)
- **`src/collectors/threads.py` — `_search_keyword()`** 스크롤 대기 시간 2초 → 4초
  - Threads 무한 스크롤 로드 완료 전 "변화 없음"으로 조기 종료하던 문제 해결
  - 검색 결과 수집 건수 증가 기대

### 2026-04-07 (Threads 수집기 구현)
- **`src/collectors/threads.py`** — 스텁 → 완전 구현
  - Threads Graph API `/keyword_search` 엔드포인트 사용 (`search_type=RECENT`, `since`/`until` Unix timestamp)
  - `/{post_id}/replies` 엔드포인트로 답글 수집 (`threads_read_replies` 권한 필요)
  - 커서 기반 페이지네이션 지원
  - HTTP 429 시 exponential backoff (최대 3회 재시도)
  - Rate limiter: 검색 1.0초, replies 0.5초 간격
  - 필요 환경변수: `THREADS_ACCESS_TOKEN` (Meta 개발자 앱 장기 토큰, 60일)
  - 필요 API 권한: `threads_basic`, `threads_keyword_search`, `threads_read_replies`
- **`src/config.py`** — `SNS_DOMAIN_MAP`에 `"threads.net": "Threads"` 추가 (API 반환 permalink 도메인)

### 2026-04-07 (LLM Rate Limit 방어/예방 개선)
- **방어 3건** — rate limit 에러 시 자동 대기 + 재시도
  - `src/run_parse_to_a.py::_fix_untranslated_records()` — `client.messages.create()` 직접 호출을 `llm_structurer._call_with_retry()` 호출로 교체 (기존엔 에러 시 skip하여 번역 데이터 유실)
  - `src/run_generate_d_summary.py::_label_chunk()` — `RateLimitError`만 별도 캐치, 15→30→60초 지수 백오프 후 재시도
  - `src/run_generate_d_summary.py::_translate_keywords_to_ja()` — 동일한 `RateLimitError` 처리 추가
- **예방 2건** — rate limit 빈도 자체를 감소
  - `src/llm_structurer.py::_call_with_retry()` — `inter_call_delay_sec` 파라미터(기본 1.0초) 추가, 성공 후 sleep하여 버스트 억제. `structure_units()` / `structure_units_with_client()`도 파라미터 전달
  - `.env` / `docs` 기본값 변경: `PARSE_CONCURRENCY` 3→2, `D_SUMMARY_CONCURRENCY` 4→3, 신규 `LLM_INTER_CALL_DELAY_SEC=1.0`
- **영향 범위**: `src/run_parse_to_a.py`, `src/run_collect_and_parse.py`, `src/run_generate_d_summary.py` 모두 env 읽어 하위 함수에 전달
- **비용 변화**: 없음 (호출 횟수 불변). 정상 케이스 오버헤드 +10~20초. Rate limit 시 중단→자동 복구

### 2026-04-03 (자동 수집 직접 파싱)
- **자동 수집 경로 텍스트 포맷/파싱 우회** — DCard 빈 줄 댓글 분할 버그 수정
  - `src/collectors/unit_converter.py` (신규): `collected_posts_to_units()` — CollectedPost → units 직접 변환
    - PTT: 같은 author 연속 댓글 병합 (preparse 로직 재현)
    - DCard: 댓글당 하나의 unit, 빈 줄 포함해도 분할 없음
    - Threads: unit에 author 키 포함
  - `src/run_collect_and_parse.py` (신규): 수집+직접 파싱 CLI (텍스트 변환 없이 LLM → A 시트)
  - `api/pipeline_adapter.py` (수정): 이중 경로 — collected posts는 직접 변환, paste_text만 텍스트 경로
  - 기존 수동 경로(`run_parse_to_a.py`, `text_formatter.py`, `preparse.py`)는 변경 없음

### 2026-04-03 (추가)
- **FastAPI 웹앱 구현** (`api/` 디렉토리)
  - `api/main.py`: FastAPI 앱, `/api` 라우터 + `/static` 서빙 + 루트 `index.html`
  - `api/jobs.py`: in-memory 잡 상태 관리 (running/done/error/cancelled)
  - `api/pipeline_adapter.py`: 기존 `src/` 모듈 래핑, 진행 콜백 지원
  - `api/routes/pipeline.py`: 수집+파싱 시작/스트림/취소/승인 API
  - `api/routes/analyze.py`: 피벗/요약 생성, xlsx 다운로드 API
  - `api/static/index.html`: Alpine.js + Tailwind CDN SPA (2탭 구조)
  - SSE(Server-Sent Events)로 수집/파싱 진행 상황 실시간 스트리밍
  - 서버 실행: `/usr/local/bin/python3.13 -m uvicorn main:app --app-dir api --port 8000`
- **웹앱 UI 정책**
  - 포인트 색상: `#06C755` (LINE 그린)
  - 수집 플랫폼: PTT(Gossiping, MobileComm 고정), DCard, **Threads**(기본 unchecked) 표시 — YouTube/Mobile01 미표시
  - Threads는 DrissionPage 브라우저 자동화 사용, 속도가 느리므로 기본 unchecked
  - 분석 탭 날짜 범위 검증: 종료일 < 시작일이면 에러 표시 + 버튼 비활성화
  - 기간 지정 시 시트명 형식: `C_pivot_2026-04-01_2026-04-03`
- **파싱 안정성 개선** (`api/pipeline_adapter.py`)
  - LLM JSON 파싱 실패 시 전체 파이프라인 중단 → `_recover_records_by_single_unit_calls`로 fallback
- **PTT 수집기 개선** (`src/collectors/ptt.py`)
  - 기존: 검색 결과 고정 3페이지 순회, 날짜 무관하게 모든 URL 열람
  - 변경: 목록 단계에서 날짜(`M/DD`) 파싱 → 날짜 범위 외 게시물은 열람 생략
  - 날짜 범위 이전 글이 등장하면 페이지 순회 조기 종료
  - `max_posts`는 비상용 상한선으로만 사용
- **의존성 추가** (`requirements.txt`): `fastapi>=0.110.0`, `uvicorn[standard]>=0.29.0`, `python-multipart>=0.0.9`

### 2026-04-03
- SNS 자동 수집 모듈 추가 (`src/run_collect_sns.py` + `src/collectors/`)
  - 지원 플랫폼: PTT (완성), DCard (완성), Threads/YouTube/Mobile01 (stub)
  - CLI: `--keywords`, `--date-from`, `--date-to`, `--platforms`, `--ptt-boards`, `--output`, `--max-posts`
  - 출력: 기존 `run_parse_to_a.py`에 바로 입력 가능한 raw text 파일
  - PTT: 게시판별 키워드 검색 → 게시글+댓글 스크래핑 → preparse.py 호환 포맷 변환
  - 수집기 구조: `base.py` (ABC), `text_formatter.py` (플랫폼별 raw text 포맷), `rate_limiter.py`
  - 의존성 추가: `requests`, `beautifulsoup4`, `playwright`, `playwright-stealth`
- DCard 수집기 완성: Playwright → DrissionPage + JS fetch 전환
  - Cloudflare Turnstile 우회: DrissionPage로 실제 Chrome 실행 → 페이지 내 fetch()로 API 호출
  - 본문 내 이미지 URL 제거(`_strip_url_lines`) — `split_url_blocks()` 오분리 방지
  - 의존성 추가: `DrissionPage>=4.0.0`
- DCard 수집기 버그 수정: 검색 API 엔드포인트 및 응답 구조 변경 대응
  - `/search/posts` → `/search/all` (엔드포인트 변경)
  - 검색 파라미터 추가: `field=all&sort=latest&country=TW&nsfw=false&platform=web`
  - 응답 구조 변경: 이전 `[{id, forumAlias}]` → 현재 `{"items": [{"searchPost": {"post": {...}, "forum": {...}}}], "nextKey": ...}`
  - `items[n]["searchPost"]["post"]["id"]`, `["forum"]["alias"]`에서 post_id/forum_alias 추출하도록 파싱 수정
  - 날짜 필터 적용 시 진단 로그 추가
- 번역 누락 이슈 식별: 1회 호출 다중작업(분류+번역+구조화)에서 중→한 번역 누락 빈발
  - 원인: 프롬프트 작업 과부하 + mini 모델 한계
  - 후속 과제: 2단계 파이프라인(번역→분류 분리) 검토 필요 → 8번 항목 참조
- `run_parse_to_a.py` 안정성 개선
  - LLM이 허용 목록 외 카테고리 반환 시 자동으로 `-` 처리 (예: `비cost`)
  - `기능` 카테고리인데 서브카테고리가 허용 목록 외일 때 `-` 처리
  - KO 번역 후처리 추가: 파싱 후 중국어가 남은 행을 감지해 LLM으로 재번역
- `prompts/sns_structuring_system_prompt.md` 수정
  - KO 번역 규칙 강화: 카테고리가 `-`이더라도 예외 없이 번역

### 2026-04-02
- D 요약 시트 생성 기능 추가 (`src/run_generate_d_summary.py`)
  - 입력: `B_누적_raw` + 분석일(`YYYY-MM-DD`)
  - 출력: `D_summary_YYYY-MM-DD` (동일 날짜 재실행 시 덮어쓰기)
- D 헤더 형식 확정:
  - `KO_Positive (N, DoD ±nn%)` / `JA_Positive` / `KO_Negative` / `JA_Negative` / `KO_Neutral` / `JA_Neutral`
- DoD: 전일 대비 퍼센트, 정수 반올림, 전일 0건은 N/A
- 셀 출력: 한 셀에 한 카테고리 + 한 언어, `키워드1 / 키워드2 / 키워드3 (총건수)` 형식
- D 요약 전용 프롬프트 추가:
  - `prompts/d_summary_keyword_system_prompt.md`
  - `prompts/d_summary_ko_to_ja_system_prompt.md`
- 승인 안전 플래그 추가: `--confirm-reviewed` 필수화
- 파싱 무결성 검증 스크립트 추가: `src/run_verify_last_parse_integrity.py`
- PTT 전처리 강화: IP/시간 제거, 동일 작성자 병합, 시간 상속
- parse 안정성 강화: LLM 응답 불일치 시 single-unit 재호출 복구
- C 피벗 기본 무차트 정책 적용 (`PIVOT_CREATE_CHARTS=0`)
- `.xlsx` export는 차트 포함 유지

### 2026-04-07
- **Threads 웹 스크래핑 방식 도입**
  - Threads API `keyword_search`는 Meta App Review 필수 (공개 키워드 검색 불가)
  - 대체: DrissionPage(실제 브라우저 자동화) + `threads.net/search?q=KEYWORD` 사용
  - 설치: `pip install DrissionPage`
- Threads 수집기 완전 재작성 (`src/collectors/threads.py`)
  - `_search_keyword()`: 검색 페이지 스크롤 → (URL, ISO timestamp) 추출
  - `_fetch_post()`: 각 게시물 방문 → 본문 + 댓글 추출
  - `_clean_text()`: 사용자명/시간/광고 노이즈 제거
- **Related threads 필터링 개선** (4단계 폴백 전략)
  - 전략 1: `[role="heading"], h1-h6`에서 "related" 문자열 검색 (언어 무관)
  - 전략 2: `aria-label` 속성 확인
  - 전략 3: TextWalker (기존) + 다국어 패턴 확장 + 부분 일치
  - 전략 4: `<hr>`, `[role="separator"]` 구조적 구분선
  - 결과: "Related threads" 섹션이 댓글로 수집되는 버그 해결
- 환경변수 로더 강화 (`src/run_collect_and_parse.py`, `src/run_collect_sns.py`)
  - `os.environ.update(env)` 추가로 Collector가 env dict 접근 가능
- 도메인 맵 확장 (`src/config.py`)
  - `SNS_DOMAIN_MAP`: `"threads.net": "Threads"` 추가

### 2026-04-07 v2 (Threads UI 추가 + DOM 추출 버그 수정)

- **웹앱 UI — Threads 플랫폼 체크박스 추가** (`api/static/index.html`)
  - `platforms` 초기 상태에 `Threads: false` 추가 (기본 unchecked — DrissionPage 브라우저 자동화로 느림)
  - `platformList` try 블록: API 응답 `activeSet.has('threads')` 기반으로 활성화 여부 결정
  - `platformList` catch 블록: DrissionPage 설치 여부 미확인 시 `active: false`로 fallback
  - Threads 힌트 텍스트(`브라우저 자동화 — 느림`) 제거 (과도한 UI 정보 정리)
  - 직접 붙여넣기 플레이스홀더 텍스트 변경:
    - 이전: 기존 영문 안내
    - 이후: `PTT·DCard 외 SNS(YouTube, Mobile01 등)는 링크와 텍스트를 여기에 직접 붙여넣으세요.`

- **Threads 수집기 DOM 추출 버그 수정** (`src/collectors/threads.py`)
  - 증상: 게시물 본문 + 댓글 + 답글의 텍스트가 하나의 행에 합쳐져서 중복 저장됨
  - 원인: JS가 `closest('[role="article"]')` 등 외부 컨테이너를 card로 잡아 모든 `<time>` 태그가 동일 카드의 `innerText`를 반환
  - 수정: `<time>` 태그별로 부모를 타고 올라가면서 "해당 time만 포함하는 가장 큰 ancestor"를 card로 사용
    ```javascript
    while (c && c !== document.body) {
        if (relatedEl && c.contains(relatedEl)) break;
        if (c.querySelectorAll('time').length > 1) break;  // 다른 time 포함 시 중단
        if (c.querySelector('a[href^="/@"]')) card = c;    // 유저 링크 있으면 유효 카드
        c = c.parentElement;
    }
    ```
  - 결과: 본문 1행, 댓글 1행, 답글 1행으로 정확히 분리됨
  - 중복 제거 키(dedup key)에 `text[:80]` 추가 (동일 username이 여러 댓글 달 경우 대비)

- **진단 로깅 추가** (stdout 버퍼링 대응)
  - `src/collectors/threads.py`: `_start_browser()` 전후 `flush=True` print 추가
  - `api/pipeline_adapter.py`: `collect()` 시작/완료/예외 시 `flush=True` print 추가
  - 배경: 웹앱에서 수집이 0건으로 즉시 종료되는 것처럼 보이는 증상 → flush=True로 실시간 출력 확인 후 실제 수집 정상 동작 확인

### 2026-04-01
- 로컬 MVP 골격 생성 (`docs/`, `prompts/`, `schemas/`, `src/`)
- Google Sheets 연결 테스트 성공
- OpenAI 파싱 파이프라인 연결
- Threads 도메인 분리, 병렬 파싱 도입
- 승인 시 중복 상세 출력, `메인 카테고리 "-"` 제외 규칙 반영
- 파싱 결과: 5 URL 블록, 121 유닛, A 시트 121행 반영 성공
- 승인 실행: approved=57, skipped_dash=64, learned=77
- Pivot 자동 생성: `C_pivot_2026-03-31`, charts 3종
- 차트 스타일 조정 (제목 제거, 라벨 형식, 색상 코드, Style 2 통일)
- xlsx 내보내기 엔진 전환: `openpyxl` → `xlsxwriter`
- C 탭 기반 다운로드 방식 추가: `src/run_export_c_sheet_xlsx.py`
- 템플릿 기반 export 정리 (1개 경로로 통일)

---

## 10. 문서 운영 규칙

- 기능/정책/로직 수정 시:
  - 이 파일의 `작업 기록`에 날짜 + 변경 내용 추가
  - 정책 변경 시 `3. 확정된 운영 정책`도 함께 갱신
