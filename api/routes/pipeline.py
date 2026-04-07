from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobs as job_store
import pipeline_adapter as adapter

router = APIRouter()


class CollectParseParams(BaseModel):
    keywords: list[str]
    date_from: str
    date_to: str
    platforms: list[str] = []
    ptt_boards: list[str] = ["Gossiping", "MobileComm"]
    max_posts: int = 30
    paste_text: str = ""


@router.post("/pipeline/start")
async def pipeline_start(params: CollectParseParams):
    job = job_store.create_job()
    cancelled = threading.Event()

    def _run() -> None:
        try:
            adapter.collect_and_parse(
                keywords=params.keywords,
                date_from=params.date_from,
                date_to=params.date_to,
                platforms=params.platforms,
                ptt_boards=params.ptt_boards,
                max_posts=params.max_posts,
                paste_text=params.paste_text,
                on_event=job.emit,
                cancelled=cancelled,
            )
            job.finish({"status": "done"})
        except Exception as e:
            job.fail(str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"job_id": job.id}


@router.post("/pipeline/cancel/{job_id}")
async def pipeline_cancel(job_id: str):
    job = job_store.get_job(job_id)
    if job:
        job.cancel()
    return {"ok": True}


@router.get("/pipeline/stream/{job_id}")
async def pipeline_stream(job_id: str):
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
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            if done:
                if status == "done":
                    yield f"data: {json.dumps({'type': 'done', 'result': result})}\n\n"
                elif status == "error":
                    yield f"data: {json.dumps({'type': 'error', 'message': error})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                break

            await asyncio.sleep(0.3)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/pipeline/approve")
async def pipeline_approve():
    result = adapter.run_approve()
    return result


@router.get("/sheets/a/stats")
async def sheet_a_stats():
    return adapter.get_sheet_a_stats()


@router.get("/platforms")
async def get_platforms():
    return {"platforms": adapter.get_available_platforms()}


@router.get("/spreadsheet-url")
async def spreadsheet_url():
    return {"url": adapter.get_spreadsheet_url()}
