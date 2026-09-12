# dARK 2.0 - Technical Architecture

> This guide is part of the [dARK 2.0 Documentation](README.md).
> See also: [Developer Guide](DARK_2.0_GUIDE.md) | [API Reference](DARK_2.0_API_REFERENCE.md)

This document details the **Authority-Centric** architecture (v2.0) of the dARK decentralized identifier system, from smart contracts to the service layer.

---

## 1. System Overview

The stack is composed of three layers:

```
┌─────────────────────────────────────────────────────────────────┐
│                         SERVICE LAYER                           │
│   Admin API (8000) · Minter API (8001) · Resolver API (8002)   │
│                     Store API (8003)                            │
└────────────────────────┬────────────────────────────────────────┘
                         │ dark-core-lib (Python SDK)
                         │ DARKCoreClient / AuthorityService / ARKService
┌────────────────────────▼────────────────────────────────────────┐
│                     BLOCKCHAIN LAYER                            │
│       Authority.sol  ←── IAuthority.sol ───→  dARK.sol         │
│       (access control)      (interface)      (ark storage)     │
│                 dARK blockchain runtime (Besu network)          │
└─────────────────────────────────────────────────────────────────┘
                         │ IPFS CID storage
┌────────────────────────▼────────────────────────────────────────┐
│                      STORAGE LAYER                              │
│             dark-ipfs (IPFS node) · dark-store-api              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Smart Contract Architecture

The monolithic v1 design was refactored into two distinct contracts to separate **Access Control** from **Data Storage**, communicating through an interface.

### 2.1 `IAuthority.sol` — Interface

[`IAuthority.sol`](components/dark-dapp/dARK_dapp/contracts/IAuthority.sol) defines the minimal surface that `dARK.sol` needs from the authority contract. This decouples the two contracts: `dARK.sol` depends only on the interface, not the implementation.

```solidity
interface IAuthority {
    function is_authorized(address wallet, string calldata naan) external view returns (bool);
    function is_active_authority(address wallet) external view returns (bool);
    function get_authority(string calldata uuid) external view
        returns (address wallet, string[] memory naans, bool active);
}
```

### 2.2 `Authority.sol` — Access Control Registry

[`Authority.sol`](components/dark-dapp/dARK_dapp/contracts/Authority.sol) is the central registry that binds organization identities to Ethereum wallets and controls which NAANs each authority can write.

**Key Concept**: One Wallet = One Authority UUID.

**`AuthorityData` struct (on-chain):**
```solidity
struct AuthorityData {
    string uuid;                   // External UUID identifier
    address wallet;                // Owner wallet
    bool active;                   // Active status
    string encrypted_private_key;  // AES-256 encrypted, hex encoded
}
```

The `encrypted_private_key` is stored on-chain so services can retrieve and decrypt it server-side to sign transactions on behalf of an authority.

**Admin functions** (`onlyAdmin`):

| Function | Description |
| :--- | :--- |
| `register_authority(uuid, wallet, encrypted_private_key)` | Binds a UUID to a wallet; stores the encrypted private key |
| `deactivate_authority(uuid)` | Marks an authority as inactive; blocks all future writes |

**Authority wallet functions** (`onlyActiveAuthority`):

| Function | Description |
| :--- | :--- |
| `authorize_naan(naan)` | Authority claims management of a NAAN |
| `revoke_naan(naan)` | Authority removes its own NAAN authorization |

**View functions**:

| Function | Description |
| :--- | :--- |
| `is_authorized(wallet, naan)` | Returns `true` if wallet is active and authorized for the NAAN |
| `is_active_authority(wallet)` | Returns `true` if wallet belongs to an active authority |
| `get_authority(uuid)` | Returns `(wallet, naans[], active)` for a UUID |
| `get_uuid_by_wallet(wallet)` | Reverse lookup: wallet → UUID |
| `get_authority_key(uuid)` | Returns encrypted private key — `onlyAdmin` |

**Events**: `AuthorityRegistered`, `AuthorityDeactivated`, `NAANAuthorized`, `NAANRevoked`

### 2.3 `dARK.sol` — ARK Identifier Storage

[`dARK.sol`](components/dark-dapp/dARK_dapp/contracts/dARK.sol) is the pure data store for ARK identifiers. It holds a reference to `IAuthority` and delegates all permission checks to it.

**`ARK` struct (on-chain):**
```solidity
struct ARK {
    string name;         // Identifier within the NAAN (e.g. "doc1")
    string naan;         // Name Assigning Authority Number
    string url;          // Resolution URL
    string cid;          // IPFS CID for metadata
    address owner;       // Authority wallet at creation time
    uint256 created_at;  // Block timestamp of creation
    uint256 updated_at;  // Block timestamp of last update
}
```

ARKs are stored by the hash `keccak256(abi.encodePacked(naan, "/", name))`.

**Write functions** (`onlyAuthorizedFor(naan)`):

| Function | Description |
| :--- | :--- |
| `create_ark(naan, name, url, cid)` | Stores a new ARK; reverts if it already exists |
| `update_ark(naan, name, url, cid)` | Updates `url` and `cid`; requires caller == original owner |

**View functions**:

| Function | Description |
| :--- | :--- |
| `resolve(naan, name)` | Returns `url` for an ARK identifier |
| `get_ark(naan, name)` | Returns the full `ARK` struct |
| `ark_exists(naan, name)` | Returns `bool` |
| `get_ark_count()` | Returns total ARKs created (analytics) |
| `get_authority_contract()` | Returns the linked Authority address |

**Events**: `ARKCreated`, `ARKUpdated`

---

## 3. Interaction Flow

### Phase 1: Authority Setup (one-time per organization)

```
Admin wallet                    Authority contract
     │                                 │
     │── register_authority(uuid,      │
     │       wallet, encrypted_key) ──►│  stores AuthorityData on-chain
     │                                 │
Authority wallet                Authority contract
     │                                 │
     │── authorize_naan("12345") ──────►│  marks wallet→naan as authorized
```

1. **Deploy**: `Authority.sol` is deployed first; then `dARK.sol` is deployed with the Authority address as constructor argument.
2. **Register** (`onlyAdmin`): The deployer admin calls `register_authority(uuid, authority_wallet, encrypted_private_key)`. This binds the organization UUID to its Ethereum wallet and stores an AES-256 encrypted copy of the wallet private key on-chain.
3. **Authorize** (`onlyActiveAuthority`): The **authority wallet** (not admin) calls `authorize_naan("12345")` to claim ownership of a NAAN.

### Phase 2: ARK Lifecycle

```
Minter API                   dARK contract              Authority contract
     │                            │                            │
     │── create_ark(naan,         │                            │
     │     name, url, cid) ──────►│── is_authorized(          │
     │                            │     msg.sender, naan) ────►│
     │                            │◄── true ──────────────────│
     │                            │  stores ARK struct         │
     │                            │
Resolver API                 dARK contract
     │                            │
     │── resolve(naan, name) ────►│  reads _arks[hash].url
     │◄── url ───────────────────│
```

1. **Create ARK**: The minter service calls `create_ark(naan, name, url, cid)`. `dARK` delegates the permission check to `Authority.is_authorized(msg.sender, naan)`. On success the full ARK struct is written with timestamps.
2. **Update ARK**: `update_ark(naan, name, url, cid)` updates `url` and `cid`; requires `msg.sender == ark.owner`.
3. **Resolve**: Anyone (including the public resolver) calls `resolve(naan, name)` to get the URL.

---

## 4. Service Layer

Each service uses [`dark-core-lib`](components/dark-core-lib/) as the Python SDK to interact with the contracts via Web3.py.

| Service | Role | Contract interaction |
| :--- | :--- | :--- |
| **dark-core-admin-api** (8000) | Authority management REST API | `Authority`: register, deactivate, authorize/revoke NAANs |
| **dark-core-minter-api** (8001) | ARK minting REST API + async workers | `dARK`: create/update ARKs; reads authority key for signing |
| **dark-core-resolver-api** (8002) | Read-only ARK resolver | `dARK`: resolve, get\_ark, ark\_exists |
| **dark-store-api** (8003) | Stateless raw content storage | No direct contract interaction; stores/retrieves metadata blobs |

`dark-core-lib` exposes:
- `DARKCoreClient` — unified entry point (`from_env()` for environment-based config)
- `AuthorityService` — wraps all `Authority.sol` calls (register, deactivate, authorize/revoke NAANs, get signing credentials)
- `ARKService` — wraps all `dARK.sol` calls; handles authority key retrieval for signing; exposes single-op `create`/`update` and batch `publish_operations`
- `ChainService` — read-only chain health helpers (`is_connected`, `get_block_number`, `get_admin_balance`)
- `MetadataService` — Level 1/2 storage orchestration (see §7.3)
- `CoreConfig` — reads from env vars `DARK_RPC_URL`, `DARK_CONTRACT_ADDRESS`, `DARK_AUTHORITY_ADDRESS`, `DARK_ADMIN_PRIVATE_KEY`, `DARK_CHAIN_ID`

At deployment time, `DARK_ADMIN_PRIVATE_KEY` is scoped per service: Admin API
receives `ADMIN_PRIVATE_KEY`, while Minter receives `MINTER_PRIVATE_KEY` under
the SDK-compatible environment name. Production validation requires these
signers to be distinct; read-only services receive neither key.

### 4.1 SDK — Pipelined Transaction Publishing

`ARKService.publish_operations(uuid, operations, pipeline_size=20)` is the SDK's bulk write engine, used by the minter's `ChainPublisherWorker` to submit batches to the blockchain with maximum throughput.

**Why pipelining?** On EVM chains, transactions from the same address must have strictly sequential nonces. Waiting for a receipt before sending the next one serializes throughput. The pipeline approach pre-signs an entire window of transactions, fires them all to the mempool, and then collects receipts — achieving near-concurrent submission.

**Processing flow:**

```
operations list  ──► [ window 0..19 ] ──► [ window 20..39 ] ──► ...
                             │
              1. Fetch nonce once ("pending")
                             │
              2. For each op in window:
                 build_transaction(nonce=N, N+1, N+2, ...)
                 sign with authority private key
                             │
              3. Fire send_raw_transaction for all signed txs
                 (non-blocking — all land in mempool together)
                             │
              4. Wait for receipt for each tx in order
```

**Per-operation result statuses:**

| Status | Meaning |
| :--- | :--- |
| `confirmed` | Receipt received, `status == 1` |
| `reverted` | Receipt received, `status == 0` (contract revert) |
| `send_failed` | `send_raw_transaction` raised — tx never reached mempool |
| `ambiguous` | Receipt wait timed out or failed — tx may or may not be on-chain |
| `not_sent` | Skipped proactively to avoid creating a nonce gap |

**`stop_pipeline` flag:** Any failure at build/sign time, send time, or ambiguous receipt sets `stop_pipeline = True`. All remaining unprocessed windows are immediately marked `not_sent` — it is never safe to submit a window when a prior nonce is in an unknown state, because EVM nodes will queue (or drop) transactions that reference a missing nonce.

```
ARKPublishOperation           ARKPublishResult
─────────────────             ────────────────
ref: str                      ref: str
action: "create"|"update"     action: "create"|"update"
naan: str                     status: confirmed|reverted|send_failed|ambiguous|not_sent
name: str                     error: Optional[str]
url: str
cid: str
```

---

## 5. Minter Architecture

The minter (`dark-core-minter-api`) runs an HTTP API plus three independent singleton workers: metadata persistence, IPFS replication reconciliation, and chain publication. They share PostgreSQL; metadata and replication workers also share the optional filesystem payload volume.

### 5.1 ARK Lifecycle States

Every ARK record in the minter's PostgreSQL database passes through these states:

```
  POST /arks ──► RESERVED (R) ──► DRAFT (D) ──────────────────────────────►┐
                                    │                                        │
  PUT /arks/{id}                    │  MetadataPersistenceWorker             │
  (on existing published)           │  stores metadata to IPFS/store-api     │
                   │                ▼                                        │
  UPDATE (U) ──────┘         [metadata CIDs written]                        │
       │                            │  ReplicationReconciliationWorker       │
       │                            │  verifies and repairs storage replicas │
       │                            ▼                                        │
       │                            │  ChainPublisherWorker                  │
       └──────────────────── PUBLISHED (P) ◄───────────────────────────────┘
  
  DELETE /arks/{id} ──────────────► TOMBSTONE (T)
```

| State | Code | Meaning |
| :--- | :--- | :--- |
| `RESERVED` | `R` | ID minted by NOID, waiting for client to provide metadata |
| `DRAFT` | `D` | Metadata received, queued for persistence and on-chain publication |
| `UPDATE` | `U` | New metadata/target pending on-chain update for an already-published ARK |
| `PUBLISHED` | `P` | Metadata persisted in IPFS and ARK written to blockchain |
| `TOMBSTONE` | `T` | Deactivated by client |

### 5.2 REST API Process

The FastAPI app handles synchronous HTTP requests and writes to the PostgreSQL database. The main endpoints for the ARK lifecycle are:

| Endpoint | Transition | Description |
| :--- | :--- | :--- |
| `POST /arks` | → `RESERVED` | Reserve a single ARK. Generates a NOID-based identifier |
| `POST /arks/batch` | → `RESERVED` | Batch-reserve multiple ARKs in a single request |
| `PUT /arks/{ark_id}` | `RESERVED` → `DRAFT` | Provide metadata and target URL; queues ARK for publication |
| `DELETE /arks/{ark_id}` | → `TOMBSTONE` | Deactivate an ARK |
| `GET /arks/{ark_id}` | — | Query current state and metadata |

**NOID generation** (`mint_ark_id`): Each ARK name is deterministically generated from an auto-incrementing counter per `naan+shoulder` namespace, with an optional check digit. The shoulder and check-digit behavior are configurable via env vars (`MINTER_SHOULDER`, `MINTER_NOID_CHECKDIGIT`).

**Authorization check cache**: Each minting request validates that the calling authority is authorized for the NAAN. This check is cached in-process to avoid a blockchain call on every request.

**mTLS middleware**: All API endpoints can require mutual TLS (`MTLS_ENABLED`). In non-production this can be disabled.

### 5.3 Worker Processes

Each worker runs as a separate process/container using `python -m app.main_worker <mode>`. PostgreSQL advisory locks, per-worker PID files and `worker_runtime_status` heartbeats ensure one active instance per runtime name.

```
Metadata worker loop
  │
  └─► MetadataPersistenceWorker.run_publish_cycle()
  │       Polls DB: ARKs in DRAFT or UPDATE state with no persisted CIDs
          For each:
  │         1. Store Level 2 (original record) → dark-store-api or IPFS
  │            Returns level2_cid (original_cid in DB)
  │         2. Store Level 1 (minimal JSON + level2_cid reference)
  │            Returns level1_cid
          3. Write both CIDs to DB and retain payloads for reconciliation

Replication worker loop
  └─► ReplicationReconciliationWorker
          observes both CIDs with Store API batch status
          waits for queued/pinning assignments; repairs only real errors
          updates replica counts and purges only when both targets are met

Chain worker loop
  └─► ChainPublisherWorker.run_publish_cycle()
          Polls DB: ARKs with both CIDs persisted, not yet PUBLISHED
          For each:
            1. Retrieve authority encrypted_private_key from Authority contract
            2. Decrypt key → authority wallet account
            3. Call dARK.create_ark() or dARK.update_ark() with (naan, name, url, level1_cid)
            4. Wait for tx receipt
            5. On success → mark PUBLISHED in DB
            6. On failure → reconcile on-chain state and retry according to policy
```

### 5.4 Metadata Levels

The minter stores metadata at two levels:

| Level | Content | Storage | DB field |
| :--- | :--- | :--- | :--- |
| **Level 1** | Minimal extracted fields (JSON, `Level1Metadata` schema) | dark-store-api / IPFS | `level1_cid` |
| **Level 2** | Original full record (XML, JSON, plain text) | dark-store-api / IPFS | `original_cid` |

The `level1_cid` is the value written to the blockchain as the `cid` field in the `ARK` struct. This means the on-chain CID always points to the normalized Level 1 document, which in turn references the Level 2 original via an embedded link.

### 5.5 Retry and Fault Tolerance

Both workers share the same retry strategy:

- **Exponential backoff**: Each failure increments `publish_retry_count` and delays the next attempt by `backoff_base ^ retry_count` seconds.
- **Max retries**: Configurable (`METADATA_WORKER_MAX_RETRIES`, `CHAIN_WORKER_MAX_RETRIES`). When exceeded, the ARK is marked as permanently failed.
- **RPC health gate**: Before each chain publication cycle, the worker checks RPC connectivity. If the node is unreachable, the cycle is deferred without consuming the retry budget (`defer_publish_retry`).
- **Reconciliation after chain failure**: If a blockchain transaction fails but the ARK may have been written (network timeout, etc.), the worker reads the on-chain state and compares `url` and `cid`. If they match → marks as published. If they differ → retries. If missing → retries normally.
- **CAS protection**: `update_to_published` uses an optimistic update to avoid double-marking in concurrent scenarios.
- **Per-cycle metrics**: Each worker tracks `total_processed`, `total_succeeded`, `total_failed`, `last_run_duration` in-memory and reports via the DB heartbeat, visible at `GET /worker/status`.

---

## 6. Resolver Architecture

The resolver (`dark-core-resolver-api`) is a **stateless, read-only** service with no database. It answers three kinds of requests for any published ARK.

### 6.1 Resolution Modes

A single catch-all route `GET /{ark:path}` handles all three modes via query parameters:

| Request | Behaviour | Source |
| :--- | :--- | :--- |
| `GET /ark:/NAAN/name` | HTTP redirect (302 by default) to `url` | Blockchain only |
| `GET /ark:/NAAN/name?info` | JSON response with full bibliographic info | Blockchain + Level 1 metadata |
| `GET /ark:/NAAN/name?metadata` | Raw original record (XML, JSON, plain text) | Blockchain → Level 1 CID → Level 2 document |
| `HEAD /ark:/NAAN/name` | Same as GET without body | Blockchain only |

The redirect status code is configurable (`RESOLVER_REDIRECT_STATUS`, default `302`).

### 6.2 Resolution Flow

```
Client                   Resolver              Blockchain (dARK.sol)       Metadata Storage
  │                         │                          │                         │
  │── GET /ark:/55555/doc ──►│                          │                         │
  │                         │── get_ark(55555, doc) ──►│                         │
  │                         │◄── ARK{url, cid, ...} ───│                         │
  │◄── 302 → url ───────────│                          │                         │
  │                         │                          │                         │
  │── GET ...?info ─────────►│                          │                         │
  │                         │── get_ark(55555, doc) ──►│                         │
  │                         │◄── ARK{url, cid, ...} ───│                         │
  │                         │── load_level1(cid) ──────────────────────────────►│
  │                         │◄── Level1Metadata ────────────────────────────────│
  │◄── ArkInfoResponse ─────│                          │                         │
  │                         │                          │                         │
  │── GET ...?metadata ─────►│                          │                         │
  │                         │── get_ark(55555, doc) ──►│                         │
  │                         │◄── ARK{url, cid, ...} ───│                         │
  │                         │── load_level1(cid) ──────────────────────────────►│
  │                         │◄── Level1Metadata ────────────────────────────────│
  │                         │── load_level2(level1) ───────────────────────────►│
  │                         │◄── raw document (XML/JSON/text) ──────────────────│
  │◄── 200 raw document ────│                          │                         │
```

Every request always starts with a blockchain read (`get_ark`). There is no local cache — consistency is guaranteed by the immutability of on-chain data.

### 6.3 `?info` Response (`ArkInfoResponse`)

The `?info` view fetches Level 1 metadata using the `cid` stored on-chain and returns a structured JSON object:

```json
{
  "ark": "ark:/55555/my-doc",
  "target": "https://example.org/resource",
  "created_at": "2026-01-15T10:00:00Z",
  "updated_at": "2026-01-15T10:00:00Z",
  "metadata_schema": "dublin_core",
  "title": "Example Resource",
  "authors": ["Author One"],
  "year": 2026,
  "publisher": "Example Org",
  "resource_type": "dataset",
  "language": "en",
  "abstract": "...",
  "subjects": ["subject1"],
  "rights": "CC-BY 4.0",
  "alternate_identifiers": [{"schema": "doi", "value": "10.1234/example"}],
  "alternate_urls": ["https://mirror.example.org/resource"]
}
```

### 6.4 `?metadata` Response

Returns the raw **Level 2** original record with its original `Content-Type` (e.g. `application/xml` for Dublin Core, `application/json` for DataCite). The flow traverses two CID hops:

1. Blockchain `cid` field → fetch Level 1 JSON from storage
2. Level 1 JSON contains `original_metadata.cid` → fetch Level 2 raw document from storage

### 6.5 Error Handling

| Condition | HTTP Status |
| :--- | :--- |
| Malformed ARK identifier | `400 Bad Request` |
| ARK not found on-chain | `404 Not Found` |
| Blockchain node unreachable | `502 Bad Gateway` |
| Metadata CID missing on published ARK | `502 Bad Gateway` |
| Metadata document not found in storage | `502 Bad Gateway` (on `?info`) / `404` (on `?metadata`) |
| Both `?info` and `?metadata` in same request | `400 Bad Request` |

---

## 7. Storage Layer

The storage layer handles all content-addressed metadata blobs. It is composed of two independent pieces: **dark-ipfs** (the distributed storage cluster) and **dark-store-api** (the abstraction API in front of it).

### 7.1 `dark-ipfs` — IPFS Cluster

`dark-ipfs` runs one Kubo container and one IPFS Cluster container per storage node. The deployer installs one pair on each host; all pairs join one global CRDT Cluster.

```
 dark-ipfs Docker Compose
 ┌─────────────────────────────────────────────────────┐
 │  cluster peer ──── Kubo peer (one pair per host)    │
 │  all peers join the global CRDT Cluster over VPN    │
 └─────────────────────────────────────────────────────┘
         │               │
    IPFS API          IPFS Cluster REST API
    :5001             :9094
```

| Container | Image | Exposed port | Role |
| :--- | :--- | :--- | :--- |
| `ipfs` | `ipfs/kubo` | `5001` (API), `4001` (swarm) | Storage peer |
| `cluster` | `ipfs/ipfs-cluster` | `9094` (REST), `9095` (proxy), `9096` (swarm) | Cluster peer |

**Key configuration:**
- **Consensus**: CRDT (no leader election, eventually consistent)
- **Replication**: configurable min/max factors (`CLUSTER_REPLICATION_MIN`/`MAX`, defaults 2/3)
- **Startup order**: Kubo first, then its Cluster peer; bootstrap and peer discovery use generated inventory values
- **CID version**: CIDv1 for all new content (`cid-version=1` on `/api/v0/add`)

### 7.2 `dark-store-api` — Storage Abstraction API

`dark-store-api` is a **stateless FastAPI service** (port 8003) that exposes a simple content-addressed HTTP API. It decouples all other services from the underlying storage technology.

**Endpoints:**

| Method | Path | Description | Returns |
| :--- | :--- | :--- | :--- |
| `POST` | `/v1/store` | Store raw bytes (any Content-Type) | `{"cid": "...", "size": N}` |
| `GET` | `/v1/retrieve/{cid}` | Retrieve content by CID | Raw bytes (`application/octet-stream`) |
| `GET` | `/v1/status/{cid}` | Pin/replication status | total pinned replicas and observation time |
| `POST` | `/v1/status/batch` | Bounded batch pin status | statuses with confirmed, queued, pinning and error counts |
| `GET` | `/health` | Service health check | `{"status": "healthy"}` |

**Storage backends** (selected by `STORAGE_BACKEND` env var):

| Backend | Value | Description |
| :--- | :--- | :--- |
| `IPFSClusterBackend` | `ipfs_cluster` | Production backend. `store` calls Cluster REST `/add?local=true` and returns when Cluster accepts the CID; `retrieve` calls Kubo `/api/v0/cat`. |
| `FileSystemBackend` | `filesystem` | Dev/test only. Uses MD5 hash as pseudo-CID; stores raw blobs on local disk with atomic write. CIDs are **not** real IPFS CIDs. |

Configuration for the IPFS backend:
```
STORAGE_BACKEND=ipfs_cluster
STORAGE_ENDPOINTS_FILE=/config/storage-endpoints.json
```

### 7.3 `MetadataService` in `dark-core-lib`

Services (minter, resolver) never call `dark-store-api` directly. Instead, they use the `MetadataService` class from `dark-core-lib`, which abstracts Level 1/2 operations:

```
MetadataService
  ├── store_level2(content, content_type, schema) → level2_cid
  │     Stores the raw original record as-is.
  │
  ├── store_level1_with_level2_reference(level1, level2_cid) → (Level1Metadata, level1_cid)
  │     Injects level2_cid into the Level-1 JSON, then stores it.
  │     Returns both the validated model and the CID of the stored Level-1 doc.
  │
  ├── load_level1(level1_cid) → Level1Metadata
  │     Fetches from storage and validates against Level1Metadata schema.
  │
  └── load_level2(level1) → StoredDocument
        Reads level1.original_metadata.cid, fetches raw bytes from storage.
        Returns content + original Content-Type.
```

Two `MetadataStorage` implementations are available:
- `StoreApiMetadataStorage` — talks to `dark-store-api` over HTTP (used in all containers)
- `FileSystemMetadataStorage` — reads/writes from local path (used in integration tests)

### 7.4 End-to-End Storage Flow

```
                 Minter Worker                dark-store-api        dark-ipfs
                      │                            │                    │
1. Level 2 store:     │── POST /v1/store ─────────►│── /api/v0/add ────►│
   (original XML)     │                            │◄── CID (L2) ───────│
                      │◄── {"cid": L2_CID} ────────│── /pins/{cid} ────►│
                      │                            │                    │
2. Level 1 store:     │── POST /v1/store ─────────►│── /api/v0/add ────►│
   (JSON + L2_CID)    │                            │◄── CID (L1) ───────│
                      │◄── {"cid": L1_CID} ────────│── /pins/{cid} ────►│
                      │                            │                    │
3. Blockchain write:  │── dARK.create_ark(naan, name, url, L1_CID) ─────►│ (contract)
                      │                                                   │
                 Resolver                    dark-store-api        dark-ipfs
                      │                            │                    │
4. ?metadata read:    │── GET /v1/retrieve/L1_CID ►│── /api/v0/cat ────►│
                      │◄── Level1 JSON ────────────│◄── bytes ──────────│
                      │── GET /v1/retrieve/L2_CID ►│── /api/v0/cat ────►│
                      │◄── raw XML/JSON ───────────│◄── bytes ──────────│
```

---

## 8. Key Improvements vs v1

| Feature | dARK v1 (Legacy) | dARK v2 (Current) |
| :--- | :--- | :--- |
| **Permissions** | Mixed in `dARK.sol` | Delegated to `Authority.sol` via `IAuthority` |
| **Identity** | Implicit | Explicit UUID ↔ Wallet binding |
| **Key management** | Off-chain only | Encrypted private key stored on-chain |
| **ARK metadata** | URL only | URL + IPFS CID + owner + timestamps |
| **Scalability** | Harder to upgrade logic | Contracts can be upgraded independently |
| **Gas Cost** | Higher (complex logic) | Optimized (separation of concerns) |

---

## 9. Developer Reference

### SDK Usage (`dark-core-lib`)

The recommended way to interact with the contracts from Python:

```python
from dark_core_lib import DARKCoreClient

# Read-only client (requires DARK_RPC_URL and DARK_CONTRACT_ADDRESS)
client = DARKCoreClient.from_env(read_only=True)
url = client.arks.resolve("12345", "my-doc")
ark = client.arks.get("12345", "my-doc")  # full ARK struct

# Write client (also requires DARK_AUTHORITY_ADDRESS and DARK_ADMIN_PRIVATE_KEY)
client = DARKCoreClient.from_env(read_only=False)

# 1. Register authority (admin wallet signs this)
client.setup_authority("org-uuid-1", naans=["12345"])

# 2. Create ARK (authority wallet signs this — key retrieved from contract)
client.create_ark("org-uuid-1", "12345", "my-doc", url, cid)

# 3. Manage NAANs
client.authorize_naan("org-uuid-1", "67890")
client.revoke_naan("org-uuid-1", "67890")
```

### Raw Web3.py (low-level)

```python
# Register Authority — admin calls this (3 required parameters)
auth.functions.register_authority(
    "uuid-1",           # organization UUID
    authority_wallet,   # authority's Ethereum wallet address
    encrypted_key       # AES-256 encrypted private key, hex encoded
).transact({'from': admin_wallet})

# Authorize NAAN — authority WALLET calls this (not admin)
auth.functions.authorize_naan("12345").transact({'from': authority_wallet})

# Create ARK (gas use depends on payload size; current default is 550k)
dark.functions.create_ark("12345", "my-doc", url, cid).transact(
    {'from': authority_wallet, 'gas': 550000}
)

# Resolve (read-only, no gas)
url = dark.functions.resolve("12345", "my-doc").call()
```

### Common Pitfalls

*   **"Not registered"**: The wallet calling `authorize_naan` is not registered via `register_authority`.
*   **"Wallet already registered"**: A wallet can only represent **one** Authority UUID; each authority needs its own wallet.
*   **"UUID already registered"**: `register_authority` is idempotent per UUID — call `get_authority(uuid)` first to check.
*   **"Not authorized for NAAN"**: The authority wallet never called `authorize_naan` for that NAAN, or the NAAN was revoked.
*   **"Authority inactive"**: `deactivate_authority` was called. Inactive authorities cannot authorize NAANs or create ARKs.
*   **`register_authority` vs `authorize_naan` callers**: `register_authority` is `onlyAdmin` (the contract deployer); `authorize_naan` must be called by the **authority wallet** itself.
