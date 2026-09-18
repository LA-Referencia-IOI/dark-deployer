"""Read-only Docker metrics for the machines of a managed deployment.

``collector`` issues the read-only probe and parses it, and imports no terminal
library at all, so a non-interactive exporter, a scheduled check or a test can
reuse exactly the same collection and parsing code.  ``textual_app`` is the
interactive panel and imports Textual lazily, which keeps the optional
dependency out of the rest of the deployment CLI.
"""

from .collector import (
    DEFAULT_CONCURRENCY,
    DEPLOYMENT_LABEL,
    PROBE_TIMEOUT_SECONDS,
    SERVICE_LABEL,
    ContainerRate,
    ContainerSample,
    DiskFootprint,
    MachineMetrics,
    container_rates,
    daemon_key,
    parse_probe_output,
    probe_script,
    sample_machine,
    sample_machines,
)
from .prometheus import collect_to_textfile, render_prometheus

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEPLOYMENT_LABEL",
    "PROBE_TIMEOUT_SECONDS",
    "SERVICE_LABEL",
    "ContainerRate",
    "ContainerSample",
    "DiskFootprint",
    "MachineMetrics",
    "container_rates",
    "daemon_key",
    "parse_probe_output",
    "probe_script",
    "sample_machine",
    "sample_machines",
    "collect_to_textfile",
    "render_prometheus",
]
