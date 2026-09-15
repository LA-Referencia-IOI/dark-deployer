#!/usr/bin/env python3
"""Instantiate a maintained inventory for a real site.

Unlike ``generate-lima-inventory.py``, this script does not discover anything and
does not assume a topology: it takes any supported inventory as input, asks only
for the site facts the deployer actually consumes, and writes a new file that the
real resolver accepts.  The questions are derived from the input document, so the
same command works for a one-machine local lab, the five-host reference and the
six-host reference with a dedicated Resolver.

Design notes kept deliberately:

* The input is never modified and the output is never overwritten unless
  ``--overwrite`` is passed, matching ``inventory-resolve`` and
  ``create_from_template``.
* Nothing is written until the result has been validated through the same code path
  the CLI uses (``resolve_inventory`` for compact, ``validate_inventory`` with the
  catalogue images injected for v3).  A failure writes nothing.
* A public address is only ever offered for ``management_address``.  ``addresses``
  must hold the interface addresses of the guest, because the deployer's preflight
  asserts each declared address with ``ip -4 addr show`` on the target host.
* ``known_hosts`` is written per machine, never in ``defaults.ssh``: the compact
  resolver rejects ``defaults.ssh.known_hosts_file`` ("is not an overridable field")
  because the catalogue only declares user, port and key.
* The SSH host keys are collected with ``ssh-keyscan`` into a dedicated file, as the
  Lima generator does, because the executor connects with
  ``StrictHostKeyChecking=yes`` and there is no interactive prompt to accept a key.
* Placeholder values from the input (documentation ranges, ``localhost``,
  ``*.example.org``) are proposed as they are and reported, never silently replaced.

All prompts and progress go to stderr so ``--json`` keeps stdout machine-readable.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
PLACEHOLDER_HOST_SUFFIXES = (".example.org", ".example.com", ".invalid")


class InstantiationError(RuntimeError):
    """The document cannot be instantiated with the answers given."""


def _say(message: str = "") -> None:
    """Progress and prompts go to stderr; only results go to stdout."""
    print(message, file=sys.stderr)


def _deployment_v3():
    """Import the deployer's own validation code, so we never re-implement it."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from deployment_v3.catalogs import get_catalog
        from deployment_v3.cli import _inventory_changes
        from deployment_v3.inventory import InventoryError, validate_inventory
        from deployment_v3.inventory_resolver import resolve_inventory
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        raise InstantiationError(
            f"cannot import the deployer from {ROOT}: {exc}. "
            "Run this script with the repository virtualenv, for example "
            "venv/bin/python local-infra/instantiate-inventory.py ..."
        ) from exc
    return {
        "get_catalog": get_catalog,
        "inventory_changes": _inventory_changes,
        "InventoryError": InventoryError,
        "resolve_inventory": resolve_inventory,
        "validate_inventory": validate_inventory,
    }


# --------------------------------------------------------------------------------------
# Value checks
# --------------------------------------------------------------------------------------


def _is_documentation_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(address in network for network in DOCUMENTATION_NETWORKS)


def _is_placeholder_host(value: str) -> bool:
    lowered = value.lower()
    return lowered == "localhost" or lowered.endswith(PLACEHOLDER_HOST_SUFFIXES)


def _non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("a value is required")
    return value.strip()


def _identifier(value: str) -> str:
    cleaned = _non_empty(value)
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in cleaned):
        raise ValueError("use lowercase letters, digits and hyphens only")
    return cleaned


def _ipv4(value: str) -> str:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not an IPv4 address") from exc
    if parsed.version != 4:
        raise ValueError("it must be IPv4")
    return str(parsed)


def _port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("it must be a port number") from exc
    if not 1 <= parsed <= 65535:
        raise ValueError("the port must be between 1 and 65535")
    return parsed


def _absolute(value: str) -> str:
    if not value:
        raise ValueError("a path is required")
    if not Path(value).is_absolute():
        raise ValueError(f"{value!r} is not an absolute path")
    return value


def _hostname_or_ip(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        raise ValueError("it must be a host name or an IP address, without spaces")
    return value


def _origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
        raise ValueError("it must be an http(s) origin, for example https://dark.example.org")
    return value


def _cidr(value: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not a valid CIDR, for example 10.40.10.0/24") from exc
    if network.version != 4:
        raise ValueError("it must be IPv4")
    return str(network)


def _docker_pool(value: str, site_networks: list[ipaddress.IPv4Network]) -> str:
    pool = _cidr(value)
    if any(ipaddress.ip_network(pool).overlaps(network) for network in site_networks):
        raise ValueError("the Docker pool must not overlap the site CIDR")
    return pool


def _docker_prefix(value: str, pool: str) -> int:
    try:
        prefix = int(value)
    except ValueError as exc:
        raise ValueError("it must be a number") from exc
    if not 20 <= prefix <= 30:
        raise ValueError("the prefix must be between 20 and 30")
    if prefix < ipaddress.ip_network(pool).prefixlen:
        raise ValueError("the prefix cannot be wider than the pool")
    return prefix


# --------------------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------------------


class Prompter:
    """Ask a question, offering the proposed value, and validate the answer."""

    def __init__(self, *, accept_defaults: bool) -> None:
        self.accept_defaults = accept_defaults

    def ask(self, label: str, proposed: str, checker=None, *, note: str = "") -> str:
        if note:
            _say(f"  {note}")
        while True:
            if self.accept_defaults:
                value = proposed
            else:
                suffix = f" [{proposed}]" if proposed else ""
                raw = input(f"{label}{suffix}: ").strip()
                value = raw or proposed
            if checker is None:
                return value
            try:
                return checker(value)
            except ValueError as exc:
                if self.accept_defaults:
                    raise InstantiationError(f"{label}: {exc}") from exc
                _say(f"  {exc}")

    def ask_yes_no(self, label: str, proposed: bool) -> bool:
        proposed_text = "yes" if proposed else "no"
        while True:
            if self.accept_defaults:
                return proposed
            raw = input(f"{label} [y/n] ({proposed_text}): ").strip().lower()
            if not raw:
                return proposed
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no"}:
                return False
            _say("  answer yes or no")


# --------------------------------------------------------------------------------------
# Deriving the questions from the document
# --------------------------------------------------------------------------------------


def detect_format(document: dict) -> str:
    if document.get("format") == "dark-operator-inventory":
        return "compact"
    if document.get("version") == 3:
        return "v3"
    raise InstantiationError(
        "unsupported input: expected the compact 'dark-operator-inventory' format or a v3 document"
    )


def _networks_of(document: dict, definition: dict) -> list[str]:
    """Networks a machine must be reachable on, derived from the document.

    An explicit ``addresses`` map is the strongest signal.  Otherwise the networks
    named by ``routing`` are used, which is what a machine converted from ``local``
    needs in order to participate in cross-host traffic.
    """
    declared = list(definition.get("addresses") or {})
    if declared:
        return declared
    routing = document.get("routing") or {}
    ordered = [network for network in routing.values() if isinstance(network, str)]
    if ordered:
        return list(dict.fromkeys(ordered))
    return list(document.get("networks") or {})


def collect_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Instantiate any supported dARK inventory for a real site",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # rehearses the whole questionnaire and writes nothing
  venv/bin/python local-infra/instantiate-inventory.py \\
      --input examples/operator-inventory/production-five-host.json \\
      --output deployment-aws.json --dry-run

  venv/bin/python local-infra/instantiate-inventory.py \\
      --input examples/operator-inventory/production-five-host.json \\
      --output deployment-aws.json

  # no questions: accept every proposed value, then validate and write
  venv/bin/python local-infra/instantiate-inventory.py \\
      --input examples/operator-inventory/production-five-host.json \\
      --output /tmp/check.json --defaults-only
""",
    )
    parser.add_argument("--input", required=True, type=Path, help="source inventory (compact or v3)")
    parser.add_argument("--output", required=True, type=Path, help="instantiated inventory to write")
    parser.add_argument("--known-hosts", type=Path, help="path of the known_hosts file to generate")
    parser.add_argument("--skip-keyscan", action="store_true", help="do not collect host keys over SSH")
    parser.add_argument(
        "--defaults-only",
        action="store_true",
        help="ask nothing: accept every proposed value (implies --skip-keyscan)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="allow writing over an existing file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="rehearse the questions and validate, but write neither inventory nor known_hosts",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    return parser.parse_args()


def instantiate(
    document: dict, fmt: str, prompt: Prompter, *, output: Path, known_hosts: Path
) -> tuple[dict, list[str]]:
    notes: list[str] = []
    result = deepcopy(document)

    networks: dict = result.get("networks") or {}
    if not networks:
        raise InstantiationError("the input declares no networks")
    machines: dict = result.get("machines") or {}
    if not machines:
        raise InstantiationError("the input declares no machines")

    # --- deployment identity -----------------------------------------------------------
    _say("\nDeployment identity")
    deployment = result.setdefault("deployment", {})
    deployment["id"] = prompt.ask(
        "Deployment identifier", str(deployment.get("id") or ""), _identifier
    )
    proposed_label = str(deployment.get("label") or "").strip()
    if proposed_label.upper().startswith("REPLACE"):
        # "REPLACE: dARK five-host production deployment" -> a usable, editable label.
        proposed_label = proposed_label.split(":", 1)[1].strip() if ":" in proposed_label else ""
    deployment["label"] = prompt.ask(
        "Human-readable label", proposed_label or f"dARK deployment {deployment['id']}", _non_empty
    )

    # --- machines declared local -------------------------------------------------------
    local_ids = sorted(
        machine_id for machine_id, item in machines.items() if item.get("execution") == "local"
    )
    if local_ids:
        convert = prompt.ask_yes_no(
            "The inventory declares "
            f"{', '.join(local_ids)} as local. Are they remote machines reached over SSH at this site?",
            False,
        )
        if convert:
            for machine_id in local_ids:
                machines[machine_id]["execution"] = "ssh"
    remote = {
        machine_id: item for machine_id, item in machines.items() if item.get("execution") != "local"
    }
    if not remote:
        notes.append("The inventory declares no remote machine: no connection data was needed.")

    # --- network model -----------------------------------------------------------------
    network_ids = list(networks)
    single_network_model = True
    if len(network_ids) > 1:
        _say("\nNetwork model")
        _say(f"  The inventory declares {len(network_ids)} logical domains: {', '.join(network_ids)}.")
        single_network_model = prompt.ask_yes_no(
            "Does the site have a single routable network? (the same private address in every domain)",
            True,
        )
    _say("\nNetworks")
    site_networks: list[ipaddress.IPv4Network] = []
    shared_cidr: str | None = None
    for network_id, raw_definition in networks.items():
        definition = dict(raw_definition)
        if single_network_model and shared_cidr is not None:
            definition.pop("cidrs", None)
            definition["cidr"] = shared_cidr
            networks[network_id] = definition
            _say(f"  {network_id}: {shared_cidr} (shared)")
            site_networks.append(ipaddress.ip_network(shared_cidr))
            continue
        candidates = definition.get("cidrs") or [definition.get("cidr") or ""]
        proposed = str(candidates[0] or "")
        placeholder = _is_documentation_address(proposed.rsplit("/", 1)[0]) if proposed else False
        label = f'CIDR of "{network_id}"' + (
            " (applied to the remaining domains)" if single_network_model else ""
        )
        value = prompt.ask(
            label,
            proposed,
            _cidr,
            note="this is the example's documentation value; it must be replaced" if placeholder else "",
        )
        definition.pop("cidrs", None)
        definition["cidr"] = value
        networks[network_id] = definition
        site_networks.append(ipaddress.ip_network(value))
        if single_network_model:
            shared_cidr = value
    for network in site_networks:
        if any(network.overlaps(item) for item in DOCUMENTATION_NETWORKS):
            notes.append(
                f"Network {network} is reserved for documentation; do not use it in a real site."
            )

    # --- docker subnet pool -----------------------------------------------------------
    defaults: dict = result.setdefault("defaults", {})
    docker = dict(defaults.get("docker") or {})
    pool = str(docker.get("subnet_pool") or "172.30.0.0/16")
    prefix = int(docker.get("subnet_prefix") or 24)
    if any(ipaddress.ip_network(pool).overlaps(network) for network in site_networks):
        _say("\nDocker subnets")
        _say(f"  The proposed pool {pool} overlaps the site CIDR.")
        pool = prompt.ask(
            "Docker subnet pool", "172.30.0.0/16", lambda value: _docker_pool(value, site_networks)
        )
        prefix = prompt.ask("Prefix per machine", str(prefix), lambda value: _docker_prefix(value, pool))
    docker["subnet_pool"] = pool
    docker["subnet_prefix"] = prefix
    defaults["docker"] = docker

    # --- shared connection data --------------------------------------------------------
    if remote:
        _say("\nConnection data (proposed for every machine)")
        shared_ssh = dict(defaults.get("ssh") or {})
        proposed_user = str(shared_ssh.get("user") or "dark")
        shared_user = prompt.ask(
            "SSH user",
            proposed_user,
            _non_empty,
            note='on an Ubuntu or Debian AMI this is usually "ubuntu" or "admin"'
            if proposed_user == "dark"
            else "",
        )
        shared_port = prompt.ask("SSH port", str(shared_ssh.get("port") or 22), _port)
        proposed_key = str(shared_ssh.get("private_key_file") or "")
        if not proposed_key or "REPLACE" in proposed_key:
            proposed_key = str(Path.home() / ".ssh" / "id_ed25519")
        shared_key = prompt.ask("SSH private key", proposed_key, _absolute)
        shared_paths = dict(defaults.get("paths") or {})
        _say("\nPaths on every machine")
        shared_paths = {
            "workspace_root": prompt.ask(
                "workspace_root", str(shared_paths.get("workspace_root") or "/srv/dark"), _absolute
            ),
            "data_root": prompt.ask(
                "data_root", str(shared_paths.get("data_root") or "/srv/dark/data"), _absolute
            ),
            "secrets_root": prompt.ask(
                "secrets_root", str(shared_paths.get("secrets_root") or "/srv/dark/secrets"), _absolute
            ),
        }
        defaults["ssh"] = {"user": shared_user, "port": shared_port, "private_key_file": shared_key}
        if "paths" in defaults:
            defaults["paths"] = dict(shared_paths)

        _say("\n  The known_hosts file is written per machine: the compact format rejects")
        _say("  defaults.ssh.known_hosts_file. And a public address or a load-balancer name")
        _say('  can only go in "management address": interface addresses do not allow NAT.')

        for machine_id, definition in remote.items():
            _say(f'\nMachine "{machine_id}"')
            wanted = _networks_of(result, definition)
            if not wanted:
                raise InstantiationError(f"{machine_id}: the document declares no network for this host")
            addresses: dict[str, str] = {}
            if single_network_model:
                proposed = str((definition.get("addresses") or {}).get(wanted[0], ""))
                address = prompt.ask(
                    f"Private address (applied to {', '.join(wanted)})",
                    proposed,
                    _ipv4,
                    note="this is the example's documentation value"
                    if _is_documentation_address(proposed)
                    else "",
                )
                for network_id in wanted:
                    addresses[network_id] = address
            else:
                for network_id in wanted:
                    proposed = str((definition.get("addresses") or {}).get(network_id, ""))
                    addresses[network_id] = prompt.ask(
                        f'Private address on "{network_id}"',
                        proposed,
                        _ipv4,
                        note="this is the example's documentation value"
                        if _is_documentation_address(proposed)
                        else "",
                    )
            for network_id, address in addresses.items():
                if network_id not in networks:
                    raise InstantiationError(f'{machine_id}: network "{network_id}" is not declared')
                allowed = networks[network_id].get("cidrs") or [networks[network_id]["cidr"]]
                if not any(ipaddress.ip_address(address) in ipaddress.ip_network(item) for item in allowed):
                    raise InstantiationError(
                        f'{machine_id}: {address} is not inside network "{network_id}"'
                    )
            definition["addresses"] = addresses
            for address in addresses.values():
                if _is_documentation_address(address):
                    notes.append(f"{machine_id}: {address} is a documentation range.")
            # A placeholder is replaced by the address just entered, which is the right
            # answer for a flat network.  A real value (for example the Lima lab's
            # forwarded 127.0.0.1) is kept as the proposal.
            proposed_management = str(definition.get("management_address") or "")
            if (
                not proposed_management
                or _is_documentation_address(proposed_management)
                or _is_placeholder_host(proposed_management)
            ):
                proposed_management = addresses[wanted[0]]
            definition["management_address"] = prompt.ask(
                "Management address for SSH",
                proposed_management,
                _hostname_or_ip,
                note="defaults to the private address itself; change it if the controller "
                "connects through a public address, a bastion or a DNS name",
            )
            if definition["management_address"] not in addresses.values():
                notes.append(
                    f"{machine_id}: SSH will go to {definition['management_address']} while services "
                    f"use {', '.join(sorted(set(addresses.values())))}. Check that this address "
                    "is reachable from the controller and that the Security Group restricts it."
                )
            definition["ssh"] = {
                "user": prompt.ask("SSH user", shared_user, _non_empty),
                "port": prompt.ask("SSH port", str(shared_port), _port),
                "private_key_file": prompt.ask("Private key", shared_key, _absolute),
                "known_hosts_file": str(known_hosts),
            }
            definition["paths"] = {
                "workspace_root": prompt.ask("workspace_root", shared_paths["workspace_root"], _absolute),
                "data_root": prompt.ask("data_root", shared_paths["data_root"], _absolute),
                "secrets_root": prompt.ask("secrets_root", shared_paths["secrets_root"], _absolute),
            }

    # --- public entry points -----------------------------------------------------------
    proxies: dict = result.get("proxies") or {}
    if proxies:
        _say("\nPublic entry points")
        for proxy_id, proxy in proxies.items():
            listener = proxy.get("listener") or {}
            for site in proxy.get("sites") or []:
                proposed_host = str(site.get("host") or "")
                site["host"] = prompt.ask(
                    f'Public host for "{proxy_id}"',
                    proposed_host,
                    _hostname_or_ip,
                    note="the inventory's example host; replace it with the real name"
                    if _is_placeholder_host(proposed_host)
                    else "",
                )
                # The validator rejects a public_origin whose host differs from the site
                # host, so a changed host must carry its origin with it instead of
                # leaving the template's placeholder behind.
                template_origin = str(site.get("public_origin") or "")
                scheme = urlparse(template_origin).scheme or "https"
                unchanged = site["host"] == proposed_host
                if template_origin and unchanged and not _is_placeholder_host(site["host"]):
                    proposed_origin = template_origin
                else:
                    proposed_origin = f"{scheme}://{site['host']}"
                site["public_origin"] = prompt.ask(
                    f'Public origin for "{proxy_id}"', proposed_origin, _origin
                )
                if _is_placeholder_host(urlparse(site["public_origin"]).hostname or ""):
                    notes.append(
                        f"{proxy_id}: {site['public_origin']} is still an example name; "
                        "the dashboard redirects use this value."
                    )
                if listener.get("bind") == "public":
                    notes.append(
                        f"{proxy_id}: listens on all interfaces on port {listener.get('port')}. "
                        "Restrict access in the Security Group, for example allowing only the load "
                        "balancer."
                    )
    if (result.get("access") or {}).get("mode") in {"gateway", "local-direct"}:
        notes.append(
            "The inventory uses the legacy \"access\" field; check that its bind and port "
            "match your site."
        )

    # --- controller-side private material ---------------------------------------------
    _say("\nController-side private material")
    omit = prompt.ask_yes_no(
        "Omit the secret and chain-artifact sources? "
        "(install generates the managed secrets)",
        True,
    )
    if omit:
        if fmt == "compact":
            result.pop("secrets", None)
        else:
            for definition in (result.get("secrets") or {}).values():
                definition.pop("source", None)
        artifact = (result.get("blockchain") or {}).get("artifact")
        if isinstance(artifact, dict):
            artifact.pop("source", None)
        notes.append(
            "With no declared sources, install generates the managed secrets; for an existing chain "
            "you must supply the artifact with --chain-artifact."
        )
    else:
        root = prompt.ask("Secret root on the controller", "", _absolute)
        artifact_root = prompt.ask(
            "Chain artifact root", str(Path(root) / "chain-artifact"), _absolute
        )
        if fmt == "compact":
            result["secrets"] = {"source_root": root}
        else:
            for definition in (result.get("secrets") or {}).values():
                definition["source"] = str(Path(root) / definition["path"])
        result.setdefault("blockchain", {}).setdefault("artifact", {})["source"] = artifact_root

    return result, notes


# --------------------------------------------------------------------------------------
# Validation and output
# --------------------------------------------------------------------------------------


def validate(document: dict, fmt: str, *, output: Path) -> dict:
    """Validate through the deployer's own code path and return the resolved document."""
    api = _deployment_v3()
    if fmt == "compact":
        return api["resolve_inventory"](document, source_path=output).document
    # A v3 document only becomes valid with the catalogue images present, exactly as
    # load_inventory does it.  They are injected for validation only: the written file
    # keeps the repository convention of omitting the block.
    candidate = deepcopy(document)
    candidate["images"] = dict(api["get_catalog"]("dark-platform-baseline-v1.0").document["images"])
    candidate.get("blockchain", {}).pop("besu_image", None)
    api["validate_inventory"](candidate)
    return candidate


def resolved_document(document: dict, fmt: str, *, path: Path) -> dict:
    api = _deployment_v3()
    if fmt == "compact":
        return api["resolve_inventory"](document, source_path=path).document
    candidate = deepcopy(document)
    candidate["images"] = dict(api["get_catalog"]("dark-platform-baseline-v1.0").document["images"])
    api["validate_inventory"](candidate)
    return candidate


def collect_host_keys(targets: list[tuple[str, int]], destination: Path) -> list[str]:
    """Write a dedicated known_hosts, as the Lima lab does."""
    problems: list[str] = []
    collected: list[str] = []
    for host, port in targets:
        scanned = subprocess.run(
            ["ssh-keyscan", "-T", "5", "-p", str(port), host], capture_output=True, text=True
        )
        if scanned.returncode or not scanned.stdout.strip():
            problems.append(f"{host}:{port} ({scanned.stderr.strip() or 'no answer'})")
            continue
        collected.append(scanned.stdout)
    if collected:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("".join(collected), encoding="utf-8")
    return problems


def main() -> int:
    args = collect_args()
    if args.defaults_only and not args.skip_keyscan:
        args.skip_keyscan = True
        _say("[INFO] --defaults-only implies --skip-keyscan")
    if args.dry_run and not args.skip_keyscan:
        args.skip_keyscan = True
        _say("[INFO] --dry-run implies --skip-keyscan")
    source = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_file():
        print(f"[ERROR] source inventory not found: {source}", file=sys.stderr)
        return 2
    if output.exists() and not args.overwrite and not args.dry_run:
        print(f"[ERROR] refusing to overwrite existing inventory: {output}", file=sys.stderr)
        print("        pass --overwrite if that is what you want", file=sys.stderr)
        return 2

    document = json.loads(source.read_text(encoding="utf-8"))
    fmt = detect_format(document)
    known_hosts = (
        args.known_hosts.expanduser().resolve()
        if args.known_hosts
        else output.with_name(output.stem + "-known_hosts")
    )
    _say(f"[INFO] {source.name}: {'compact' if fmt == 'compact' else 'v3'} format")

    prompt = Prompter(accept_defaults=args.defaults_only)
    try:
        candidate, notes = instantiate(document, fmt, prompt, output=output, known_hosts=known_hosts)
    except InstantiationError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\n[INFO] cancelled; nothing was written", file=sys.stderr)
        return 130

    try:
        resolved = validate(candidate, fmt, output=output)
    except Exception as exc:  # InventoryError subclasses, and anything the resolver raises
        print(f"[ERROR] the resulting inventory does not validate: {exc}", file=sys.stderr)
        print("        nothing was written", file=sys.stderr)
        return 2

    keyscan_problems: list[str] = []
    skipped_targets: list[str] = []
    if not args.skip_keyscan:
        all_targets = sorted(
            {
                (str(item["management_address"]), int((item.get("ssh") or {}).get("port") or 22))
                for item in (candidate.get("machines") or {}).values()
                if (item.get("ssh") or {}).get("known_hosts_file")
            }
        )
        # Scanning a documentation range or a placeholder name only wastes the timeout
        # and prints a misleading failure; the operator has not filled them in yet.
        targets = [
            item
            for item in all_targets
            if not _is_documentation_address(item[0]) and not _is_placeholder_host(item[0])
        ]
        skipped_targets = [
            f"{host}:{port}"
            for host, port in all_targets
            if _is_documentation_address(host) or _is_placeholder_host(host)
        ]
        _say(f"\n[INFO] collecting host keys from {len(targets)} host(s) into {known_hosts.name}")
        if skipped_targets:
            _say(f"[INFO] skipped as example values: {', '.join(skipped_targets)}")
        keyscan_problems = collect_host_keys(targets, known_hosts)

    if not args.dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")

    changes = _deployment_v3()["inventory_changes"](
        resolved_document(document, fmt, path=source), resolved
    )
    summary = {
        "input": str(source),
        "output": str(output),
        "written": not args.dry_run,
        "format": fmt,
        "known_hosts": str(known_hosts) if not args.skip_keyscan else None,
        "machines": sorted((candidate.get("machines") or {})),
        "changed_sections": changes.get("changed_sections") or [],
        "changes": changes,
        "notes": notes,
        "keyscan_problems": keyscan_problems,
        "keyscan_skipped": skipped_targets,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if args.dry_run:
        print(
            "\n[OK] DRY RUN: the inventory resolves and validates; nothing was written "
            f"(target was {output})"
        )
    else:
        print(f"\n[OK] Inventory written to {output}")
    if skipped_targets:
        print(f"[INFO] Not scanned (still example values): {', '.join(skipped_targets)}")
    if not args.skip_keyscan:
        if keyscan_problems:
            _say("[WARN] not every host key could be read:")
            for problem in keyscan_problems:
                _say(f"       {problem}")
            _say("[WARN] run the script again once the hosts answer, or build the file by hand.")
        elif known_hosts.exists():
            print(f"[OK] Host keys in {known_hosts}")
    if changes.get("changed_sections"):
        print(
            "[INFO] Sections that change with respect to the source inventory: "
            f"{', '.join(changes['changed_sections'])}"
        )
    for note in notes:
        print(f"[NOTE] {note}")
    print(
        "\nReview the result and then:\n"
        f"  venv/bin/python deploy.py validate --inventory {output}\n"
        f"  venv/bin/python deploy.py plan --inventory {output}\n"
        f"  venv/bin/python deploy.py inventory-explain --inventory {output} --path /services/store-api"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
