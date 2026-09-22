"""Compose generation by explicit service instance and machine."""

from __future__ import annotations

from .model import DeploymentPlan, Machine, ServiceInstance
from .network import has_remote_peer, host_bind_address, listener_bind_address, internal_port, chain_node_addresses, chain_node_ports, p2p_bindings, p2p_edges, docker_lab_address


def _build(source_root: str, dockerfile: str) -> dict:
    return {"context": source_root, "dockerfile": dockerfile}


def _ports(machine: Machine, service: ServiceInstance) -> list[str]:
    result = []
    # Private listeners in docker-lab are container-to-container only.  The
    # gateway may still publish its intentional loopback/public listener.
    if machine.execution == "docker-lab":
        if service.exposure and service.exposure.get("mode") in {"loopback", "public"}:
            bind = host_bind_address(machine, service.exposure)
            protocols = service.exposure.get("protocols", ["tcp"])
            return [f"{bind}:{service.exposure['port']}:{internal_port(service)}/{protocol}" for protocol in protocols]
        return result
    bind = host_bind_address(machine, service.exposure)
    if bind is not None:
        protocols = service.exposure.get("protocols", ["tcp"])
        result.extend(f"{bind}:{service.exposure['port']}:{internal_port(service)}/{protocol}" for protocol in protocols)
    result.extend(
        f"{listener_bind_address(machine, listener)}:{listener['port']}:{internal_port(service)}/{protocol}"
        for listener in service.listeners
        for protocol in listener["protocols"]
    )
    return result


def _ipfs_network_ports(plan: DeploymentPlan, machine: Machine, service: ServiceInstance) -> list[str]:
    """Publish storage API and P2P ports on every selected peer network.

    Bootstrap discovery first calls the provider API to obtain its PeerID and
    then dials the advertised P2P port. A provider can therefore need its API
    on a different network from its first/local consumer (for example LAN for
    site-local traffic and VPN for a remote site).
    """
    if machine.execution == "docker-lab":
        return []
    peer_type = "ipfs-kubo" if service.type == "ipfs-kubo" else "ipfs-cluster"
    if not has_remote_peer(plan, {peer_type}):
        return []
    policy = service.configuration["_infrastructure"]
    family = "ipfs" if service.type == "ipfs-kubo" else "cluster"
    incoming_networks = sorted({edge["network"] for edge in p2p_edges(plan, family, service.id)})
    result = []
    if service.type == "ipfs-kubo":
        port = int(service.configuration.get("p2p_advertise_port", policy["swarm_port"]))
        result.extend(f"{bind}:{bound_port}:4001/{protocol}" for bind, bound_port, protocol in p2p_bindings(plan, "ipfs", service.id, port, ("tcp", "udp")))
        api_port = int(policy["api_port"])
    else:
        port = int(service.configuration.get("p2p_advertise_port", policy["p2p_port"]))
        result.extend(f"{bind}:{bound_port}:9096/{protocol}" for bind, bound_port, protocol in p2p_bindings(plan, "cluster", service.id, port, ("tcp",)))
        api_port = int(policy["api_port"])
    existing = set(result)
    declared_networks = {listener["network"] for listener in service.listeners}
    if service.exposure and service.exposure.get("mode") == "private":
        declared_networks.add(service.exposure["network"])
    for network in incoming_networks:
        if network in declared_networks:
            continue
        value = f"{machine.address_on(network)}:{api_port}:{api_port}/tcp"
        if value not in existing:
            result.append(value)
    return result


def _p2p_ports(plan: DeploymentPlan, machine: Machine, service: ServiceInstance) -> list[str]:
    """Publish Besu P2P only where it crosses host boundaries.

    Every container listens on 30303 internally; host ports are deterministic
    from the generated chain artifact and are never an Internet-facing RPC.
    """
    if machine.execution == "docker-lab" or not has_remote_peer(plan, {"besu-rpc", "besu-validator", "besu-observer"}):
        return []
    node_id = service.configuration["node_id"]
    host_port = chain_node_ports(plan)[node_id]
    return [f"{bind}:{bound_port}:30303/{protocol}" for bind, bound_port, protocol in p2p_bindings(plan, "blockchain", service.id, host_port, ("tcp", "udp"))]


def compose_document(plan: DeploymentPlan, machine: Machine, services: tuple[ServiceInstance, ...], group_id: str | None = None) -> dict:
    source_root = "../../sources/components" if group_id else "./sources/components"
    runtime_root = "../../runtime" if group_id else "./runtime"
    network = f"{plan.deployment_id}-{machine.id}"
    data = f"{machine.data_root}/{plan.deployment_id}"
    chain_artifact = f"{machine.secrets_root}/{plan.raw['blockchain']['artifact']['path']}"
    secret_path = lambda secret_id: f"{machine.secrets_root}/{plan.raw['secrets'][secret_id]['path']}"
    common = {"restart": "unless-stopped", "networks": [network]}
    images = plan.raw["images"]
    result: dict = {}
    besu_count = sum(item.type in {"besu-validator", "besu-rpc", "besu-observer"} for item in plan.services)
    sync_min_peers = min(3, besu_count - 1) if besu_count > 1 else None
    for service in services:
        # Keep generated service instances immutable from the inventory while
        # giving the low-level port helper the policy it must expose.
        if service.type == "ipfs-kubo":
            service = ServiceInstance(service.id, service.type, service.machine_id, service.connections, {**service.configuration, "_infrastructure": plan.raw["infrastructure"]["ipfs"]}, service.exposure, service.listeners)
        elif service.type == "ipfs-cluster":
            service = ServiceInstance(service.id, service.type, service.machine_id, service.connections, {**service.configuration, "_infrastructure": plan.raw["infrastructure"]["cluster"]}, service.exposure, service.listeners)
        # Compose applies later env files over earlier ones.  Keep generated
        # public defaults first and private runtime credentials last so the
        # dashboard and its database share the provisioned password.
        env = [f"./env/{service.id}.env"]
        service_data = f"{data}/{service.id}"
        if service.type in {"minter-api", "minter-worker", "admin-api", "resolver-api"}:
            env.append(f"{data}/contracts/contracts.env")
        if service.type in {"minter-api", "minter-worker", "minter-migrate", "minter-postgres", "admin-api"}:
            env.append(secret_path("minter-runtime-env"))
        if service.type in {"dashboard", "dashboard-migrate", "dashboard-mysql"}:
            env.append(secret_path("dashboard-runtime-env"))
        if service.type == "besu-rpc":
            node = service.configuration["node_id"]
            command = ["--config-file=/config/besu-config.toml", "--genesis-file=/config/genesis.json", "--node-private-key-file=/config/nodekey", "--static-nodes-file=/config/static-nodes.json", "--rpc-http-enabled=true", "--rpc-http-host=0.0.0.0", "--p2p-port=30303"]
            if sync_min_peers is not None: command.append(f"--sync-min-peers={sync_min_peers}")
            result[service.id] = {**common, "image": images["besu"], "env_file": env, "ports": [*_ports(machine, service), *_p2p_ports(plan, machine, service)], "volumes": [f"{service_data}:/data", f"{runtime_root}/blockchain/scripts/besu-entrypoint.sh:/usr/local/bin/dark-besu-entrypoint.sh:ro", f"{runtime_root}/blockchain/config/besu-config.toml:/config/besu-config.toml:ro", f"{chain_artifact}/genesis.json:/config/genesis.json:ro", f"{chain_artifact}/nodes/{node}/nodekey:/config/nodekey:ro", f"{chain_artifact}/nodes/{node}/static-nodes.json:/config/static-nodes.json:ro"], "entrypoint": ["/usr/local/bin/dark-besu-entrypoint.sh"], "command": command}
            if not has_remote_peer(plan, {"besu-rpc", "besu-validator", "besu-observer"}): result[service.id]["networks"] = {network: {"ipv4_address": chain_node_addresses(plan)[node]}}
        elif service.type in {"besu-validator", "besu-observer"}:
            node = service.configuration["node_id"]
            command = ["--config-file=/config/besu-config.toml", "--genesis-file=/config/genesis.json", "--node-private-key-file=/config/nodekey", "--static-nodes-file=/config/static-nodes.json", "--rpc-http-enabled=" + ("true" if service.type == "besu-observer" else "false"), "--p2p-port=30303"]
            if service.type == "besu-observer": command.append("--rpc-http-host=0.0.0.0")
            if sync_min_peers is not None: command.append(f"--sync-min-peers={sync_min_peers}")
            result[service.id] = {**common, "image": images["besu"], "env_file": env, "ports": [*_ports(machine, service), *_p2p_ports(plan, machine, service)], "volumes": [f"{service_data}:/data", f"{runtime_root}/blockchain/scripts/besu-entrypoint.sh:/usr/local/bin/dark-besu-entrypoint.sh:ro", f"{runtime_root}/blockchain/config/besu-config.toml:/config/besu-config.toml:ro", f"{chain_artifact}/genesis.json:/config/genesis.json:ro", f"{chain_artifact}/nodes/{node}/nodekey:/config/nodekey:ro", f"{chain_artifact}/nodes/{node}/static-nodes.json:/config/static-nodes.json:ro"], "entrypoint": ["/usr/local/bin/dark-besu-entrypoint.sh"], "command": command}
            if not has_remote_peer(plan, {"besu-rpc", "besu-validator", "besu-observer"}): result[service.id]["networks"] = {network: {"ipv4_address": chain_node_addresses(plan)[node]}}
        elif service.type == "minter-postgres":
            result[service.id] = {**common, "image": images["postgres"], "env_file": env, "volumes": [f"{service_data}:/var/lib/postgresql/data"]}
        elif service.type in {"minter-api", "minter-worker", "minter-migrate"}:
            command = [] if service.type == "minter-api" else ["migrate"] if service.type == "minter-migrate" else [f"{service.configuration['worker']}-worker"]
            result[service.id] = {**common, "build": _build(source_root, "dark-core-minter-api/Dockerfile"), "env_file": env, "command": command, "volumes": [f"{data}/minter-metadata:/app/metadata_storage"]}
            if service.type == "minter-api":
                result[service.id]["ports"] = _ports(machine, service)
        elif service.type == "admin-api":
            result[service.id] = {**common, "build": _build(source_root, "dark-core-admin-api/Dockerfile"), "env_file": env, "ports": _ports(machine, service)}
        elif service.type == "resolver-api":
            result[service.id] = {**common, "build": _build(source_root, "dark-core-resolver-api/Dockerfile"), "env_file": env, "ports": _ports(machine, service)}
        elif service.type == "store-api":
            result[service.id] = {**common, "build": _build(source_root, "dark-store-api/Dockerfile"), "env_file": env, "ports": _ports(machine, service), "volumes": ["./config/storage-endpoints.json:/config/storage-endpoints.json:ro"]}
        elif service.type == "dashboard-mysql":
            result[service.id] = {**common, "image": images["mysql"], "env_file": env, "volumes": [f"{service_data}:/var/lib/mysql"]}
        elif service.type in {"dashboard", "dashboard-migrate"}:
            # Composer runs as the image's unprivileged ``ambientum`` user.
            # Keep its managed dependencies outside the synced component
            # checkout so a host-owned development vendor tree cannot block
            # production-style ``composer install --no-dev``.
            dashboard_runtime = f"{data}/dashboard"
            item = {
                **common,
                "image": images["dashboard"],
                "env_file": env,
                "volumes": [
                    f"{source_root}/dashboard-web:/var/www/app",
                    f"{dashboard_runtime}/vendor:/var/www/app/vendor",
                    f"{dashboard_runtime}/storage:/var/www/app/storage",
                    f"{dashboard_runtime}/bootstrap-cache:/var/www/app/bootstrap/cache",
                ],
            }
            if service.type == "dashboard": item["ports"] = _ports(machine, service)
            else: item["command"] = ["sh", "-lc", "cd /var/www/app && php composer.phar install --no-interaction --prefer-dist --no-dev && php artisan optimize:clear && php artisan migrate --force --seed"]
            result[service.id] = item
        elif service.type == "ipfs-kubo":
            result[service.id] = {**common, "image": images["kubo"], "env_file": env, "environment": {"IPFS_TELEMETRY": "off"}, "ports": [*_ports(machine, service), *_ipfs_network_ports(plan, machine, service)], "volumes": [f"{source_root}/dark-ipfs/scripts/ipfs-entrypoint.sh:/usr/local/bin/ipfs-entrypoint.sh:ro", f"{service_data}:/data/ipfs", f"{secret_path('ipfs-swarm-key')}:/run/secrets/ipfs-swarm-key:ro"], "entrypoint": ["/usr/local/bin/ipfs-entrypoint.sh"]}
        elif service.type == "ipfs-cluster":
            result[service.id] = {**common, "image": images["ipfs_cluster"], "env_file": env, "ports": [*_ports(machine, service), *_ipfs_network_ports(plan, machine, service)], "volumes": [f"{source_root}/dark-ipfs/scripts/cluster-entrypoint.sh:/usr/local/bin/cluster-entrypoint.sh:ro", f"{service_data}:/data/ipfs-cluster", f"{secret_path('ipfs-cluster-secret')}:/run/secrets/ipfs-cluster-secret:ro"], "entrypoint": ["/usr/local/bin/cluster-entrypoint.sh"]}
        elif service.type == "explorer":
            result[service.id] = {**common, "build": {"context": f"{source_root}/dark-explorador", "dockerfile": "Dockerfile"}, "env_file": env, "ports": _ports(machine, service)}
        elif service.type == "edge-proxy":
            volumes = [f"./config/nginx-{service.id}.conf:/etc/nginx/conf.d/default.conf:ro"]
            tls = service.configuration.get("tls", {})
            if tls.get("mode") == "direct":
                for secret_id in (tls["certificate_secret"], tls["key_secret"]):
                    volumes.append(f"{secret_path(secret_id)}:/run/secrets/{secret_id}:ro")
            result[service.id] = {**common, "image": images["edge_proxy"], "ports": _ports(machine, service), "volumes": volumes}
        elif service.type in {"contracts-deploy", "rpc-probe"}:
            result[service.id] = {**common, "profiles": ["setup"], "build": {"context": runtime_root, "dockerfile": "deployment_v3/Dockerfile.contracts"}, "env_file": env, "volumes": [f"{secret_path('contract-signer')}:/run/secrets/contract-signer:ro", "./artifacts/contracts:/contracts:ro", f"{data}/contracts:/runtime"]}
        result[service.id]["labels"] = {
            "org.dark.deployment.id": plan.deployment_id,
            "org.dark.service.id": service.id,
        }
    networks = {network: {"name": network, "external": True}}
    if machine.execution == "docker-lab":
        for network_id in machine.addresses:
            physical = f"{plan.deployment_id}-{network_id}"
            networks[physical] = {"name": physical, "external": True}
        for service_id, item in result.items():
            item["networks"] = {
                network: {},
                **{
                    f"{plan.deployment_id}-{network_id}": {
                        "aliases": [f"{service_id}-{network_id}"],
                        "ipv4_address": docker_lab_address(plan, plan.service(service_id), network_id),
                    }
                    for network_id in machine.addresses
                },
            }
    project = f"{plan.deployment_id}-{machine.id}" + (f"-{group_id}" if group_id else "")
    return {"name": project, "services": result, "networks": networks}
