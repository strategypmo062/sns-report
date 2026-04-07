from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobs as job_store
import pipeline_adapter as adapter

router = APIRouter()

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class PivotParams(BaseModel):
    analysis_date: str  # YYYY-MM-DD (end of range → used as pivot analysis date)
    trend_start: str    # YYYY-MM-DD


class SummaryParams(BaseModel):
    analysis_date: str  # YYYY-MM-DD (end of range)


@router.post("/analyze/pivot")
async def generate_pivot(params: PivotParams):
    script = str(SRC_DIR / "run_generate_pivot.py")
    result = subprocess.run(
        [sys.executable, script, params.analysis_date, params.trend_start],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()}
    return {
        "ok": True,
        "sheet": f"C_pivot_{params.analysis_date}",
    }


@router.post("/analyze/summary/start")
async def summary_start(params: SummaryParams):
    job = job_store.create_job()

    def _run() -> None:
        try:
            script = str(SRC_DIR / "run_generate_d_summary.py")
            proc = subprocess.Popen(
                [sys.executable, script, params.analysis_date],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(PROJECT_ROOT),
            )
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if "KO keyword extraction" in line:
                    m = re.search(r"tasks=(\d+)", line)
                    total = int(m.group(1)) if m else 0
                    job.emit({"type": "stage", "stage": "ko", "done": 0, "total": total})
                elif "language=ko)" in line:
                    m = re.search(r"done (\d+)/(\d+)", line)
                    if m:
                        job.emit({"type": "progress", "stage": "ko",
                                  "done": int(m.group(1)), "total": int(m.group(2))})
                elif "KO->JA translation" in line:
                    m = re.search(r"tasks=(\d+)", line)
                    total = int(m.group(1)) if m else 0
                    job.emit({"type": "stage", "stage": "ja", "done": 0, "total": total})
                elif "language=ja)" in line:
                    m = re.search(r"done (\d+)/(\d+)", line)
                    if m:
                        job.emit({"type": "progress", "stage": "ja",
                                  "done": int(m.group(1)), "total": int(m.group(2))})

            proc.wait()
            if proc.returncode == 0:
                job.finish({"sheet": f"D_summary_{params.analysis_date}"})
            else:
                job.fail("D summary script failed (non-zero exit)")
        except Exception as e:
            job.fail(str(e))

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job.id}


@router.get("/analyze/summary/stream/{job_id}")
async def summary_stream(job_id: str):
    async def generator() -> AsyncGenerator[str, None]:
        job = job_store.get_job(job_id)
        if not job:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Job not found'})}\n\n"
            return

        last_idx = 0
        while True:
            with job._lock:
                new_events = job.events[last_idx:]
                last_idx += len(new_events)
                done = job.is_done
                status = job.status
                result = job.result
                error = job.error

            for event in new_events:
                yield f"data: {json.dumps(event)}\n\n"

            if done:
                if status == "done":
                    yield f"data: {json.dumps({'type': 'done', 'result': result})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': error})}\n\n"
                break

            await asyncio.sleep(0.3)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/analyze/export/{sheet_name:path}")
async def export_xlsx(sheet_name: str):
    script = str(SRC_DIR / "run_export_c_sheet_xlsx.py")
    result = subprocess.run(
        [sys.executable, script, sheet_name],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()}

    exports_dir = PROJECT_ROOT / "exports"
    xlsx_files = sorted(exports_dir.glob(f"pivot_from_c_{sheet_name}*.xlsx"), reverse=True)
    if not xlsx_files:
        # Fallback: any file with the date in name
        date_part = sheet_name.replace("C_pivot_", "")
        xlsx_files = sorted(exports_dir.glob(f"*{date_part}*.xlsx"), reverse=True)
    if not xlsx_files:
        return {"ok": False, "error": "xlsx file not found after export"}

    return FileResponse(
        str(xlsx_files[0]),
        filename=xlsx_files[0].name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/analyze/pivot-sheets")
async def list_pivot_sheets():
    return {"sheets": adapter.list_pivot_sheets()}
