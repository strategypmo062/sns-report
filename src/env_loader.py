from __future__ import annotations

from pathlib import Path


def load_env_file(path: str = ".env") -> dict[str, str]:
    import os
    # os.environ을 기본값으로 사용하고, .env 파일이 있으면 그 값으로 덮어씀.
    # Render처럼 .env 파일이 없는 환경에서는 os.environ만 사용.
    result: dict[str, str] = dict(os.environ)
    env_path = Path(path)
    if not env_path.exists():
        return result

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result

