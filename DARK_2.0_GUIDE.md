# dARK 2.0 — Developer Guide

This guide covers everything needed to use the dARK 2.0 stack from a developer's perspective: the ARK identifier format, on-chain data structures, the ARK lifecycle, and how to interact with the system using the Python SDK (`dark-core-lib`) or the REST APIs directly.

---

## Table of Contents

1. [ARK Identifier Format](#1-ark-identifier-format)
2. [Data Structures](#2-data-structures)
   - [2.1 Authority (on-chain)](#21-authority-on-chain)
   - [2.2 ARK (on-chain)](#22-ark-on-chain)
   - [2.3 SDK Models](#23-sdk-models)
3. [ARK Lifecycle](#3-ark-lifecycle)
4. [Smart Contract Interaction](#4-smart-contract-interaction)
   - [4.1 Authority Contract](#41-authority-contract)
   - [4.2 dARK Contract](#42-dark-contract)
5. [Python SDK (`dark-core-lib`)](#5-python-sdk-dark-core-lib)
   - [5.1 Installation & Configuration](#51-installation--configuration)
   - [5.2 DARKCoreClient](#52-darkccoreclient)
   - [5.3 Authority Operations](#53-authority-operations)
   - [5.4 ARK Operations](#54-ark-operations)
   - [5.5 Batch Publishing (Pipelined)](#55-batch-publishing-pipelined)
6. [Error Handling](#6-error-handling)
7. [Key Design Decisions](#7-key-design-decisions)

---

## 1. ARK Identifier Format

ARK identifiers follow the [ARK Alliance](https://arks.org/) specification. dARK stores and resolves both classical and compact forms.

| Form | Example |
| :--- | :--- |
| Classical | `ark:/12345/abc123` |
| Compact | `ark:12345/abc123` |

Both forms are accepted by the SDK and REST APIs. Internally, the `naan` (Name Assigning Authority Number) and `name` are stored separately.

**Parsing with the SDK:**

```python
from dark_core_lib.ark_id import parse_ark_id

ark = parse_ark_id("ark:/12345/abc123")
print(ark.naan)      # "12345"
print(ark.name)      # "abc123"
print(ark.canonical) # "ark:/12345/abc123"
print(ark.compact)   # "ark:12345/abc123"
```

`parse_ark_id` raises `ARKError` for any malformed input (missing `ark:` prefix, missing slash, empty segments).

---

## 2. Data Structures

### 2.1 Authority (on-chain)

Defined in `Authority.sol`:

```solidity
struct AuthorityData {
    string  uuid;                   // External UUID identifier
    address wallet;                 // Owner wallet
    bool    active;                 // Active status
    string  encrypted_private_key; // AES-256-GCM, hex-encoded (nonce+ciphertext)
}
```

The private key is AES-256-GCM encrypted using a key derived from the admin's private key via SHA-256 (`crypto.py`). This means the admin wallet is the only entity capable of decrypting authority keys. The nonce is prepended to the ciphertext before hex encoding.

NAANs are stored separately in two mappings:

- `_authorized_naans[wallet][naan] → bool` — O(1) authorization check
- `_naan_list[wallet] → string[]` — enumeration list (used by `get_authority`)

### 2.2 ARK (on-chain)

Defined in `dARK.sol`:

```solidity
struct ARK {
    string  name;        // The ARK name segment
    string  naan;        // The NAAN
    string  url;         // Resolution URL
    string  cid;         // IPFS CID for metadata
    address owner;       // Authority wallet at creation time
    uint256 created_at;  // Unix timestamp
    uint256 updated_at;  // Unix timestamp (last update)
}
```

ARKs are stored in `mapping(bytes32 => ARK)` keyed by `keccak256(abi.encodePacked(naan, "/", name))`. This means the storage key never changes even if `url` or `cid` are updated.

### 2.3 SDK Models

The Python SDK (`dark_core_lib.models`) exposes dataclasses that mirror the on-chain structures:

```python
@dataclass
class AuthorityInfo:
    uuid:           str
    wallet_address: str
    naans:          list[str]
    active:         bool

@dataclass
class ARKInfo:
    naan:       str
    name:       str
    url:        str
    cid:        str
    owner:      str
    created_at: datetime   # UTC-aware
    updated_at: datetime   # UTC-aware

    @property
    def ark_id(self) -> str:
        return f"ark:/{self.naan}/{self.name}"

@dataclass
class TxReceiptInfo:
    tx_hash:      str
    status:       int        # 1 = success, 0 = revert
    gas_used:     int | None
    block_number: int | None
```

For batch publishing, two additional models are used:

```python
@dataclass
class ARKPublishOperation:
    ref:    str   # Client-side reference (correlation ID)
    action: str   # "create" | "update"
    naan:   str
    name:   str
    url:    str
    cid:    str

@dataclass
class ARKPublishResult:
    ref:    str
    action: str
    status: str   # "confirmed" | "reverted" | "send_failed" | "ambiguous" | "not_sent"
    error:  str | None
```

---

## 3. ARK Lifecycle

ARKs managed through `dark-core-minter-api` pass through a state machine before becoming live on-chain. Identifiers are minted via NOID and stored in PostgreSQL until the background workers settle them on-chain.

| State | Code | Meaning |
| :--- | :---: | :--- |
| `reserved` | `R` | NOID-based ID allocated; no metadata yet |
| `draft` | `D` | Metadata received; pending IPFS persistence + on-chain `create_ark` |
| `update` | `U` | Metadata updated; pending on-chain `update_ark` |
| `published` | `P` | Live on blockchain — `url` and `cid` come from the contract |
| `tombstone` | `T` | Soft-deleted; irreversible |

**Transitions:**

```
reserve()     → R
PUT metadata  → R ──► D  (new create queued)
              → P ──► U  (update queued)
Worker runs   → D ──► P  (create_ark confirmed)
              → U ──► P  (update_ark confirmed)
DELETE        → any ──► T
```

If you interact with the contracts directly (bypassing the minter), ARKs are `published` as soon as the transaction is confirmed. There is no `reserved` or `draft` state at the contract level.

---

## 4. Smart Contract Interaction

### 4.1 Authority Contract

The Authority contract is managed exclusively through `dark-core-admin-api` in normal operations. The SDK `AuthorityService` provides the same operations for direct integration.

**Admin-only functions** (`onlyAdmin` — called by the admin wallet):

| Function | Purpose |
| :--- | :--- |
| `register_authority(uuid, wallet, encrypted_private_key)` | Register a new authority |
| `deactivate_authority(uuid)` | Permanently deactivate; no undo |
| `get_authority_key(uuid)` | Retrieve the encrypted private key |

**Authority-wallet functions** (`onlyActiveAuthority` — called by the authority's own wallet):

| Function | Purpose |
| :--- | :--- |
| `authorize_naan(naan)` | Authorize a NAAN for minting |
| `revoke_naan(naan)` | Revoke NAAN authorization |

**View functions** (anyone):

| Function | Returns |
| :--- | :--- |
| `get_authority(uuid)` | `(wallet, naans[], active)` |
| `is_authorized(wallet, naan)` | `bool` — also checks `active` flag |
| `is_active_authority(wallet)` | `bool` |
| `get_uuid_by_wallet(wallet)` | `uuid` string |

**Important:** `is_authorized` returns `false` if the authority is deactivated, even if the NAAN was previously authorized. The check is atomic: registered + active + NAAN-authorized.

### 4.2 dARK Contract

The dARK contract does not know about UUIDs. It operates purely on wallet addresses, which it validates through the Authority contract interface (`IAuthority`).

**Write functions:**

| Function | Access | Gas |
| :--- | :--- | :--- |
| `create_ark(naan, name, url, cid)` | Wallet authorized for `naan` | ~200k–300k |
| `update_ark(naan, name, url, cid)` | Original owner **and** still authorized for `naan` | ~50k–100k |

`update_ark` enforces dual ownership: the sender must be both the original creator (`owner` field) and currently authorized for the NAAN. If the authority was revoked and re-authorized, a different wallet would be blocked from updating ARKs created by the previous wallet.

**View functions** (anyone):

| Function | Returns |
| :--- | :--- |
| `resolve(naan, name)` | `url` string; reverts with `"ARK not found"` if absent |
| `get_ark(naan, name)` | Full `ARK` struct as tuple |
| `ark_exists(naan, name)` | `bool` — safe check, no revert |
| `get_ark_count()` | `uint256` total ARKs created |
| `get_authority_contract()` | Address of the linked Authority contract |

---

## 5. Python SDK (`dark-core-lib`)

### 5.1 Installation & Configuration

```bash
pip install dark-core-lib
```

The SDK is configured via environment variables or a `.env` file:

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `DARK_RPC_URL` | ✅ | — | HTTP(S) URL of the Ethereum/Besu RPC node |
| `DARK_CONTRACT_ADDRESS` | ✅ | — | `dARK.sol` deployed address (`0x...`) |
| `DARK_AUTHORITY_ADDRESS` | write only | — | `Authority.sol` deployed address |
| `DARK_ADMIN_PRIVATE_KEY` | write only | — | Admin wallet private key (`0x...`, 32 bytes) |
| `DARK_CHAIN_ID` | no | — | Expected chain ID; connection is rejected on mismatch |
| `DARK_READ_ONLY` | no | `true` | Set to `false` to enable write operations |
| `DARK_VALIDATE_CHAIN_ID` | no | `true` | Disable chain ID check (useful in tests) |
| `DARK_GAS_LIMIT` | no | `550000` | Default gas limit per transaction |
| `DARK_TX_TIMEOUT_SECONDS` | no | `120` | Transaction confirmation timeout |

In **read-only mode** (`DARK_READ_ONLY=true`), only `DARK_RPC_URL` and `DARK_CONTRACT_ADDRESS` are required. No admin key is loaded and write operations raise `ReadOnlyModeError`.

`DARK_ADMIN_PRIVATE_KEY` is the SDK-facing variable name inside a service. The
deployer supplies the administration signer to Admin API and the separate
minter signer to Minter API under that service-local name. Resolver and Store
API do not receive blockchain signing keys. See
[Deployer Operations](docs/deployer-operations.md#3-signer-roles).

### 5.2 DARKCoreClient

`DARKCoreClient` is the main entry point. It wires together the Authority and ARK services.

```python
from dark_core_lib import DARKCoreClient

# From .env file in the current directory
client = DARKCoreClient.from_env()

# With explicit path
client = DARKCoreClient.from_env(env_path="/path/to/.env")

# Force read-only regardless of .env
client = DARKCoreClient.from_env(read_only=True)

# Force write mode
client = DARKCoreClient.from_env(read_only=False)

# From explicit config object
from dark_core_lib.config import CoreConfig
config = CoreConfig(
    rpc_url="http://localhost:8545",
    dark_contract_address="0xabc...",
    authority_contract_address="0xdef...",
    admin_private_key="0x...",
    read_only=False,
    chain_id=2025,
)
client = DARKCoreClient(config)
```

The client exposes three service objects:

- `client.arks` — `ARKService`
- `client.authorities` — `AuthorityService` (only if `DARK_AUTHORITY_ADDRESS` is set)
- `client.chain` — `ChainService` (block number, balance, raw transfers)

### 5.3 Authority Operations

All write operations require write mode and a configured `DARK_AUTHORITY_ADDRESS`.

**Setup a new authority (one-shot):**

```python
# Creates wallet, funds it (default 0.01 ETH), registers on-chain,
# and authorizes all listed NAANs.
info = client.setup_authority(
    uuid="org-uuid-12345",
    naans=["12345", "67890"],
    fund_amount_wei=client.w3.to_wei(0.05, "ether"),  # optional
)
print(info.wallet_address)  # Newly created wallet
```

`setup_authority` is idempotent-safe: it raises `AuthorityAlreadyExistsError` if the UUID is already registered.

**Lookup:**

```python
info = client.get_authority_by_uuid("org-uuid-12345")
# AuthorityInfo(uuid=..., wallet_address=..., naans=[...], active=True)

info = client.get_authority_by_wallet("0xabc...")
naans = client.get_authorized_naans("org-uuid-12345")  # list[str]

authorized = client.is_authorized_for_naan("org-uuid-12345", "12345")  # bool
```

**Manage NAANs:**

```python
# Individual
client.authorize_naan("org-uuid-12345", "99999")
client.revoke_naan("org-uuid-12345", "99999")

# Batch (via AuthorityService directly)
client.authorities.authorize_naans("org-uuid-12345", ["11111", "22222"])
```

`authorize_naan` and `revoke_naan` are idempotent: if the NAAN is already in the desired state, the method returns a no-op receipt without submitting a transaction.

**Deactivate:**

```python
client.deactivate_authority("org-uuid-12345")
```

Deactivation is irreversible at the contract level. The authority's ARKs remain on-chain and resolvable, but no new ARKs can be minted.

**Fund wallet:**

```python
client.fund_authority_wallet("org-uuid-12345", amount_wei=client.w3.to_wei(0.1, "ether"))
```

### 5.4 ARK Operations

**Read operations (available in read-only mode):**

```python
# Resolve to URL — raises ARKNotFoundError if absent
url = client.arks.resolve("12345", "abc123")

# Safe existence check
exists = client.arks.exists("12345", "abc123")  # bool

# Full metadata
ark_info = client.arks.get("12345", "abc123")
# ARKInfo(naan="12345", name="abc123", url="...", cid="...", owner="0x...",
#         created_at=datetime(...), updated_at=datetime(...))
print(ark_info.ark_id)  # "ark:/12345/abc123"
```

Alternatively, use the top-level client shortcuts:

```python
url = client.arks.resolve("12345", "abc123")
info = client.arks.get("12345", "abc123")
```

**Write operations (write mode required):**

```python
# Create a new ARK
ark_info = client.create_ark(
    uuid="org-uuid-12345",
    naan="12345",
    name="abc123",
    url="https://example.org/item/1",
    cid="bafyreiabc...",
    fetch_result=True,   # default: re-reads state after tx
)

# Update an existing ARK
ark_info = client.update_ark(
    uuid="org-uuid-12345",
    naan="12345",
    name="abc123",
    url="https://example.org/item/1/v2",
    cid="bafyreixyz...",
)
```

`create_ark` and `update_ark` sign and submit the transaction using the authority's wallet (decrypted from the on-chain encrypted key), wait for confirmation, and return an `ARKInfo` if `fetch_result=True`. Set `fetch_result=False` to skip the post-tx read.

### 5.5 Batch Publishing (Pipelined)

For bulk operations, `publish_operations` uses a sliding-window nonce pipeline to maximize throughput without waiting for each transaction to confirm before sending the next.

```python
from dark_core_lib.models import ARKPublishOperation

ops = [
    ARKPublishOperation(ref="item-001", action="create",
                        naan="12345", name="abc001",
                        url="https://example.org/1", cid="bafyrei..."),
    ARKPublishOperation(ref="item-002", action="create",
                        naan="12345", name="abc002",
                        url="https://example.org/2", cid="bafyrei..."),
    ARKPublishOperation(ref="item-003", action="update",
                        naan="12345", name="abc003",
                        url="https://example.org/3/v2", cid="bafyrei..."),
    # ... up to thousands of operations
]

results = client.arks.publish_operations(
    uuid="org-uuid-12345",
    operations=ops,
    pipeline_size=20,   # window size; default 20
)

for r in results:
    print(r.ref, r.status, r.error)
```

**Pipeline behaviour:**

1. The current `pending` nonce is fetched once before the first window.
2. All transactions in the window are pre-signed with sequential nonces and sent concurrently (non-blocking `send_raw_transaction`).
3. Receipts are collected after all sends complete.
4. If a transaction's receipt cannot be determined (`ambiguous`), `stop_pipeline=True` is set and all subsequent operations are marked `not_sent` to avoid nonce gaps.

**Result statuses:**

| Status | Meaning |
| :--- | :--- |
| `confirmed` | Transaction mined with `status == 1` |
| `reverted` | Transaction mined with `status == 0` (contract reverted) |
| `send_failed` | RPC rejected the `send_raw_transaction` call |
| `ambiguous` | Receipt could not be fetched; on-chain state unknown |
| `not_sent` | Skipped because a prior `ambiguous` stopped the pipeline |

---

## 6. Error Handling

All SDK exceptions inherit from `DarkCoreError`:

| Exception | Raised when |
| :--- | :--- |
| `ConfigurationError` | Missing or invalid environment variables at startup |
| `ConnectionError` | RPC node unreachable or chain ID mismatch |
| `ReadOnlyModeError` | Write operation called in read-only mode |
| `TransactionError` | Transaction confirmed with `status == 0` (revert) |
| `AuthorityError` | Generic authority contract failure |
| `AuthorityNotFoundError` | UUID or wallet not registered |
| `AuthorityAlreadyExistsError` | `setup_authority` called for existing UUID |
| `AuthorizationError` | NAAN operation on inactive or unregistered wallet |
| `ARKError` | Generic dARK contract failure |
| `ARKNotFoundError` | `resolve` or `get` called for non-existent ARK |

**Typical pattern:**

```python
from dark_core_lib.exceptions import ARKNotFoundError, TransactionError

try:
    url = client.arks.resolve("12345", "abc123")
except ARKNotFoundError:
    print("ARK does not exist yet")
except TransactionError as e:
    print(f"Revert: {e} — tx: {e.tx_hash}")
```

`TransactionError` carries `.tx_hash`, `.gas_used`, `.status`, and `.block_number` as attributes for diagnostics.

---

## 7. Key Design Decisions

### Two-contract architecture

Authority and ARK storage are separated into `Authority.sol` and `dARK.sol`. The dARK contract only stores ARK data; all permission logic is delegated to `IAuthority.is_authorized(wallet, naan)`. This allows replacing or upgrading the authority model without re-deploying the ARK storage contract.

### On-chain encrypted key storage

Each authority's private key is encrypted with AES-256-GCM, using a key derived from the admin private key via SHA-256, and stored directly on-chain. This means:

- The admin wallet is the single root of trust for all authority key recovery.
- No separate key management service is needed.
- Anyone can read the ciphertext from the chain, but only the admin key can decrypt it.
- If the admin key is lost, all authority keys become unrecoverable.

### UUID-to-wallet binding

The admin wallet registers the authority, creates the authority wallet on its behalf, and stores the encrypted key. From the authority's perspective, the UUID is the stable external identifier; the wallet is an internal implementation detail managed by the admin. This allows wallet rotation by deactivating the old authority and registering a new one with the same UUID prefix convention.

### ARK ownership is wallet-bound

The `owner` field in the ARK struct stores the wallet address at creation time, not the UUID. If an authority's wallet is replaced (deactivate + re-register), the new wallet cannot update ARKs created by the old wallet. This is intentional: it prevents impersonation after wallet compromise. Migrating ARKs to a new wallet requires explicit `update_ark` calls from the old wallet before deactivation.

### NAAN authorization is per-wallet, not per-UUID

`_authorized_naans` maps `wallet → naan → bool`. When a wallet is deactivated and a new one registered for the same logical organization, the new wallet starts with no authorized NAANs and must call `authorize_naan` again. This is consistent with the wallet-bound ownership model above.
