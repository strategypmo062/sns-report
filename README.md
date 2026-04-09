# SNS Reaction Automation

SNS 반응(댓글/게시물)을 자동으로 분류·요약해서 Google Sheets에 저장하는 도구입니다. Anthropic Claude LLM 사용.

---

## 📋 받는 사람을 위한 안내 (완전 초보용)

이 가이드는 **Python도 git도 한 번도 안 써본 사람**을 기준으로 작성됐어요. 순서대로 따라하면 됩니다.

### 0단계 — 본인 OS 확인

아래 안내에서 본인 컴퓨터에 맞는 부분만 보면 됩니다:
- 🍎 **Mac** = macOS
- 🪟 **Windows** = Windows 10/11

---

## 1단계 — 필수 프로그램 설치

### 🍎 Mac

**(1) Homebrew 설치** — Mac용 패키지 매니저. 터미널 앱(Spotlight에서 "터미널" 검색)을 열고 아래를 복사해서 붙여넣고 Enter:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

설치 끝나면 마지막에 안내되는 `echo ... >> ~/.zprofile` 같은 명령도 그대로 실행해 주세요.

**(2) Python + git 설치**:

```bash
brew install python git
```

**(3) 설치 확인**:

```bash
python3 --version    # Python 3.11 이상 권장
git --version        # git version 2.x.x
```

---

### 🪟 Windows

**(1) Python 설치**
- https://www.python.org/downloads/ 접속 → 최신 Python 다운로드 (3.11 이상이면 OK)
- 설치 파일 실행 시 **반드시 맨 아래 "Add python.exe to PATH" 체크박스를 켜고** "Install Now" 클릭

**(2) Git 설치**
- https://git-scm.com/download/win 접속 → 자동 다운로드된 설치 파일 실행
- 모든 옵션은 기본값(Next 연타)으로 설치하면 됩니다

**(3) 설치 확인** — 시작 메뉴에서 "PowerShell" 검색 → 열고:

```powershell
python --version    # Python 3.11 이상 권장
git --version       # git version 2.x.x
```

> 만약 `python`이 안 먹히면 PowerShell을 닫고 다시 열어보세요. 그래도 안 되면 Python 설치를 다시 하면서 "Add to PATH" 체크 확인.

---

## 2단계 — 프로젝트 클론(다운로드)

원하는 폴더로 이동 후 (예: 바탕화면) 아래 명령 실행:

```bash
git clone https://github.com/strategypmo062/sns-report.git
cd sns-report
```

`sns-report` 폴더가 생기고 그 안으로 들어가게 됩니다.

---

## 3단계 — 파이썬 패키지 설치

**중요**: 이 명령은 반드시 **2단계에서 만든 `sns-report` 폴더 안에서** 실행해야 해요. 터미널/PowerShell에 `cd sns-report`로 들어간 상태인지 확인하세요.

```bash
# Mac
pip3 install -r requirements.txt

# Windows
python -m pip install -r requirements.txt
```

> 💡 **Windows에서 `pip는 내부 또는 외부 명령... 아닙니다` 에러가 뜨면**:
> Windows는 `pip` 명령이 PATH에 안 잡히는 경우가 많아요. 위 표처럼 `pip install ...` 대신 **`python -m pip install ...`** 형태로 쓰면 거의 항상 됩니다.
>
> 그래도 안 되면 (`'python'은(는) 인식되지 않습니다` 라고 뜨면):
> 1. PowerShell 닫고 다시 열기
> 2. `python --version` 쳐서 Python 3.11+ 가 뜨는지 확인
> 3. 안 뜨면 → Python 재설치하면서 **첫 화면 맨 아래 "Add python.exe to PATH" 체크박스 꼭 켜기**
> 4. 재설치 후 PowerShell **새 창** 열고 다시 시도

---

## 4단계 — API 키와 인증 파일 준비

이 도구는 **본인 키 3종**이 필요합니다. 직접 발급 받으셔야 해요.

### (1) Anthropic API 키
- https://console.anthropic.com 가입 → 결제수단 등록 → "API Keys" 메뉴에서 새 키 발급
- `sk-ant-api03-...` 형태 문자열을 메모장에 잠깐 복사

### (2) 서비스 계정 JSON 파일을 `keys` 폴더에 넣기

받은 JSON 파일은 반드시 다음 **경로**와 **파일명**으로 두어야 코드가 인식해요:

```
sns-report/                    ← 2단계에서 git clone으로 만든 폴더
└── keys/                      ← 이 폴더 안에
    └── service_account.json   ← 정확히 이 이름으로
```

#### 🍎 Mac에서 하는 법

1. **`sns-report` 폴더 열기**
   - Finder 열기 → 사이드바에서 본인 사용자 이름(집 모양 아이콘) 클릭
   - 2단계에서 클론한 `sns-report` 폴더가 보임 → 더블클릭으로 들어가기
   - (만약 다른 위치에 클론했다면 그 위치로 이동)

2. **`keys` 폴더 만들기**
   - `sns-report` 폴더 안에서 **빈 공간 우클릭** → **"새로운 폴더"**
   - 폴더 이름을 정확히 **`keys`** (소문자) 로 입력 → Enter

3. **JSON 파일을 `keys` 폴더 안으로 옮기기**
   - 방금 만든 `keys` 폴더 더블클릭으로 들어가기
   - 다른 Finder 창에서 다운로드 폴더(또는 받은 JSON 파일이 있는 위치) 열기
   - JSON 파일을 `keys` 폴더 창 안으로 **드래그해서 놓기**

4. **파일 이름 바꾸기** *(제가 메일로 드린 파일 그대로 가지고 계신다면 이 과정은 생략해 주세요! 이름 안 바꾸셔도 됩니다)*
   - `keys` 폴더 안의 JSON 파일 클릭 → **Enter** 키 (이름 변경 모드)
   - 정확히 **`service_account.json`** 으로 입력 → Enter
   - 확장자를 변경하겠냐고 물으면 ".json 사용"

#### 🪟 Windows에서 하는 법

1. **`sns-report` 폴더 열기**
   - 파일 탐색기 열기 (작업표시줄의 폴더 모양 아이콘 또는 `Win + E`)
   - 사이드바에서 "내 PC" → "C:" → "사용자" → 본인 계정 폴더로 이동
   - 2단계에서 클론한 `sns-report` 폴더가 보임 → 더블클릭으로 들어가기
   - (만약 다른 위치에 클론했다면 그 위치로 이동)

   > 💡 **PowerShell에서 한 번에 여는 법**: 2단계 직후라면 PowerShell이 이미 `sns-report` 폴더 안에 있을 거예요. 아래 명령으로 해당 폴더를 파일 탐색기로 바로 열 수 있습니다:
   > ```powershell
   > explorer .
   > ```
   > 만약 PowerShell을 새로 열었다면 먼저 `cd $HOME\sns-report` 로 이동한 다음 `explorer .` 실행.

2. **`keys` 폴더 만들기**
   - `sns-report` 폴더 안에서 **빈 공간 우클릭** → **"새로 만들기"** → **"폴더"**
   - 폴더 이름을 정확히 **`keys`** (소문자) 로 입력 → Enter

3. **JSON 파일을 `keys` 폴더 안으로 옮기기**
   - 방금 만든 `keys` 폴더 더블클릭으로 들어가기
   - 다른 파일 탐색기 창에서 `다운로드` 폴더(또는 받은 JSON 파일이 있는 위치) 열기
   - JSON 파일을 `keys` 폴더 창 안으로 **드래그해서 놓기**

4. **파일 이름 바꾸기** *(제가 메일로 드린 파일 그대로 가지고 계신다면 이 과정은 생략해 주세요! 이름 안 바꾸셔도 됩니다)*
   - 먼저 파일 확장자가 보이게 설정 (Windows 11 기준): 상단 메뉴 **"보기" → "표시" → "파일 확장명"** 체크
   - `keys` 폴더 안의 JSON 파일 우클릭 → **"이름 바꾸기"** (또는 클릭 후 F2)
   - 정확히 **`service_account.json`** 으로 입력 → Enter

#### ⚠️ 자주 하는 실수

- **`keys` 가 아니라 `key`, `Keys`, `key 폴더` 같은 오타/대소문자 차이** → 정확히 소문자 `keys`
- **파일명이 `service_account.json.json` 으로 중복** → Windows에서 확장자 숨김 켜진 상태로 이름 바꿀 때 자주 발생. 위 4번에서 확장자 표시 먼저 켜기
- **JSON 파일을 `sns-report` 바로 아래 두고 `keys` 폴더 안 만듦** → 반드시 `keys` 폴더 안에 있어야 함
- **파일명을 `service-account.json` 처럼 하이픈으로** → 정확히 언더스코어(`_`) `service_account.json`

### (3) Google Spreadsheet 만들기 *(제가 이미 만들어 뒀기 때문에 이 과정도 생략해 주세요)*
- https://sheets.google.com 에서 새 빈 시트 만들기
- 시트 URL이 `https://docs.google.com/spreadsheets/d/**여기긴문자열**/edit` 같이 생겼는데, **여기긴문자열** 부분이 `SPREADSHEET_ID`
- 그 시트의 "공유" 버튼 → 위에서 만든 서비스 계정 이메일(`...@....iam.gserviceaccount.com`, JSON 파일 안에 `client_email`로 적혀있음)을 **편집자** 권한으로 추가

---

## 5단계 — `.env` 파일 만들기

프로젝트 폴더에 있는 `.env.example` 파일을 복사해서 `.env`로 이름 바꾸기:

```bash
# Mac
cp .env.example .env

# Windows (PowerShell)
copy .env.example .env
```

그다음 `.env` 파일을 텍스트 에디터로 열어서 4단계에서 받은 값들을 채워넣으세요.

> 💡 `.env` 파일은 점(`.`)으로 시작해서 더블클릭으로는 잘 안 열려요. 아래 방법 중 하나를 쓰세요:

#### 🍎 Mac에서 `.env` 여는 법

**방법 A — 터미널에서 기본 텍스트 에디터로**
```bash
open -a TextEdit .env
```

**방법 B — 숨김 파일 보이게 하고 Finder에서 열기**
1. Finder에서 프로젝트 폴더 열기
2. `Cmd + Shift + .` (점) — 숨김 파일 표시 토글
3. `.env` 파일이 회색으로 보임 → 우클릭 → "다음으로 열기" → "TextEdit" (또는 VSCode)

**방법 C — VSCode가 깔려 있다면**
```bash
code .env
```

#### 🪟 Windows에서 `.env` 여는 법

**방법 A — 메모장으로 (PowerShell)**
```powershell
notepad .env
```

**방법 B — 파일 탐색기에서**
1. 파일 탐색기로 프로젝트 폴더 열기
2. 상단 메뉴 "보기" → "표시" → "파일 확장명" 체크 (Windows 11 기준)
3. `.env` 파일 우클릭 → "연결 프로그램" → "메모장"

**방법 C — VSCode가 깔려 있다면**
```powershell
code .env
```

---

`.env` 파일을 열면 안에 이런 내용이 보여요. **`=` 뒤에 값만 채우면 됩니다** (따옴표 없이, 공백 없이):

```
ANTHROPIC_API_KEY=sk-ant-api03-여기에본인키
SPREADSHEET_ID=여기에시트ID
GOOGLE_APPLICATION_CREDENTIALS=keys/service_account.json
THREADS_USERNAME=
THREADS_PASSWORD=
PARSE_CONCURRENCY=2
D_SUMMARY_CONCURRENCY=3
LLM_INTER_CALL_DELAY_SEC=1.5
```

> ⚠️ `.env`와 `keys/service_account.json`은 절대 GitHub에 올라가지 않습니다 (`.gitignore`에 등록되어 있음). 안심하고 본인 키 넣으세요.

---

## 6단계 — 실행

### 웹앱으로 쓰기 (권장)

```bash
# Mac
python3 -m uvicorn main:app --app-dir api --port 8000

# Windows
python -m uvicorn main:app --app-dir api --port 8000
```

브라우저에서 http://localhost:8000 접속하면 화면이 뜹니다.
종료는 터미널에서 `Ctrl+C`.

---

## 자주 막히는 부분

| 증상 | 해결 |
|---|---|
| `command not found: python3` (Mac) | `brew install python` 다시 실행 |
| `'python'은(는) ... 인식되지 않습니다` (Windows) | Python 재설치 시 "Add to PATH" 체크. 설치 후 PowerShell 새 창 열기 |
| `pip는 내부 또는 외부 명령... 아닙니다` (Windows) | `pip` 대신 **`python -m pip install -r requirements.txt`** 로 실행 |
| `pip: command not found` (Mac) | `pip` 대신 `pip3` 로 실행 |
| `FileNotFoundError: keys/service_account.json` | 파일명 오타 확인 (`service_account.json` 정확히), `keys` 폴더 안에 있는지 확인, `sns-report` 폴더 안에서 실행 중인지 확인 |
| `gspread.exceptions.APIError: ... PERMISSION_DENIED` | 시트 공유 설정에서 서비스 계정 이메일에 **편집자** 권한 줬는지 확인 |
| `anthropic.AuthenticationError` | `.env`의 `ANTHROPIC_API_KEY` 값 확인, 결제수단 등록됐는지 확인 |
| 포트 8000 이미 사용 중 | 명령어 끝의 `--port 8000`을 `--port 8001` 로 바꾸기 |

---

## CLI로도 쓸 수 있음 (상급)

웹앱 대신 명령줄로 직접 단계별 실행도 가능. 자세한 명령은 [`CLAUDE.md`](CLAUDE.md) 또는 [`docs/PROJECT_REFERENCE.md`](docs/PROJECT_REFERENCE.md) 참고.

---

## 핵심 규칙

1. `approve` 전 반드시 사람 검토 — `--confirm-reviewed` 플래그 없이 실행 금지
2. `메인 카테고리=기능`이면 `서브 카테고리`는 상세 항목만 허용
3. LINE Premium/유료 문맥 불분명 → 메인/서브/감정 모두 `-`
4. C_pivot 기본 무차트
5. `.xlsx` export는 항상 차트 3종 포함
6. D_summary는 한국어 키워드 추출 → 일본어 번역, 별도 열
7. DoD 변화율은 정수 반올림

전체 정책·작업 기록은 [`docs/PROJECT_REFERENCE.md`](docs/PROJECT_REFERENCE.md) 참고.
