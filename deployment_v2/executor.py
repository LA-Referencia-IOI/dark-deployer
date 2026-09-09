"""Small, explicit local and SSH executors used by v2 preflight and runner."""

from __future__ import annotations

import ipaddress
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .model import Machine


class ExecutionError(RuntimeError):
    """A destination command could not be executed or failed."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Executor:
    def run(self, argv: Sequence[str], *, timeout: float = 30.0) -> CommandResult:
        raise NotImplementedError


class LocalExecutor(Executor):
    def run(self, argv: Sequence[str], *, timeout: float = 30.0) -> CommandResult:
        completed = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
        return CommandResult(tuple(argv), completed.returncode, completed.stdout, completed.stderr)


class SshExecutor(Executor):
    def __init__(self, machine: Machine):
        self.machine = machine

    def run(self, argv: Sequence[str], *, timeout: float = 30.0) -> CommandResult:
        ssh = self.machine.ssh
        command = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-i", ssh.private_key_file, "-p", str(ssh.port)]
        if ssh.known_hosts_file:
            command.extend(["-o", f"UserKnownHostsFile={ssh.known_hosts_file}"])
        command.append(f"{ssh.user}@{self.machine.management_address}")
        command.extend(argv)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return CommandResult(tuple(command), completed.returncode, completed.stdout, completed.stderr)


def _local_addresses() -> set[str]:
    values = {"127.0.0.1", "::1"}
    for hostname in (socket.gethostname(), socket.getfqdn()):
        try:
            values.update(item[4][0] for item in socket.getaddrinfo(hostname, None))
        except socket.gaierror:
            continue
    return values


def resolve_executor(machine: Machine) -> Executor:
    if machine.execution == "local":
        return LocalExecutor()
    if machine.execution == "ssh":
        return SshExecutor(machine)
    try:
        management = {item[4][0] for item in socket.getaddrinfo(machine.management_address, None)}
    except socket.gaierror as exc:
        raise ExecutionError(f"cannot resolve auto machine {machine.id}: {machine.management_address}") from exc
    local = _local_addresses()
    matching = management.intersection(local)
    if matching:
        if len(management) != len(matching):
            raise ExecutionError(f"auto machine {machine.id} resolves to both local and remote addresses")
        return LocalExecutor()
    return SshExecutor(machine)


def run_preflight(machine: Machine) -> list[dict[str, str | bool]]:
    """Run only read-only checks. It never creates paths, networks or containers."""
    executor = resolve_executor(machine)
    commands = {
        "docker": ("docker", "info", "--format", "{{.ID}}"),
        "compose": ("docker", "compose", "version", "--short"),
        "workspace_parent": ("test", "-d", str(Path(machine.workspace_root).parent)),
        "data_parent": ("test", "-d", str(Path(machine.data_root).parent)),
        "secrets_parent": ("test", "-d", str(Path(machine.secrets_root).parent)),
    }
    results: list[dict[str, str | bool]] = []
    for name, argv in commands.items():
        result = executor.run(argv)
        results.append({
            "check": name,
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        })
    return results
