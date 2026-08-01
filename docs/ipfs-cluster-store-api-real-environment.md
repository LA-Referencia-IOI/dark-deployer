# IPFS, IPFS Cluster and Store API in a real environment

> Superseded by [IPFS Architecture](ipfs-architecture.md). The current Store API
> uses two site-local endpoint lists and verifies global peer/site quorum; the
> single-endpoint examples below are retained only as historical context.

This document explains how `dark-store-api`, IPFS and IPFS Cluster fit together when the system is deployed as real infrastructure rather than as one local Docker stack.

## Roles

### Store API

`dark-store-api` is the application-facing storage service. The minter and resolver talk to it, not directly to IPFS or IPFS Cluster.

Its responsibilities are intentionally narrow:

- accept raw bytes through `POST /v1/store`;
- return a CID;
- retrieve raw bytes by CID through `GET /v1/retrieve/{cid}`;
- expose storage readiness through `GET /health`;
- expose process liveness through `GET /health/live`.

It does not understand ARK semantics, L1/L2 meaning, authority rules or blockchain state. Those concepts live in the minter, resolver and `dark-core-lib`.

### Local IPFS node

Each Store API instance should have access to a local IPFS node. In Docker development this is `ipfs0`; in production it would normally be an IPFS daemon running on the same host or on the same private node network.

The Store API uses this local IPFS API mainly for:

- reading content with `cat`;
- basic health checks;
- legacy `ipfs_then_cluster` mode, if explicitly enabled.

The local IPFS node should not be treated as the whole storage system. It is the local entry point into the distributed IPFS layer.

### Local IPFS Cluster peer

Each Store API node should also have a local IPFS Cluster peer. In Docker development this is `cluster0`.

The cluster peer is responsible for:

- accepting pin/add operations;
- coordinating replication with the other cluster peers;
- reporting peer availability;
- enforcing the replication policy.

The Store API should not need direct network access to every remote IPFS node. It talks to the local cluster peer, and the cluster peer talks to the rest of the cluster.

### IPFS Cluster Proxy

The default write path uses the IPFS Cluster Proxy:

```text
Store API -> local Cluster Proxy -> local IPFS -> Cluster replication
```

The proxy exposes an IPFS-compatible `/api/v0/add` endpoint. The Store API sends content to:

```text
{IPFS_CLUSTER_PROXY_API_URL}/api/v0/add?cid-version=1&pin=true
```

This lets the Store API add and pin in one operation, while Cluster handles allocation and replication.

## Recommended production shape

A practical production deployment should look like this:

```text
Application network
  Minter / Resolver
        |
        v
  Store API
        |
        | private local node network
        v
  Local Cluster Peer / Proxy
        |
        v
  Local IPFS Node

Cluster private network
  Local Cluster Peer <-> Remote Cluster Peer 1 <-> Remote Cluster Peer 2
  Local IPFS Node    <-> Remote IPFS Node 1    <-> Remote IPFS Node 2
```

From the Store API point of view, only the local IPFS and local Cluster peer are directly visible. Remote nodes are part of the storage substrate, not direct application dependencies.

## Local Docker simulation

The local `dark-ipfs` compose simulates that production shape with the shared `dark-net` integration network plus the IPFS/Cluster internal networks:

- `dark-net`: integration network shared by dARK services. `dark-store-api`, `ipfs0`, and `cluster0` join this network.
- `dark-ipfs-backbone`: connects all IPFS and Cluster peers.
- `dark-ipfs-remote-node-1` and `dark-ipfs-remote-node-2`: simulate remote storage nodes.

The Store API joins `dark-net`, so it can resolve:

```text
http://ipfs0:5001
http://cluster0:9094
http://cluster0:9095
```

It should not need to resolve `ipfs1`, `ipfs2`, `cluster1` or `cluster2`.

## Write path

Default write mode:

```text
Minter
  -> Store API /v1/store
  -> Cluster Proxy /api/v0/add?cid-version=1&pin=true
  -> local IPFS receives content
  -> IPFS Cluster pins and allocates replicas
  -> Store API returns CID
```

The Store API does not call `/pins/{cid}` after a proxy add. The proxy add is the pinning operation.

If `IPFS_ADD_MODE=ipfs_then_cluster` is explicitly configured, the legacy path is:

```text
Store API
  -> IPFS /api/v0/add?pin=false
  -> Cluster REST /pins/{cid}
```

There is no automatic fallback between modes. If the configured write path fails, the store operation fails clearly.

## Read path

Read path:

```text
Resolver or Minter
  -> Store API /v1/retrieve/{cid}
  -> local IPFS /api/v0/cat
```

The local IPFS node may already have the content. If it does not, it can fetch it through IPFS from the other nodes that hold replicas.

## Health and readiness

The Store API exposes two different health endpoints:

```text
/health/live
```

This is a lightweight liveness check. It only means the API process is up. Docker healthchecks should use this endpoint.

```text
/health
```

This is storage readiness. It checks the local IPFS node, local Cluster peer, Cluster Proxy when using `cluster_proxy`, and visible Cluster peers. It is cached for `IPFS_HEALTH_CACHE_TTL_SECONDS` to avoid repeatedly hammering IPFS/Cluster.

Use:

```text
/health?refresh=true
```

to force a fresh backend check.

The minter metadata worker should use readiness. If storage is not ready, it should pause metadata persistence instead of turning infrastructure trouble into ARK-level failures.

## Replication policy

Development defaults:

```text
CLUSTER_REPLICATION_MIN=2
CLUSTER_REPLICATION_MAX=2
```

This means every stored object should be allocated to exactly two cluster peers. In production, choose replication based on the number of available nodes, durability expectations and storage budget.

The Store API only needs to know the minimum number of visible peers required for readiness:

```text
IPFS_CLUSTER_MIN_PEERS=2
```

Cluster itself owns the actual placement and replication rules.

## Startup order

The IPFS/Cluster substrate must exist before Store API starts.

For local Docker, start the shared dARK network first, then the IPFS substrate, then Store API:

```bash
cd components/blockchain/dark-env
docker compose up -d

cd ../dark-ipfs
docker compose up -d

cd ../../services/dark-store-api
docker compose up -d
```

The root installer installs components in that order. `dark-env` creates `dark-net`; then `dark-ipfs` connects `ipfs0` and `cluster0` to it; then Store API starts and reaches those services through stable Docker DNS aliases.

The root installer should generate the Store API integration env with:

```text
IPFS_API_URL=http://ipfs0:5001
IPFS_CLUSTER_API_URL=http://cluster0:9094
IPFS_CLUSTER_PROXY_API_URL=http://cluster0:9095
IPFS_ADD_MODE=cluster_proxy
IPFS_HEALTH_CACHE_TTL_SECONDS=10
```

## Common error: dark-net not found

Error:

```text
network dark-net declared as external, but could not be found
```

This means a component is trying to attach to the shared dARK integration network before the blockchain stack has created it.

Usually one of these is true:

- `dark-env` was not started yet;
- `dark-net` was removed with `docker network rm` or a Docker cleanup command;
- Docker is using a different context or engine than the one where `dark-env` was started.

Fix:

```bash
python3 install.py
```

or manually:

```bash
cd components/blockchain/dark-env
docker compose up -d

cd ../dark-ipfs
docker compose up -d

docker network ls | grep dark-net

cd ../../services/dark-store-api
docker compose up -d
```

If the Store API cannot resolve `ipfs0` or `cluster0`, inspect the rendered IPFS compose and the `dark-net` attachments:

```bash
cd components/blockchain/dark-ipfs
docker compose config | grep -A20 'networks:'
docker inspect dark-ipfs-ipfs0 --format '{{json .NetworkSettings.Networks}}'
docker inspect dark-ipfs-cluster0 --format '{{json .NetworkSettings.Networks}}'
```

## Operational notes

- Store API should be stateless. IPFS and Cluster hold persistence.
- Do not expose remote IPFS node APIs to application services unless there is a specific operational reason.
- Keep Cluster REST and Proxy on private networks in production.
- Use `/health/live` for container liveness and `/health` for worker readiness decisions.
- Treat IPFS/Cluster outages as infrastructure pauses, not as permanent ARK failures.
- Avoid frequent uncached health polling; use readiness cache to reduce background pressure.
