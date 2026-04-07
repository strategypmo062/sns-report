from __future__ import annotations

from pathlib import Path


def load_env_file(path: str = ".env") -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}

    result: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result

