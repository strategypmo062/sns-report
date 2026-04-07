from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from anthropic import Anthropic, RateLimitError


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return s


def load_system_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _call_with_retry(
    client,
    model,
    system_prompt,
    user_content,
    max_retries=5,
    inter_call_delay_sec: float = 1.0,
):
    """Call Anthropic API with automatic retry on rate limit errors.

    After a successful call, sleeps for `inter_call_delay_sec` seconds to throttle
    burst traffic across concurrent threads and reduce rate limit occurrences.
    """
    for attempt in range(1, max_retries + 1):
        try:
            message = client.messages.create(
                model=model or DEFAULT_MODEL,
                max_tokens=16384,
                temperature=0,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_content},
                ],
            )
            if inter_call_delay_sec > 0:
                time.sleep(inter_call_delay_sec)
            return message
        except RateLimitError as e:
            if attempt == max_retries:
                raise
            wait = min(15 * attempt, 60)
            print(f"  [rate limit] 대기 {wait}초 후 재시도 ({attempt}/{max_retries})...", flush=True)
            time.sleep(wait)


def structure_units(
    api_key: str,
    system_prompt: str,
    model: str,
    run_at_iso: str,
    url: str,
    sns: str,
    units: list[dict[str, Any]],
    request_timeout_sec: float = 120.0,
    inter_call_delay_sec: float = 1.0,
) -> dict[str, Any]:
    client = Anthropic(api_key=api_key, timeout=request_timeout_sec, max_retries=1)

    user_payload = {
        "run_at_iso": run_at_iso,
        "url": url,
        "sns": sns,
        "units": units,
    }

    message = _call_with_retry(
        client, model, system_prompt,
        json.dumps(user_payload, ensure_ascii=False),
        inter_call_delay_sec=inter_call_delay_sec,
    )

    content = message.content[0].text
    content = _strip_code_fence(content)
    return json.loads(content)


def structure_units_with_client(
    client: Anthropic,
    system_prompt: str,
    model: str,
    run_at_iso: str,
    url: str,
    sns: str,
    units: list[dict[str, Any]],
    inter_call_delay_sec: float = 1.0,
) -> dict[str, Any]:
    user_payload = {
        "run_at_iso": run_at_iso,
        "url": url,
        "sns": sns,
        "units": units,
    }

    message = _call_with_retry(
        client, model, system_prompt,
        json.dumps(user_payload, ensure_ascii=False),
        inter_call_delay_sec=inter_call_delay_sec,
    )

    content = message.content[0].text
    content = _strip_code_fence(content)
    return json.loads(content)
