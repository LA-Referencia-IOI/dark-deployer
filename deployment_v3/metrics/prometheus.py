"""Prometheus textfile rendering for the read-only deployment metrics collector."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from .collector import MachineMetrics, sample_machines


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values: object) -> str:
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(values.items())) + "}"


def render_prometheus(plan, samples: tuple[MachineMetrics, ...], *, collected_at: float | None = None) -> str:
    """Render one completed collection; missing values are omitted, never zeroed."""
    timestamp = time.time() if collected_at is None else collected_at
    lines = [
        "# HELP dark_metrics_export_timestamp_seconds Unix time of the completed deployment collection.",
        "# TYPE dark_metrics_export_timestamp_seconds gauge",
        f'dark_metrics_export_timestamp_seconds{_labels(deployment=plan.deployment_id)} {timestamp:.6f}',
        "# HELP dark_machine_probe_success Whether the latest machine probe was usable.",
        "# TYPE dark_machine_probe_success gauge",
        "# HELP dark_machine_probe_warnings Number of partial-section or malformed-row warnings.",
        "# TYPE dark_machine_probe_warnings gauge",
        "# HELP dark_docker_daemon_cores CPU cores reported by the Docker daemon.",
        "# TYPE dark_docker_daemon_cores gauge",
        "# HELP dark_docker_daemon_memory_total_bytes Memory visible to the Docker daemon.",
        "# TYPE dark_docker_daemon_memory_total_bytes gauge",
        "# HELP dark_container_running Whether the managed container is running.",
        "# TYPE dark_container_running gauge",
        "# HELP dark_container_cpu_percent CPU percentage reported by docker stats.",
        "# TYPE dark_container_cpu_percent gauge",
        "# HELP dark_container_memory_bytes Memory used by the managed container.",
        "# TYPE dark_container_memory_bytes gauge",
        "# HELP dark_container_restart_count Container restart count; may reset after recreation.",
        "# TYPE dark_container_restart_count gauge",
        "# HELP dark_container_network_bytes_total Combined received and transmitted bytes since container start.",
        "# TYPE dark_container_network_bytes_total counter",
        "# HELP dark_container_block_bytes_total Combined block read and written bytes since container start.",
        "# TYPE dark_container_block_bytes_total counter",
        "# HELP dark_container_pids Number of processes or threads reported by docker stats.",
        "# TYPE dark_container_pids gauge",
        "# HELP dark_docker_disk_size_bytes Docker disk footprint reported by docker system df.",
        "# TYPE dark_docker_disk_size_bytes gauge",
        "# HELP dark_docker_disk_reclaimable_bytes Docker disk footprint that docker system df reports as reclaimable.",
        "# TYPE dark_docker_disk_reclaimable_bytes gauge",
        "# HELP dark_docker_disk_objects Docker objects counted by docker system df.",
        "# TYPE dark_docker_disk_objects gauge",
    ]
    machine_sites = {machine.id: machine.site or "default" for machine in plan.machines}
    for sample in samples:
        machine_labels = {
            "deployment": plan.deployment_id,
            "machine": sample.machine_id,
            "site": machine_sites.get(sample.machine_id, "default"),
        }
        lines.append(f"dark_machine_probe_success{_labels(**machine_labels)} {1 if sample.reachable else 0}")
        lines.append(f"dark_machine_probe_warnings{_labels(**machine_labels)} {len(sample.warnings)}")
        if sample.cores is not None:
            lines.append(f"dark_docker_daemon_cores{_labels(**machine_labels)} {sample.cores}")
        if sample.memory_total_bytes is not None:
            lines.append(f"dark_docker_daemon_memory_total_bytes{_labels(**machine_labels)} {sample.memory_total_bytes}")
        for footprint in sample.disk_footprint:
            disk_labels = {**machine_labels, "type": footprint.type}
            if footprint.size_bytes is not None:
                lines.append(f"dark_docker_disk_size_bytes{_labels(**disk_labels)} {footprint.size_bytes}")
            if footprint.reclaimable_bytes is not None:
                lines.append(f"dark_docker_disk_reclaimable_bytes{_labels(**disk_labels)} {footprint.reclaimable_bytes}")
            if footprint.total_count is not None:
                lines.append(f"dark_docker_disk_objects{_labels(**disk_labels)} {footprint.total_count}")
        for container in sample.containers:
            labels = {
                **machine_labels,
                "container": container.name,
                "service": container.service_id or "unknown",
            }
            lines.append(f"dark_container_running{_labels(**labels)} {1 if container.running else 0}")
            for metric, value in (
                ("dark_container_cpu_percent", container.cpu_percent),
                ("dark_container_memory_bytes", container.memory_bytes),
                ("dark_container_restart_count", container.restart_count),
                ("dark_container_network_bytes_total", container.network_bytes),
                ("dark_container_block_bytes_total", container.block_bytes),
                ("dark_container_pids", container.pids),
            ):
                if value is not None:
                    lines.append(f"{metric}{_labels(**labels)} {value}")
    return "\n".join(lines) + "\n"


def collect_to_textfile(plan, output: Path, *, include_disk: bool = False) -> tuple[MachineMetrics, ...]:
    """Collect all machines and atomically publish a node-exporter textfile."""
    samples = sample_machines(plan, include_disk=include_disk)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(render_prometheus(plan, samples))
        # node-exporter normally runs as an unprivileged user in its container.
        # The file contains operational measurements, never secret values.
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, output)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()
    return samples
