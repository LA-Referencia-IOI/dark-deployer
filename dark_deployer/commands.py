"""Parsing for declarative component setup commands."""

import json
import sys


def parse_commands(commands: str | list[str]) -> list[str]:
    if isinstance(commands, list):
        parsed = commands
    else:
        raw = commands.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"[ERROR] Invalid COMMANDS_JSON value: {exc}")
                sys.exit(1)
        else:
            parsed = [part.strip() for part in raw.split("|") if part.strip()]

    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        print("[ERROR] Commands must be a JSON array of strings.")
        sys.exit(1)
    return [command.strip() for command in parsed if command.strip()]


def get_commands(env: dict, legacy_key: str, default: list[str] | None = None) -> list[str]:
    json_key = f"{legacy_key}_JSON"
    if env.get(json_key, "").strip():
        return parse_commands(env[json_key])
    if env.get(legacy_key, "").strip():
        return parse_commands(env[legacy_key])
    return list(default or [])

