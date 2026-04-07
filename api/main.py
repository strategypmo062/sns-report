from __future__ import annotations

import os
import sys
from pathlib import Path

# Add src/ to Python path so existing pipeline modules are importable
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))


def _materialize_google_credentials() -> None:
    """Render 등 클라우드 환경 대응.

    GOOGLE_APPLICATION_CREDENTIALS_JSON 환경변수에 service_account.json 전체 내용을
    텍스트로 넣어두면, 부팅 시점에 임시 파일로 복원하고 GOOGLE_APPLICATION_CREDENTIALS
    경로를 그 파일로 가리키게 한다. 환경변수가 없으면 (=로컬 Mac) 아무 동작도 하지 않으므로
    기존 워크플로우와 호환된다.
    """
    json_blob = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not json_blob:
        return
    target = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    p = Path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json_blob)
    p.chmod(0o600)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(p)


_materialize_google_credentials()

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.analyze import router as analyze_router
from routes.pipeline import router as pipeline_router

app = FastAPI(title="SNS Report")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(pipeline_router, prefix="/api")
app.include_router(analyze_router, prefix="/api")


@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))
