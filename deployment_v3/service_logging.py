"""Runtime log-level capabilities for managed service types."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .private_rpc import private_json_rpc_command


class RuntimeLogLevelError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeLogLevelAdapter:
    service_types: frozenset[str]
    levels: frozenset[str]


_BESU_ADAPTER = RuntimeLogLevelAdapter(
    service_types=frozenset({"besu-rpc", "besu-validator", "besu-observer"}),
    levels=frozenset({"TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL", "OFF"}),
)
_ADAPTERS = (_BESU_ADAPTER,)
_LEVEL_ALIASES = {"WARNING": "WARN", "CRITICAL": "FATAL"}


def runtime_log_level_adapter(service_type: str) -> RuntimeLogLevelAdapter | None:
    return next((adapter for adapter in _ADAPTERS if service_type in adapter.service_types), None)


def runtime_log_level_capability(service_type: str) -> dict[str, object]:
    adapter = runtime_log_level_adapter(service_type)
    return {
        "runtime_log_level": adapter is not None,
        "runtime_log_levels": sorted(adapter.levels) if adapter else [],
    }


def configured_runtime_log_level(plan, service) -> str:
    """Return the deployed base level for a service that supports runtime changes."""
    if service.type in _BESU_ADAPTER.service_types:
        return normalize_runtime_log_level(service.type, plan.raw.get("blockchain", {}).get("logging", {}).get("level", "INFO"))
    raise RuntimeLogLevelError(f"runtime log-level unsupported for {service.type}")


def normalize_runtime_log_level(service_type: str, level: str) -> str:
    adapter = runtime_log_level_adapter(service_type)
    if adapter is None:
        raise RuntimeLogLevelError(f"runtime log-level unsupported for {service_type}")
    normalized = _LEVEL_ALIASES.get(str(level).upper(), str(level).upper())
    if normalized not in adapter.levels:
        allowed = ", ".join(sorted(adapter.levels | set(_LEVEL_ALIASES)))
        raise RuntimeLogLevelError(f"unsupported runtime log level {level!r} for {service_type}; use one of: {allowed}")
    return normalized


def apply_runtime_log_level(executor, deployment_id: str, machine_id: str, service, level: str) -> str:
    """Apply a supported runtime log level and return its normalized value."""
    normalized = normalize_runtime_log_level(service.type, level)
    # Besu is the first adapter. Future service types add their own branch here
    # while preserving the operation, capability and audit contract.
    if service.type in _BESU_ADAPTER.service_types:
        result = executor.run(
            private_json_rpc_command(deployment_id, machine_id, service, "admin_changeLogLevel", [normalized]),
            timeout=20,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "private RPC request failed"
            raise RuntimeLogLevelError(f"set runtime log level for {service.id}: {detail}")
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeLogLevelError(f"runtime log-level RPC for {service.id} returned invalid JSON") from exc
        if response.get("result") != "Success":
            raise RuntimeLogLevelError(
                f"runtime log-level RPC rejected {service.id}: {response.get('error') or response.get('result')}"
            )
        return normalized
    raise RuntimeLogLevelError(f"runtime log-level unsupported for {service.type}")
