# dARK Operations Manual

This is the practical guide for installing, inspecting, operating, and troubleshooting dARK. Start here after reading the short repository overview in [README.md](README.md). Use the supported `deploy.py` interface; do not edit generated Compose files or `.env.integration` files by hand.

## 1. Choose an inventory path

An **inventory** is the source of truth for an installation: machines, placement, networks, components, secrets, blockchain, storage, and public proxies. There are two supported authoring paths.

| Path | Start with | Best for | Trade-off |
| --- | --- | --- | --- |
| Compact operator inventory | `examples/operator-inventory/` | New local and standard five-host deployments | Uses the supported dARK catalogue shape |
| Complete v3 inventory | `examples/deployment-v3/` | Existing v3 installations or a shape outside the catalogue | More fields and relationships to maintain |

The compact format records operator choices. The resolver derives services, typed dependencies, secret consumers, groups, and private exposures, then validates the resulting v3 inventory. The v3 format is already the complete execution contract. Both formats contain secret *references*, never values.

Use a maintained compact inventory directly for a new deployment:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json \
  --verbose
```

The installer expands the compact file into the complete v3 execution
contract internally. `inventory-create` is optional and only makes an editable
copy of a template; `inventory-resolve`, `plan` and `render` are optional
review commands, not prerequisites.

Available templates are `operator-local-simple`, `operator-local-observer`, `operator-local-ha`, `operator-production-five-host`, and `operator-production-six-host`. `local-simple` has one storage peer. `local-observer` adds a Besu observer with Resolver bound to its private RPC. `local-ha` has two peers on one Docker host and tests replication, not host-loss tolerance. Production templates are documentation-only until every `REPLACE` value is replaced with real infrastructure values.

## 2. Prepare the controller

```bash
cd /Users/lmatas/source/dark-deployer
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
docker version
docker compose version
```

For remote deployment, the controller also needs SSH access to every declared machine and the private material named by `secrets` and `blockchain.artifact.source`. Keep these files outside Git. The deployer copies each secret only to its declared consumers with the declared mode (normally `0600`); public bundles never contain secret values.

## 3. Inspect before changing infrastructure

The compact inspection commands are offline: they do not use Docker, SSH, Git, or secret contents.

```bash
# Expand compact decisions into the effective v3 contract.
venv/bin/python deploy.py inventory-resolve \
  --inventory inventory.json \
  --output resolved-inventory.json

# Explain whether a field is explicit, inherited, or catalogue-derived.
venv/bin/python deploy.py inventory-explain \
  --inventory inventory.json \
  --path /services/store-api

# Compare two inventory decisions semantically.
venv/bin/python deploy.py inventory-diff \
  --before previous-inventory.json \
  --after inventory.json

# Validate and construct an execution plan without applying it.
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py plan --inventory inventory.json --json
```

`inventory-resolve` refuses to overwrite an existing output. `plan` is the last safe review point: examine group placement, cross-host routes, exposure, and the phases (`validators`, `rpc`, `contracts`, `storage`, `data`, `applications`, `verify`) before installing.

## 4. Configure a compact inventory

### Local functional test

The shortest supported local path is:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json --verbose
```

Keep `operator-local-simple` unchanged for the smallest functional installation. If a new local chain is needed, provide a master wallet file (the installer derives its public address and contract signer) or use the guarded wallet-generation option documented by `deploy.py --help`.

```bash
venv/bin/python deploy.py install \
  --inventory inventory.json \
  --master-wallet-file /secure/dark/master-wallet.key
```

### Local replication simulation

Create `operator-local-ha`. Its five logical groups run on one Docker daemon: `apps`, `blockchain-a`, `blockchain-b`, `storage-1`, and `storage-2`. It proves that two storage peers can replicate; it does not prove that the system survives loss of the single host.

### Standard five-host deployment

Start with `operator-production-five-host`, then replace all sample network addresses, SSH settings, paths, source references, public origins and proxy settings. The normal placement is apps, two blockchain hosts, and two storage hosts. Route traffic intentionally:

```json
{
  "routing": {
    "blockchain_p2p": "vpn",
    "storage_p2p": "vpn",
    "storage_api": "vpn",
    "application_api": "lan"
  },
  "proxies": {
    "gateway": {
      "group": "apps",
      "listener": {"bind": "public", "port": 80},
      "tls": {"mode": "external"},
      "sites": [{
        "host": "dark.example.org",
        "public_origin": "https://dark.example.org",
        "routes": [
          {"id": "resolver", "path": "/", "service": "resolver-api", "upstream_path": "/api/v1/arks/"},
          {"id": "dashboard", "path": "/admin/", "service": "dashboard", "upstream_path": "/"},
          {"id": "explorer", "path": "/explorer/", "service": "explorer", "upstream_path": "/"},
          {"id": "minter-v1", "path": "/api/v1/", "service": "minter-api", "upstream_path": "/api/v1/"},
          {"id": "minter-docs", "path": "/api/docs", "service": "minter-api", "upstream_path": "/docs"},
          {"id": "minter-openapi", "path": "/api/openapi.json", "service": "minter-api", "upstream_path": "/openapi.json"}
        ]
      }]
    }
  }
}
```

Same-machine consumers use Docker DNS. A cross-host dependency receives the selected network, TCP, and a derived private provider exposure. The resolver never publishes a dependency publicly merely to make a route work.

For a concrete EC2 deployment with five private hosts in one Availability
Zone, Security Groups, EBS volumes, controller access, acceptance and recovery,
see [the AWS single-zone guide](docs/aws-single-az-five-host.md).

### Storage and replication

Declare each logical peer once. The resolver creates its Kubo/Cluster pair and updates Store API relationships.

```json
{
  "storage": {
    "peers": {
      "storage-east": {"group": "storage-east"},
      "storage-west": {"group": "storage-west"}
    },
    "replication": {
      "publish_after_replicas": 1,
      "target_replicas": 2
    }
  }
}
```

`publish_after_replicas` is the availability threshold required before chain publication. `target_replicas` is later durability work. A target cannot exceed the number of peers.

### Controlled overrides

The compact catalogue supplies component defaults and settings. Use only the typed override areas it supports. For example, the Minter shoulder uses the `2xx` form, where each `x` is lowercase alphanumeric:

```json
{
  "overrides": {"settings": {"minter": {"shoulder": "200"}}}
}
```

Use the complete v3 inventory when the desired service layout is outside the catalogue's fixed four-validator, one-RPC, and one-or-two-peer model.

## 5. Render, preflight, install

### Runtime image versions

All managed runtime image versions have one source of truth: the `base.images`
block in `deployment_v3/catalog_data/dark-platform-baseline-v1.0.json`. It defines Besu,
Kubo, IPFS Cluster, PostgreSQL, MySQL, Redis, the Dashboard runtime and the edge
proxy. Neither the Compose renderer nor its auxiliary ownership/artifact tasks
contain fallback image versions; a missing catalogue key fails inventory
validation. Compact operator inventories inherit this block automatically, and
legacy full inventories are normalized to the same installed catalogue release.

To upgrade a runtime image, change the catalogue entry, run the focused tests,
render the target inventory and review every resulting `image:` field before
applying. Use an immutable digest for development/canary images when available.

For reviewable output without applying it:

```bash
venv/bin/python deploy.py render \
  --inventory inventory.json \
  --output /tmp/dark-render
```

The output includes plan evidence, one public bundle per machine, a firewall suggestion, and the resolved v3 inventory. Its retained path `shared/deployment-topology.json` is a legacy bundle filename, not the model name; `shared/inventory-resolution.json` records input and catalogue digests.

For remote machines, check prerequisites before mutation:

```bash
venv/bin/python deploy.py preflight --inventory inventory.json
venv/bin/python deploy.py push --inventory inventory.json
venv/bin/python deploy.py apply --inventory inventory.json
```

`install` coordinates validation, acquisition, runtime secret preparation, chain-artifact handling, preflight, rendering, application, and verification. It acquires components with `fetch` and `merge --ff-only`; a divergent or dirty checkout stops rather than being overwritten. Use `--skip-acquire` only for checkouts you intentionally prepared.

```bash
venv/bin/python deploy.py install --inventory inventory.json
```

For a new chain, use `chain-init` only as an explicit initialization action. Do not regenerate genesis, validator identities, or static nodes for an existing network. See [blockchain/README.md](blockchain/README.md) for the role-artifact workflow.

## 6. Verify and operate

```bash
venv/bin/python deploy.py status --inventory inventory.json
venv/bin/python deploy.py verify --inventory inventory.json
```

Confirm block production, the expected Besu nodes, storage peers, API health, and the three Minter workers. `status` is a short summary; `verify` performs bounded diagnostic checks. The functional acceptance path is `notebooks/dark_platform_deposit_lifecycle.ipynb`: reserve an ARK, repeat the request with the same client item ID, complete metadata, wait for `PUBLISHED`, inspect both CIDs, and resolve the ARK.

The depositing application must not wait for IPFS or blockchain inside its web request. It stores the ARK and checks later through its own background task.

First list known managed deployments, then the services and exact selectors on
the deployed hosts:

```bash
venv/bin/python deploy.py deployments
venv/bin/python deploy.py services --deployment dark-operator-local-ha
venv/bin/python deploy.py services --inventory inventory.json
venv/bin/python deploy.py services --inventory inventory.json --json
```

En una terminal interactiva, el mismo inventario operativo está disponible en
una consola TUI: muestra deployments, servicios, detalle, acciones y un panel
de logs que se refresca mientras está abierto.

```bash
venv/bin/python deploy.py tui
```

Las acciones disponibles aparecen como botones en el panel derecho. Use `l`
para logs, `r` para refrescar y `q` para salir. Las acciones que interrumpen o
reconstruyen un servicio piden confirmación explícita.

Para observar sin poder actuar, `deploy.py metrics` abre un panel de solo
lectura sobre el mismo despliegue: muestra una fila por máquina con CPU,
memoria, contenedores y reinicios, más los contenedores de la máquina
seleccionada. Cada máquina se muestrea contra su propio daemon Docker, en local
o por su host SSH configurado, y la edad de cada muestra aparece siempre en
pantalla. `r` refresca, `m` vuelve a muestrear la máquina seleccionada, `s` cicla
el orden de los contenedores (nombre, CPU de mayor a menor, memoria de mayor a
menor) y `q` sale. El criterio activo aparece en el título del panel, el pie
muestra el ciclo completo y la línea de estado avisa del orden siguiente antes de
pulsar, para que el efecto de la tecla nunca sea una sorpresa. El panel no tiene
botones de ciclo de vida, así que puede quedarse abierto junto a la consola de
operaciones.

```bash
venv/bin/python deploy.py metrics
```

Para imprimir las últimas líneas y seguir el log en tiempo real de un servicio
del bundle desplegado, use el mismo selector exacto. Termine el seguimiento con
`Ctrl-C`:

```bash
venv/bin/python deploy.py logs \
  --deployment dark-operator-local-ha \
  --target service:dashboard \
  --tail 100
```

To operate one service without touching the rest of its machine, use its exact
`service:ID` selector:

```bash
venv/bin/python deploy.py recreate \
  --inventory inventory.json \
  --target service:dashboard --build
```

The supported operations are `build`, `stop`, `start`, `restart`, `recreate` and `remove`.
`build` builds an image without changing the running container.
`recreate --build` rebuilds from the current deployed Compose context. `remove`
removes only the managed container and never deletes volumes, bind-mounted data
or networks. Operations resolve services from the deployed snapshot and exact
Compose labels, and accept `--dry-run`; a changed working inventory does not
block operations on the already deployed revision.

### Recreate a deployed service

`recreate` is for replacing one container while retaining the deployed service
definition. It reads the target machine, Compose project, Compose file, service
identifier and declared runtime configuration from the active bundle; it does
not render an inventory again and it does not change other services, volumes,
bind-mounted data or Docker networks.

First inspect the target and perform a dry run:

```bash
venv/bin/python deploy.py deployments
venv/bin/python deploy.py services --deployment dark-operator-local-ha
venv/bin/python deploy.py recreate \
  --deployment dark-operator-local-ha \
  --target service:explorer \
  --dry-run
```

To recreate the existing image and configuration, omit `--build`:

```bash
venv/bin/python deploy.py recreate \
  --deployment dark-operator-local-ha \
  --target service:explorer
```

Use `--build` only after changing source code for a service whose deployed
Compose definition has a `build:` context. Docker rebuilds that image from the
current checkout referenced by the deployed Compose file, then recreates that
single service:

```bash
venv/bin/python deploy.py recreate \
  --deployment dark-operator-local-ha \
  --target service:explorer \
  --build
```

This does **not** import a changed inventory, regenerate environment files,
change ports, move a service to another machine, alter dependency wiring or
distribute new secrets. For any such declarative change, review the inventory
and run `apply` (or `install`) with it. `recreate --build` is a code rebuild;
`apply` is a configuration revision.

## 7. Recovery, evidence, and safety

Inspect the affected group logs before restarting anything. Use `resume` only after correcting the cause of an interrupted remote transfer or apply:

```bash
venv/bin/python deploy.py resume --inventory inventory.json
```

If Docker reports an overlapping subnet, the deployer lists the conflicting
bridge networks. It automatically reuses only the network with the expected
deployment name and exact subnet. To remove conflicting networks only when they
are empty, opt in explicitly:

```bash
venv/bin/python deploy.py install \
  --inventory inventory.json \
  --clean-empty-network-conflicts
```

The option never removes a network with attached containers. Change the
inventory subnet or investigate the other deployment when Docker reports an
occupied conflict.

During an interactive `install`, an empty conflict also produces a
`Remove them and continue? [y/N]` prompt. Declining preserves every network and
stops the installation with the exact conflicting name and subnet. `--yes` and
`--non-interactive` suppress that prompt; automation must use the explicit
cleanup option to authorize removal.

Preserve status and verification JSON, the applied bundle hash, genesis and static-node hashes, source-commit evidence, the resolution evidence, and notebook results. A mutable branch is not an immutable release: recorded commit hashes identify what actually ran.

Never remove production volumes as part of routine recovery. For a local trial, stop only projects generated by the selected inventory and keep explicitly excluded external containers intact.
