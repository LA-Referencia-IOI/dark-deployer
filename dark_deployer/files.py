"""Configuration file parsing and secure atomic writes."""

import os
import sys
import tempfile
from pathlib import Path


def parse_env_file(filepath: Path, required: bool = True) -> dict:
    values = {}
    if not filepath.exists():
        if required:
            print(
                f"[ERROR] '{filepath}' not found. "
                "Copy .env.example to .env and fill in the values."
            )
            sys.exit(1)
        return values

    with filepath.open() as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()
    return values


def write_text_secure(path: Path, content: str, mode: int = 0o600) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        temporary.chmod(mode)
        os.replace(temporary, path)
        path.chmod(mode)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_env_secure(path: Path, values: dict, mode: int = 0o600) -> None:
    content = "".join(f"{key}={value}\n" for key, value in values.items())
    write_text_secure(path, content, mode=mode)

