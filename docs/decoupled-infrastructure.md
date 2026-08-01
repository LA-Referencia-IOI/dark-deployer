# Decoupled Infrastructure Architecture — Sandbox & Production

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
│  │  dark-env            │  │  dark-env        │  │  dark-env        │  │
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
| Node 1 (bootnode) | Validator + RPC entrypoint + contract deployment | `dark-env`, `dark-dapp`, `dark-explorador` |
| Node 2…N | Validators | `dark-env` only |

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

Each application service receives an `.env.integration` file generated by `install.py`. In the decoupled setup, `RPC_URL` and IPFS endpoints in those files must point to the remote tiers' IPs, not `localhost`. No change is needed in service Dockerfiles or compose files.

---

## Deployment Flow

### Phase 1 — Blockchain Tier (Node 1)

```bash
# On blockchain Node 1
cp .env.example .env
# TYPE=sandbox
# Fill only blockchain repo URLs (leave IPFS and service URLs empty)
# Leave SANDBOX_BLOCKCHAIN_HOST blank so this machine installs the blockchain
python3 install.py
```

After completion, collect from this machine:
- `RPC_URL` (written into `.env` by dark-env setup)
- Contract addresses from `components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini`
- signer material from `.env.integration.secrets` on apps hosts only; the storage
  tier receives no blockchain private key

### Phase 2 — Blockchain Tier (Nodes 2…N)

Each additional validator node runs only `dark-env`, configured to join Node 1's network:

```bash
# On each additional validator node
cp .env.example .env
# TYPE=sandbox
# Set only SANDBOX_BLOCKCHAIN_DARK_ENV_REPOSITORY_URL
# Configure dark-env to use Node 1 as the bootnode (via dark-env's own config)
# Set SANDBOX_BLOCKCHAIN_HOST=<this-node-ip> to prevent dark-dapp/explorer install
python3 install.py
```

### Phase 3 — IPFS Tier (Node 1)

```bash
# On IPFS Node 1
cp .env.example .env
# TYPE=sandbox
# Fill only SANDBOX_IPFS_REPOSITORY_URL
# Leave SANDBOX_IPFS_HOST blank so this machine installs IPFS
python3 install.py
```

For additional IPFS nodes (Node 2…N), repeat the same process — IPFS Cluster handles peer joining via its own bootstrap configuration inside `dark-ipfs`.

### Phase 4 — Application Tier

```bash
# On application server
cp .env.example .env

# Point to the remote tiers:
# SANDBOX_BLOCKCHAIN_HOST=<node1-ip>
# SANDBOX_IPFS_HOST=<ipfs-node1-ip>
# RPC_URL=http://<node1-ip>:8545
# SANDBOX_IPFS_API_URL=http://<ipfs-node1-ip>:5001
# SANDBOX_IPFS_CLUSTER_URL=http://<ipfs-node1-ip>:9094

# Contract addresses from Phase 1:
# MASTER_WALLET_ADDRESS=...
# MASTER_PRIVATE_KEY=  # keep blank when using .env.integration.secrets
# MASTER_PUBLIC_KEY=...

python3 install.py
```

`install.py` detects the remote hosts and installs only the application services.

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

## Implementation Checklist

- [ ] Add `{PREFIX}_BLOCKCHAIN_HOST`, `{PREFIX}_BLOCKCHAIN_EXTRA_NODES` to `.env.example` for SANDBOX and PRODUCTION
- [ ] Add `{PREFIX}_IPFS_HOST`, `{PREFIX}_IPFS_EXTRA_NODES`, `{PREFIX}_IPFS_API_URL`, `{PREFIX}_IPFS_CLUSTER_URL` to `.env.example`
- [ ] Update `install_profile()` in `install.py` with the guard logic described above
- [ ] Ensure `.env.integration` generation uses `RPC_URL` and IPFS URLs from `.env` (verify per service)
- [ ] Update `stop.py` and `clean.py` to skip blockchain/IPFS directories when remote hosts are set
- [ ] Document dark-env bootnode configuration for multi-node Besu setup
- [ ] Document IPFS Cluster bootstrap configuration for multi-node IPFS setup
- [ ] Define firewall rules between all three tiers
