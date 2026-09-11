# Decoupled Infrastructure Architecture — Sandbox & Production

> Historical document. Its tier model and storage instructions are superseded by the
> single global-cluster design in [IPFS Architecture](ipfs-architecture.md).
> Store API now belongs to each apps site and `storage-node` runs only Kubo and
> one Cluster peer.

## Overview

In the **developer** profile, the entire dARK stack runs on a single machine.  
In **sandbox** and **production** profiles, the infrastructure is split across three dedicated tiers:

| Tier | Minimum nodes | Responsibility |
| ---- | ------------- | -------------- |
| **Blockchain Tier** | 3 | Hyperledger Besu validator nodes, smart-contract deployment, block explorer |
| **IPFS Tier** | 1 | IPFS nodes + IPFS Cluster for distributed content storage |
| **Application Tier** | 1 | Admin API, Minter API, Resolver API, Store API, Dashboard |

Each tier scales independently. The application tier connects to the other two over a private network and runs no blockchain or storage software itself.

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          BLOCKCHAIN TIER                                │
│                                                                         │
│  ┌──────────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │   BLOCKCHAIN NODE 1  │  │ BLOCKCHAIN NODE 2│  │ BLOCKCHAIN NODE N│  │
│  │  (bootnode + RPC)    │  │  (validator)     │  │  (validator)     │  │
│  │ blockchain runtime   │  │ runtime           │  │ runtime           │  │
│  │  dark-dapp           │  │                  │  │                  │  │
│  │  dark-explorador     │  │                  │  │                  │  │
│  │  :8545 (RPC)         │  │  :8545 (RPC)     │  │  :8545 (RPC)     │  │
│  └──────────────────────┘  └──────────────────┘  └──────────────────┘  │
│            │  peer-to-peer (Besu discovery / QBFT / PoA)  │            │
│            └──────────────────────────────────────────────┘            │
│            Exposes to other tiers: RPC_URL (Node 1 or LB) :8545        │
└─────────────────────────────────────────────────────────────────────────┘
                  ▲                                    ▲
                  │ RPC                                │ IPFS API / Cluster
                  │                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│                          APPLICATION TIER                               │
│                                                                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │ dark-core-      │  │ dark-core-      │  │ dark-core-      │         │
│  │ admin-api :8000 │  │ minter-api :8001│  │ resolver :8002  │         │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘         │
│                                                                         │
│  ┌─────────────────┐  ┌─────────────────┐                              │
│  │ dark-store-api  │  │ dashboard-web   │                              │
│  │ :8003           │  │ :8081           │                              │
│  └─────────────────┘  └─────────────────┘                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │ IPFS API / Cluster
┌─────────────────────────────────────────────────────────────────────────┐
│                             IPFS TIER                                   │
│                                                                         │
│  ┌──────────────────────┐  ┌──────────────────────┐                    │
│  │     IPFS NODE 1      │  │     IPFS NODE N       │                   │
│  │  dark-ipfs           │  │  dark-ipfs            │                   │
│  │  IPFS API    :5001   │  │  IPFS API    :5001    │                   │
│  │  Cluster API :9094   │  │  Cluster API :9094    │                   │
│  └──────────────────────┘  └──────────────────────┘                    │
│            │  IPFS Cluster replication                  │              │
│            └────────────────────────────────────────────┘              │
│            Exposes to application tier: :5001 / :9094 (Node 1 or LB)  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Blockchain Tier — Multiple Nodes

The blockchain tier runs **at least 3 Hyperledger Besu nodes** forming a private PoA/QBFT network:

| Node | Role | Components installed |
| ---- | ---- | -------------------- |
| Apps/RPC | RPC no validador + contract deployment | blockchain runtime, `dark-dapp` |
| blockchain-a / blockchain-b | Validadores y explorador en blockchain-a | blockchain runtime, `dark-explorador` en blockchain-a |

**Node 1** is the primary RPC endpoint used by the application tier (`RPC_URL`). The remaining nodes connect to Node 1 via peer discovery and participate in block validation. Having at least 3 validators ensures Byzantine fault tolerance — the network remains live if one node fails.

`dark-dapp` runs only on Node 1: it compiles the contracts and deploys them via Node 1's RPC. The resulting contract addresses are then shared with the application tier through `.env`.

---

## IPFS Tier — Distributed Storage

The IPFS tier runs **at least 1 dark-ipfs node**. When more than one node is provisioned, **IPFS Cluster** replicates content across all nodes automatically:

| Nodes | Behaviour |
| ----- | --------- |
| 1 | Single IPFS node + Cluster daemon (no replication) |
| 2+ | IPFS Cluster leader (Node 1) + followers — content pinned to all |

The application tier's `dark-store-api` connects only to Node 1's IPFS Cluster API (`:9094`). Cluster handles distribution internally.

---

## Changes Required in dark-deployer

### 1. New `.env` variables

```ini
# ── SANDBOX PROFILE ──────────────────────────────────────────────────
# Set to skip local installation of that tier on the current machine.
# Leave blank to install locally (developer default).

# Blockchain tier — primary RPC node (Node 1)
SANDBOX_BLOCKCHAIN_HOST=<node1-ip>

# Additional validator nodes (comma-separated, Node 2 onwards)
# Used for documentation/firewall rules; dark-deployer does not SSH into them.
SANDBOX_BLOCKCHAIN_EXTRA_NODES=<node2-ip>,<node3-ip>

# RPC endpoint (Node 1 or a load balancer in front of all nodes)
RPC_URL=http://<node1-ip>:8545

# IPFS tier — primary IPFS node (Node 1)
SANDBOX_IPFS_HOST=<ipfs-node1-ip>

# Additional IPFS nodes joining the cluster (comma-separated)
SANDBOX_IPFS_EXTRA_NODES=<ipfs-node2-ip>

# IPFS endpoints (Node 1 or LB)
SANDBOX_IPFS_API_URL=http://<ipfs-node1-ip>:5001
SANDBOX_IPFS_CLUSTER_URL=http://<ipfs-node1-ip>:9094
```

When `SANDBOX_BLOCKCHAIN_HOST` is set, `install.py` skips `install_blockchain()` on the current machine.  
When `SANDBOX_IPFS_HOST` is set, `install.py` skips `install_dark_ipfs()` on the current machine.

### 2. `install_profile()` guard logic

```python
def install_profile(prefix: str, env: dict) -> None:
    blockchain_host = env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip()
    ipfs_host       = env.get(f"{prefix}_IPFS_HOST", "").strip()

    if blockchain_host:
        print(f"[INFO] Blockchain tier is remote ({blockchain_host}) — skipping local install.")
    else:
        install_blockchain(prefix=prefix, env=env)

    install_core_lib(prefix=prefix, env=env)
    install_core_admin_api(prefix=prefix, env=env)

    if ipfs_host:
        print(f"[INFO] IPFS tier is remote ({ipfs_host}) — skipping local install.")
    else:
        install_dark_ipfs(prefix=prefix, env=env)

    install_dark_store_api(prefix=prefix, env=env)
    install_core_resolver_api(prefix=prefix, env=env)
    install_single_component(name="MINTER", prefix=prefix, env=env)
    install_dashboard(prefix=prefix, env=env)
```

### 3. Integration env files

Each application service receives a mode-`0600` `.env.integration` generated by
`install.py`. The root public handoff supplies remote RPC, contract, ABI and
Store API values; signer values are loaded separately from
`.env.integration.secrets`. Apps-only treats these handoffs as authoritative,
so stale `localhost` values from an old `.env` cannot shadow deployed endpoints.

---

## Deployment Flow

### Phase 1 — Blockchain Tier (Node 1)

```bash
# On blockchain Node 1
cp .env.example .env
# Wizard: Sandbox → Blockchain only
# Configure distinct ADMIN_PRIVATE_KEY and MINTER_PRIVATE_KEY before production.
python3 install.py
```

After completion this machine produces:

- `.env.integration`: public RPC, chain, contracts and ABI state.
- `.env.integration.secrets`: admin/minter signer handoff for the apps host.

Copy only the public file to storage. Transfer the secret file directly to the
apps host through a protected channel.

### Phase 2 — Additional Blockchain Validators

The deployer wizard provisions the primary blockchain tier. Additional Besu
validators are joined using the internal runtime's bootnode and validator procedures;
they must not redeploy contracts or regenerate the handoff. Preserve the
primary tier's contract addresses and chain ID.

### Phase 3 — IPFS Tier (Node 1)

```bash
# On IPFS Node 1
cp .env.example .env
# Copy .env.integration from the blockchain host; do not copy the secret file.
# Wizard: Sandbox → Storage only
python3 install.py
```

The storage run appends `METADATA_STORE_API_URL` to the public handoff. Copy
that updated public file to the apps host.

For additional IPFS nodes (Node 2…N), repeat the same process — IPFS Cluster handles peer joining via its own bootstrap configuration inside `dark-ipfs`.

### Phase 4 — Application Tier

```bash
# On application server
cp .env.example .env
# Copy the updated .env.integration from storage.
# Copy .env.integration.secrets directly from blockchain.
# Wizard: Sandbox → Apps only
python3 install.py
```

The wizard validates both handoffs before installing application services. Run
`python3 install.py validate` or `python3 install.py plan` for a non-mutating
preflight.

---

## Network Requirements

### Application Tier → Blockchain Tier

| Port | Protocol | Purpose |
| ---- | -------- | ------- |
| 8545 | HTTP | Besu JSON-RPC (Node 1 or LB) |

### Application Tier → IPFS Tier

| Port | Protocol | Purpose |
| ---- | -------- | ------- |
| 5001 | HTTP | IPFS API (Node 1) |
| 9094 | HTTP | IPFS Cluster API (Node 1) |

### Blockchain Tier — Inter-node

| Port | Protocol | Purpose |
| ---- | -------- | ------- |
| 30303 | TCP/UDP | Besu peer discovery and block propagation |

### IPFS Tier — Inter-node

| Port | Protocol | Purpose |
| ---- | -------- | ------- |
| 4001 | TCP | IPFS swarm (libp2p) |
| 9096 | TCP | IPFS Cluster swarm |

All ports above must be firewalled to allow only the listed source tiers. None should be publicly exposed.

---

## Profile Summary

| Profile | Blockchain tier | IPFS tier | Application tier |
| ------- | --------------- | --------- | ---------------- |
| `developer` | Local (1 node) | Local (1 node) | Local |
| `sandbox` | Remote (≥ 3 nodes) | Remote (≥ 1 node) | Local (application server) |
| `production` | Remote (≥ 3 nodes) | Remote (≥ 1 node) | Local (application server) |

---

## Implementation Status

- [x] Decoupled blockchain, storage and apps selections.
- [x] Public and secret handoff generation with explicit precedence.
- [x] Remote RPC, Store API and IPFS endpoint propagation.
- [x] Project-root-safe stop, restart and cleanup scripts.
- [x] Preflight validation, redacted planning and component commit locks.
- [ ] Expand the blockchain runtime runbook for multi-node validator enrollment.
- [ ] Expand the IPFS Cluster runbook for peer bootstrap and replacement.
- [ ] Maintain environment-specific firewall rules outside this repository.
