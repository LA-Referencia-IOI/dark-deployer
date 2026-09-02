# dARK 2.0 - API & Library Developer Reference

> This guide is part of the [dARK 2.0 Documentation](README.md).
> See also: [Developer Guide](DARK_2.0_GUIDE.md) | [Architecture](DARK_2.0_ARCHITECTURE.md)

This guide provides technical specifications for developers integrating with the dARK 2.0 stack: REST APIs, smart contract interfaces, events, and SDK usage.

**Service ports:**

| Service | Port | Auth |
| :--- | :--- | :--- |
| `dark-core-admin-api` | 8000 | mTLS (all endpoints) |
| `dark-core-minter-api` | 8001 | mTLS (read/worker) or authority identity (write) |
| `dark-core-resolver-api` | 8002 | Public (no auth) |
| `dark-store-api` | 8003 | No auth (internal network) |

---

## 1. Admin API (`dark-core-admin-api` — port 8000)

All endpoints require **mTLS**. The base path prefix for the admin router is `/admin`; for ARK stats it is `/arks`.

### 1.1 Authority Management

#### `POST /admin/authority` — Register authority
Creates a new authority: generates a wallet, encrypts its private key on-chain, funds it from the admin account, registers it in the `Authority` contract, and authorizes all specified NAANs.

**Request body:**
```json
{
  "uuid": "org-uuid-12345",
  "naans": ["12345", "67890"],
  "fund_amount_eth": 0.01
}
```
`fund_amount_eth` is optional (uses `DEFAULT_FUND_AMOUNT_ETH` env var if omitted).

**Response `200`** — `AuthorityResponse`:
```json
{
  "uuid": "org-uuid-12345",
  "wallet_address": "0xABCD...",
  "naans": ["12345", "67890"],
  "active": true,
  "balance_eth": 0.01
}
```

#### `GET /admin/authority/{uuid}` — Get authority info
Returns authority wallet, NAANs, active status, and wallet balance.

**Response `200`** — `AuthorityResponse` (same structure as above).

#### `POST /admin/authority/{uuid}/authorize-naan` — Authorize NAAN
Adds a NAAN to an existing authority. Idempotent: if NAAN is already authorized, returns success without a transaction.

**Request body:** `{"naan": "12345"}`

**Response `200`** — `OperationResponse`:
```json
{
  "status": "success",
  "message": "NAAN 12345 authorized for org-uuid-12345",
  "transaction_hash": "0xabc..."
}
```

#### `POST /admin/authority/{uuid}/revoke-naan` — Revoke NAAN
Removes a NAAN from an authority. Idempotent.

**Request body:** `{"naan": "12345"}`

**Response `200`** — `OperationResponse`.

#### `POST /admin/authority/{uuid}/deactivate` — Deactivate authority
Prevents the authority from minting new ARKs. Idempotent.

**Response `200`** — `OperationResponse`.

#### `GET /admin/authority/{uuid}/balance` — Get wallet balance

**Response `200`**:
```json
{"uuid": "...", "wallet_address": "0x...", "balance_eth": 0.0095}
```

#### `POST /admin/authority/{uuid}/fund` — Fund authority wallet
Sends ETH from the admin account to an authority wallet.

**Request body:** `{"amount_eth": 0.01}`

**Response `200`** — `OperationResponse`.

#### `GET /admin/status` — System status

**Response `200`** — `AdminStatusResponse`:
```json
{
  "admin_address": "0x...",
  "admin_balance_eth": 1.5,
  "blockchain_connected": true,
  "current_block": 12345,
  "chain_id": 2025
}
```

### 1.2 ARK Stats

#### `GET /arks/count` — Total ARK count

**Response `200`:** `{"count": 42}`

#### `GET /arks/recent?limit=10` — Recent ARKs
`limit` is 1–100, default 10.

**Response `200`:**
```json
{
  "limit": 10,
  "arks": [
    {"pid": "ark:/12345/abc123", "naan": "12345", "name": "abc123",
     "owner": "0x...", "url": "https://...", "cid": "bafyrei..."}
  ]
}
```

---

## 2. Minter API (`dark-core-minter-api` — port 8001)

### 2.1 ARK Lifecycle

ARKs transition through these states:

| State | Code | Meaning |
| :--- | :--- | :--- |
| `reserved` | `R` | ID generated, waiting for metadata |
| `draft` | `D` | Metadata provided, pending metadata persistence + on-chain creation |
| `update` | `U` | Updated metadata pending on-chain update |
| `published` | `P` | Live on blockchain |
| `tombstone` | `T` | Soft-deleted, cannot be modified |

**`ARKResponse` schema** (returned by all ARK endpoints):
```json
{
  "ark": "ark:/12345/abc123",
  "state": "D",
  "target": "https://example.org/item/1",
  "metadata_cid": "bafyrei...",
  "metadata_schema": "dublin_core",
  "minimal_metadata": { ... },
  "level1_cid": "bafyrei...",
  "level2_cid": "bafyrei...",
  "client_item_id": "my-local-id-001"
}
```
All fields except `ark` and `state` are optional (`null` when not yet available).

### 2.2 ARK Endpoints

Auth note: write endpoints (`POST`, `PUT`, `DELETE`) require **authority identity** (certificate CN = authority UUID). Read endpoints require **mTLS**.

#### `POST /arks` — Reserve ARK
Allocates a NOID-based ARK identifier and persists a `reserved` record. Validates that the authority is authorized for the NAAN on-chain (result is cached).

**Auth:** authority identity  
**Request body:**
```json
{"authority_id": "org-uuid-12345", "naan": "12345"}
```
**Response `201`** — `ARKResponse` with `state: "R"`.

**Errors:**
| Code | Cause |
| :--- | :--- |
| `403` | Authority not authorized for NAAN |
| `409` | Collision after retries (retry the call) |

#### `POST /arks/batch` — Batch reserve ARKs
Reserves multiple ARKs in one request. Authorization is validated once for the batch. Each item gets its own `client_item_id` for correlation.

**Auth:** authority identity  
**Request body:**
```json
{
  "authority_id": "org-uuid-12345",
  "naan": "12345",
  "items": [
    {"client_item_id": "local-001"},
    {"client_item_id": "local-002"}
  ]
}
```
**Response `200`** — `ARKBatchResponse`:
```json
{
  "results": [ ... ],
  "errors": [{"client_item_id": "local-003", "error": "...", "index": 2}]
}
```
`errors` is `null` when all items succeeded.

#### `GET /arks/{ark:path}` — Get ARK
Returns the ARK record from the database; falls back to the blockchain if not found locally. For `published` ARKs, merges DB metadata with on-chain data (URL and CID come from the contract).

**Auth:** mTLS  
**Path:** full ARK identifier, e.g. `ark:/12345/abc123`  
**Response `200`** — `ARKResponse`.

**Errors:**
| Code | Cause |
| :--- | :--- |
| `400` | Invalid ARK format / bad checkdigit |
| `404` | Not found in DB or blockchain |

#### `PUT /arks/{ark:path}` — Update metadata → DRAFT
Submits Level-1 (minimal) + Level-2 (original) metadata and transitions the ARK:
- `reserved` → `draft` (new create_ark queued)
- `published` → `update` (update_ark queued)

Metadata is stored in the PostgreSQL DB immediately. The background workers handle IPFS persistence and blockchain submission.

If the ARK exists on-chain but not locally, it is imported as `published` before the update (ownership is verified against the authority wallet).

**Auth:** authority identity  
**Request body:**
```json
{
  "authority_id": "org-uuid-12345",
  "target": "https://example.org/item/1",
  "minimal_metadata": {
    "ark": "ark:/12345/abc123",
    "title": "My Item",
    "creator": ["Alice"],
    "date": "2026-05-21",
    "original_metadata": {"schema": "dublin_core", "media_type": "application/xml", "cid": null}
  },
  "original_metadata": "<oai_dc:dc>...</oai_dc:dc>",
  "metadata_schema": "dublin_core",
  "metadata_media_type": "application/xml"
}
```

**Response `200`** — `ARKResponse` with updated state.

**Errors:**
| Code | Cause |
| :--- | :--- |
| `400` | Invalid ARK format |
| `403` | Authority doesn't own ARK |
| `404` | ARK not found |
| `409` | Tombstoned / already in draft / already pending update |
| `422` | Level-1 metadata validation failed |
| `502` | Blockchain read failed during on-chain import |

#### `DELETE /arks/{ark:path}` — Tombstone ARK
Marks an ARK as deleted. **Irreversible.** Only the owning authority can tombstone.

**Auth:** authority identity  
**Response `204`** No Content.

**Errors:**
| Code | Cause |
| :--- | :--- |
| `403` | Authority doesn't own the ARK |
| `404` | ARK not found |
| `409` | Already tombstoned |

### 2.3 Authority Endpoints (read-only)

All require **mTLS**.

#### `GET /authority/{uuid}` — Get authority info
Returns `AuthorityResponse` (same schema as Admin API, without `balance_eth`).

#### `GET /authority/{uuid}/naans` — List authorized NAANs

**Response `200`:** `{"uuid": "...", "naans": ["12345", "67890"]}`

#### `GET /authority/{uuid}/authorized/{naan}` — Check NAAN authorization

**Response `200`:** `{"uuid": "...", "naan": "12345", "authorized": true}`

### 2.4 Worker Status Endpoints

No authentication required (internal network only).

#### `GET /worker/status?detail=simple|full` — Worker status

`detail=simple` (default) returns a compact operational summary:
```json
{
  "overall": "ok",
  "message": "Metadata worker is idle; Chain worker is idle.",
  "rpc": {"available": true, "latency_ms": 12},
  "workers": {
    "metadata": {
      "state": "idle",
      "alive": true,
      "last_heartbeat_seconds": 4,
      "last_cycle": {"processed": 0, "succeeded": 0, "failed": 0,
                     "duration_seconds": 0.12, "page_size": 50, "full_page": false,
                     "next_action": "sleep"},
      "queue": {"pending": 0, "ready": 0, "delayed": 0}
    },
    "chain": { ... }
  },
  "errors": {"retrying": 0, "permanent": 0},
  "arks": {"reserved": 10, "draft": 2, "update": 0, "published": 500, "tombstone": 1}
}
```

`detail=full` adds per-worker heartbeat details (host, PID, instance ID, cycle utilization, estimated drain time), RPC retry config, and full queue breakdowns per stage.

**Worker states:** `idle` | `working` | `backlogged` | `delayed` | `paused_rpc_unavailable` | `stopped` | `stale` | `unknown` | `disabled`

**Overall health:** `ok` | `degraded` | `down`

#### `GET /worker/errors/permanent?stage=all|metadata|chain&page=1&page_size=50` — Permanent errors
Returns paginated list of ARKs that exhausted all retry attempts. Used for operational triage.

**Response `200`:**
```json
{
  "total": 3,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "ark": "ark:/12345/abc123",
      "state": "D",
      "stage": "metadata",
      "authority_id": "org-uuid",
      "publish_retry_count": 5,
      "publish_last_error": "Connection timeout",
      "metadata": {"level1_cid": null, "original_cid": null, "original_schema": "dublin_core"}
    }
  ]
}
```

---

## 3. Resolver API (`dark-core-resolver-api` — port 8002)

Public, no authentication.

#### `GET /arks/{ark:path}` — Resolve ARK (default)
Redirects to the ARK's target URL. HTTP 301 or 302 depending on `RESOLVER_REDIRECT_STATUS` config (default 301).

**Path:** full ARK, e.g. `ark:/12345/abc123`

#### `GET /arks/{ark:path}?info` — Public info
Returns a JSON document with on-chain ARK metadata (URL, owner, timestamps, CID).

**Response `200`:**
```json
{
  "ark": "ark:/12345/abc123",
  "url": "https://example.org/item/1",
  "owner": "0x...",
  "cid": "bafyrei...",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

#### `GET /arks/{ark:path}?metadata` — Metadata view
Fetches Level-1 and Level-2 metadata from the storage layer and returns the original record with its MIME type.

**HEAD** is supported on all three variants.

**Errors** (all variants):
| Code | Cause |
| :--- | :--- |
| `400` | Malformed ARK identifier |
| `400` | Both `?info` and `?metadata` in same request |
| `404` | ARK not found on-chain |
| `502` | Blockchain node unreachable |
| `502` | Metadata CID missing on published ARK (on `?info`) |
| `404` | Metadata document not found in storage (on `?metadata`) |

---

## 4. Store API (`dark-store-api` — port 8003)

Stateless content-addressed storage abstraction. Backend is selected by `STORAGE_BACKEND` env var (`ipfs_cluster` for production, `filesystem` for dev/test).

#### `POST /v1/store` — Store content
Accepts raw bytes with any `Content-Type`.

**Response `200`:**
```json
{"cid": "bafyrei...", "size": 4096}
```
In `ipfs_cluster` mode, the CID is a real IPFS CIDv1. In `filesystem` mode, it is an MD5 hex hash (not an IPFS CID).

#### `GET /v1/retrieve/{cid}` — Retrieve content
Returns raw bytes. Content-Type is `application/octet-stream`.

**Errors:** `404` if not found.

#### `GET /v1/status/{cid}` — Pin status

**Response `200`:**
```json
{"cid": "bafyrei...", "status": "pinned", "replication": {"total_replicas": 2, "local_replicas": 2, "remote_replicas": 0, "sites": {"site-a": 2}, "purge_target_met": true, "checked_at": "2026-09-01T12:00:00Z"}}
```

#### `GET /health` — Health check

**Response `200`:** `{"status": "healthy"}`

---

## 5. Contract Interfaces

### 5.1 Authority Contract (`Authority`)
Manages identity and write permissions.

**View Functions**
*   `is_authorized(address wallet, string naan) returns (bool)`
    *   Checks if a wallet has permission to create/update ARKs for a given NAAN.
*   `get_authority(string uuid) returns (address wallet, string[] naans, bool active)`
    *   Returns full authority details. Reverts if UUID is not registered.
*   `get_uuid_by_wallet(address wallet) returns (string uuid)`
    *   Reverse lookup. Reverts if wallet is not registered.

**Write Functions**
*   `register_authority(string uuid, address wallet, string encrypted_private_key)`
    *   *Access*: Admin only (`onlyAdmin` modifier).
    *   *Gas*: ~80k–120k.
    *   *Params*: `encrypted_private_key` — AES-256 encrypted private key, hex-encoded. Stored on-chain; retrieved later by minter workers to sign ARK transactions.
    *   *Errors*: `"UUID already registered"`, `"Wallet already registered"`, `"Empty encrypted key"`.
*   `get_authority_key(string uuid) returns (string encrypted_private_key)`
    *   *Access*: Admin only.
    *   Returns the encrypted private key for the authority.
*   `authorize_naan(string naan)`
    *   *Access*: `onlyActiveAuthority` — called by the **authority wallet** (not admin).
    *   *Gas*: ~60k.
    *   *Errors*: `"NAAN already authorized"`.
*   `revoke_naan(string naan)`
    *   *Access*: `onlyActiveAuthority` — authority wallet.
*   `deactivate_authority(string uuid)`
    *   *Access*: Admin only.

### 5.2 dARK Contract (`dARK`)
Manages ARK lifecycle and storage.

**View Functions**
*   `resolve(string naan, string name) returns (string url)`
    *   Primary resolution function. Reverts with `"ARK not found"` if the ARK does not exist.
*   `ark_exists(string naan, string name) returns (bool)`
    *   Safe check without revert.
*   `get_ark(string naan, string name) returns (tuple)`
    *   Returns: `(name, naan, url, cid, owner, created_at, updated_at)`.
    *   Reverts with `"ARK not found"`.

**Write Functions**
*   `create_ark(string naan, string name, string url, string cid)`
    *   *Access*: Wallet authorized for `naan` via the Authority contract.
    *   *Gas*: **High** (~200k–300k) due to string storage.
    *   *Errors*: `"Not authorized for NAAN"`, `"ARK already exists"`.
*   `update_ark(string naan, string name, string url, string cid)`
    *   *Access*: Original owner **AND** currently authorized for NAAN.
    *   *Errors*: `"Not ARK owner"`, `"Not authorized for NAAN"`.

---

## 6. Event Indexing

For high-performance APIs, index these events instead of making RPC calls.

### Authority Events
```solidity
event AuthorityRegistered(string indexed uuid, address indexed wallet);
event NAANAuthorized(address indexed wallet, string naan);
event NAANRevoked(address indexed wallet, string naan);
```
*   **Indexing Strategy**: Monitor `AuthorityRegistered` to build a local `wallet → uuid` map. Monitor `NAANAuthorized` / `NAANRevoked` to maintain the set of NAANs per authority.

### dARK Events
```solidity
event ARKCreated(string naan, string name, address indexed owner, string url, string cid);
event ARKUpdated(string naan, string name, string url, string cid);
```
*   **Indexing Strategy**: `ARKCreated` contains all data needed to serve resolution requests off-chain — no need to query contract storage if you index this event. The unique identifier is the pair (`naan`, `name`).

---

## 7. Integration Guidelines

### 7.1 Gas & Transaction Safety
*   **String Handling**: dARK relies on string keys (`naan`, `name`). These are expensive in EVM. Use a safe gas buffer (e.g. 500k for creation).
*   **Status Checks**: Always check `receipt.status == 1`. `create_ark` may revert silently if the authority lost authorization or the ARK already exists, depending on the client library.
*   **Nonce Management**: For bulk operations, use `ARKService.publish_operations` which handles sequential nonce pipelining automatically. Do not manage nonces manually — gaps cause transactions to queue indefinitely in the mempool.

### 7.2 Error Handling Map
| Error String | Cause | Action |
| :--- | :--- | :--- |
| `Authority not found` | UUID lookup on non-existent auth | Register authority or check UUID |
| `Wallet already registered` | Registering a wallet twice | Use `get_uuid_by_wallet` to find existing UUID |
| `Not authorized for NAAN` | Wallet tries to create ARK in restricted NAAN | Call `authorize_naan` first |
| `ARK already exists` | Duplicate creation | Use `update_ark` instead |
| `Not ARK owner` | Update attempt by non-owner | Only original creator can update |

### 7.3 Stack Depth Warning
Previous versions had "Stack too deep" issues. v2.0 fixes this by inlining logic. If you fork the contracts and add modifiers to `create_ark` or `update_ark`, you may hit this limit again.
