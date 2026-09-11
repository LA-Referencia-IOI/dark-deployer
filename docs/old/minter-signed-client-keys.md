# Minter Signed Client Keys

This document specifies a deployment-key style authentication model for dARK Minter clients.

The goal is to replace production use of plain `X-Authority-Id` with signed requests. A client proves it controls a private key whose public key was registered by an administrator for a specific authority.

## Summary

Each authority can have one or more client public keys registered in the Admin API. A Minter verifies incoming mutating requests by checking a cryptographic signature over a canonical request string.

The Admin API is the source of truth for authority client keys. Each Minter keeps a local cached copy so it can validate requests efficiently and tolerate short Admin API outages.

```text
Client private key
  signs request
        |
        v
Minter API
  validates timestamp, nonce, body hash, key, signature
        |
        v
Admin API
  source of truth for authority public keys
        |
        v
Minter local DB/cache
  cached public keys and used nonces
```

## Current Problem

Today, when `MTLS_ENABLED=false`, mutating Minter endpoints accept an authority identity from:

```text
X-Authority-Id
X-Authority-UUID
```

The Minter verifies that this header matches the `authority_id` in the request body. That prevents accidental mismatch, but it does not authenticate the caller.

Anyone who can reach the Minter and knows an `authority_id` can impersonate that authority in development-header mode.

## Design Goals

- Keep local notebooks and development workflows simple.
- Give production clients a portable authentication mechanism.
- Avoid requiring every Minter to be manually configured with every client key.
- Let Admin API remain the source of truth for authority key registration.
- Let multiple Minters independently validate requests.
- Support key revocation and rotation.
- Prevent request replay.
- Avoid exposing private keys to Minter or Admin.
- Keep blockchain authority wallets separate from client API keys.

## Non-Goals

- This is not SSH login.
- This does not replace on-chain authority wallets.
- This does not sign blockchain transactions from the client.
- This does not make the Minter stateless.
- This does not remove mTLS as a possible outer transport security layer.

## Actors

### Client

An external system, script, notebook or institution process that calls the Minter API.

The client owns a private signing key.

### Admin API

The central authority administration service. It owns the registry of public keys allowed to act for each authority.

### Minter API

One or more Minter instances that receive client requests. Each Minter verifies request signatures and caches public keys locally.

### Authority

The dARK authority UUID. It is the authorization subject for ARK reservation and update operations.

## Key Model

Recommended key type for v1:

```text
ssh-ed25519
```

The client generates a keypair:

```bash
ssh-keygen -t ed25519 -f dark_client_key -C "authority-id/client-label"
```

The client sends only the public key to the Minter administrator:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... authority-id/client-label
```

The Admin API stores:

- `authority_id`
- `key_id`
- `public_key`
- `key_type`
- `label`
- `scopes`
- `active`
- `created_at`
- `revoked_at`
- `expires_at`

`key_id` should be derived from the public key fingerprint. Recommended format:

```text
SHA256:<base64url fingerprint>
```

This is conceptually similar to GitHub deployment keys: the public key is authorized server-side, and the private key stays with the client.

## Request Signing

Each mutating Minter request must include signed authentication headers.

### Required Headers

```http
X-DARK-Authority-Id: resolver-e2e-1779541393
X-DARK-Key-Id: SHA256:abc123...
X-DARK-Timestamp: 2026-05-25T12:34:56Z
X-DARK-Nonce: 8f85b4ea-5a7d-4e3e-8d9b-8af4515f03ad
X-DARK-Content-SHA256: 8f434346648f6b96df89dda901c5176b...
X-DARK-Signature-Alg: ssh-ed25519
X-DARK-Signature: <base64 signature>
```

### Body Hash

`X-DARK-Content-SHA256` is the lowercase hex SHA-256 digest of the exact request body bytes.

For empty bodies:

```text
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

### Canonical Request

The signature must cover a canonical string:

```text
DARK1
<METHOD>
<PATH_WITH_QUERY>
<AUTHORITY_ID>
<KEY_ID>
<TIMESTAMP>
<NONCE>
<CONTENT_SHA256>
```

Example:

```text
DARK1
PUT
/api/v1/arks/ark:12345/x0000001
resolver-e2e-1779541393
SHA256:abc123...
2026-05-25T12:34:56Z
8f85b4ea-5a7d-4e3e-8d9b-8af4515f03ad
8f434346648f6b96df89dda901c5176b...
```

Rules:

- `METHOD` is uppercase.
- `PATH_WITH_QUERY` is the request path plus query string exactly as received by FastAPI, excluding scheme and host.
- `AUTHORITY_ID` is the normalized authority UUID from `X-DARK-Authority-Id`.
- `KEY_ID` is the normalized key fingerprint.
- `TIMESTAMP` is UTC ISO-8601 with `Z`.
- `NONCE` is client-generated and unique for the key within the replay window.
- `CONTENT_SHA256` must match the request body bytes.

The leading `DARK1` version marker lets us evolve the canonical format later.

## Replay Protection

The Minter must reject replayed requests.

Validation rules:

- Reject timestamps older than `MINTER_SIGNED_AUTH_MAX_SKEW_SECONDS`.
- Reject timestamps too far in the future.
- Reject any reused `(authority_id, key_id, nonce)` inside the replay window.
- Store accepted nonces until they expire.

Recommended defaults:

```text
MINTER_SIGNED_AUTH_MAX_SKEW_SECONDS=300
MINTER_SIGNED_AUTH_NONCE_TTL_SECONDS=600
```

For a single Minter instance, nonce storage in the local DB is enough.

For multiple Minters behind a load balancer, local nonce storage only prevents replay against the same Minter. To prevent replay across all Minters, use one of these:

- sticky routing per client/key;
- shared nonce store, such as Redis;
- central nonce validation service;
- accept the limited risk during v1 and keep a short timestamp window.

Recommended production target: shared Redis or DB-backed nonce store if multiple Minters are active behind the same public endpoint.

## Admin API Responsibilities

Admin API owns the authority client key registry.

### Proposed Admin DB Table

```sql
CREATE TABLE authority_client_keys (
    id BIGSERIAL PRIMARY KEY,
    authority_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    public_key TEXT NOT NULL,
    key_type TEXT NOT NULL DEFAULT 'ssh-ed25519',
    label TEXT,
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    revoked_at TIMESTAMP NULL,
    expires_at TIMESTAMP NULL,
    UNIQUE (authority_id, key_id)
);

CREATE INDEX idx_authority_client_keys_authority
    ON authority_client_keys (authority_id);
```

If the Admin API remains intentionally stateless, this table can live in a small Admin PostgreSQL database. The key registry should not be stored on-chain in v1.

### Proposed Admin Endpoints

Register a key:

```http
POST /api/v1/admin/authority/{authority_id}/client-keys
```

Request:

```json
{
  "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...",
  "label": "oai-harvester-prod",
  "scopes": ["ark:reserve", "ark:update"],
  "expires_at": null
}
```

Response:

```json
{
  "authority_id": "resolver-e2e-1779541393",
  "key_id": "SHA256:abc123...",
  "key_type": "ssh-ed25519",
  "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...",
  "label": "oai-harvester-prod",
  "scopes": ["ark:reserve", "ark:update"],
  "active": true,
  "created_at": "2026-05-25T12:00:00Z",
  "expires_at": null,
  "revoked_at": null
}
```

List keys:

```http
GET /api/v1/admin/authority/{authority_id}/client-keys
```

Fetch one key for Minter validation:

```http
GET /api/v1/admin/authority/{authority_id}/client-keys/{key_id}
```

Revoke a key:

```http
DELETE /api/v1/admin/authority/{authority_id}/client-keys/{key_id}
```

or:

```http
POST /api/v1/admin/authority/{authority_id}/client-keys/{key_id}/revoke
```

Admin should return inactive/revoked keys to Minters with `active=false`, not a generic 404. This lets the Minter cache revocation for a short TTL.

## Minter Responsibilities

Each Minter validates signed requests and caches public keys locally.

### Proposed Minter DB Tables

Cached public keys:

```sql
CREATE TABLE authority_client_key_cache (
    authority_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    public_key TEXT NOT NULL,
    key_type TEXT NOT NULL,
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    fetched_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP NULL,
    source_updated_at TIMESTAMP NULL,
    PRIMARY KEY (authority_id, key_id)
);
```

Used nonces:

```sql
CREATE TABLE authority_request_nonces (
    authority_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    request_timestamp TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (authority_id, key_id, nonce)
);

CREATE INDEX idx_authority_request_nonces_expires
    ON authority_request_nonces (expires_at);
```

Optional audit table:

```sql
CREATE TABLE signed_request_audit (
    id BIGSERIAL PRIMARY KEY,
    authority_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    accepted BOOLEAN NOT NULL,
    rejection_reason TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
```

Audit can also be logs-only in v1.

### Proposed Minter Settings

```text
MINTER_AUTH_MODE=development|signed|mtls
MINTER_SIGNED_AUTH_ENABLED=true
MINTER_ADMIN_API_URL=http://admin-api:8000
MINTER_ADMIN_API_TIMEOUT_SECONDS=5
MINTER_CLIENT_KEY_CACHE_TTL_SECONDS=60
MINTER_CLIENT_KEY_NEGATIVE_CACHE_TTL_SECONDS=30
MINTER_SIGNED_AUTH_MAX_SKEW_SECONDS=300
MINTER_SIGNED_AUTH_NONCE_TTL_SECONDS=600
MINTER_SIGNED_AUTH_REQUIRED_SCOPES=true
```

Recommended behavior:

- `development`: accept `X-Authority-Id` header, local only.
- `signed`: require signed key headers for mutating endpoints.
- `mtls`: require mTLS identity, optionally still use signed keys for defense in depth.

For production, `MINTER_AUTH_MODE=signed` should be the default.

### Key Lookup Algorithm

For `(authority_id, key_id)`:

1. Check local in-memory cache.
2. Check local DB cache.
3. If missing or expired, call Admin API.
4. Store Admin response in local DB cache.
5. Verify `active=true`, `expires_at` not passed, and requested scope allowed.

If Admin API is unavailable:

- If a non-expired cached key exists, continue with cached key.
- If no valid cached key exists, reject with `503 Authentication key registry unavailable`.

Revocation is eventually consistent. With a 60s cache TTL, a revoked key may be accepted for up to 60 seconds by a Minter that already cached it.

## Scope Model

Initial scopes:

```text
ark:reserve
ark:update
ark:delete
worker:rescue
```

Endpoint mapping:

| Endpoint | Required scope |
| --- | --- |
| `POST /api/v1/arks` | `ark:reserve` |
| `POST /api/v1/arks/batch` | `ark:reserve` |
| `PUT /api/v1/arks/{ark}` | `ark:update` |
| `DELETE /api/v1/arks/{ark}` | `ark:delete` |
| `POST /api/v1/worker/errors/rescue` | `worker:rescue` |

If `scopes=[]`, choose one explicit semantic:

- Option A: no permissions.
- Option B: all standard authority permissions.

Recommended: Option A. Empty scopes means no mutating access.

## Verification Algorithm

For every protected request:

1. Read required signed-auth headers.
2. Reject if any required header is missing.
3. Read raw request body bytes.
4. Compute SHA-256 over raw body.
5. Compare with `X-DARK-Content-SHA256`.
6. Parse and validate timestamp.
7. Reject if timestamp is outside skew window.
8. Insert nonce into nonce table.
9. If nonce already exists, reject as replay.
10. Load key from local cache or Admin API.
11. Reject if key inactive, revoked, expired, or missing required scope.
12. Build canonical request string.
13. Verify signature.
14. Set authenticated identity:

```json
{
  "authority_id": "...",
  "auth_mode": "signed",
  "key_id": "SHA256:...",
  "scopes": ["ark:reserve"]
}
```

15. Existing endpoint checks continue:
    - request body `authority_id` must match authenticated authority;
    - authority must be authorized for requested NAAN;
    - ARK ownership rules still apply.

Important: insert nonce before signature verification only if bad signatures should also consume nonces. This prevents brute-force replay attempts with the same nonce. If this creates operational friction, insert after signature verification and rate-limit bad signatures. Recommended v1: insert after timestamp/body validation and before signature verification.

## Client Signing Example

Pseudo-code:

```python
import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone

method = "POST"
path = "/api/v1/arks"
body = json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")
content_sha256 = hashlib.sha256(body).hexdigest()
timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
nonce = str(uuid.uuid4())

canonical = "\n".join([
    "DARK1",
    method,
    path,
    authority_id,
    key_id,
    timestamp,
    nonce,
    content_sha256,
])

signature = sign_with_private_key(canonical.encode("utf-8"))

headers = {
    "Content-Type": "application/json",
    "X-DARK-Authority-Id": authority_id,
    "X-DARK-Key-Id": key_id,
    "X-DARK-Timestamp": timestamp,
    "X-DARK-Nonce": nonce,
    "X-DARK-Content-SHA256": content_sha256,
    "X-DARK-Signature-Alg": "ssh-ed25519",
    "X-DARK-Signature": base64.b64encode(signature).decode("ascii"),
}
```

The exact client helper can live in a small Python package or notebook utility.

## OpenSSH Signature Format

There are two possible implementation paths:

### Option 1: Raw Ed25519 Signatures

The client signs the canonical bytes directly with Ed25519. The server parses the SSH public key, extracts the Ed25519 public key, and verifies the raw signature.

Pros:

- Simple HTTP header format.
- Easy to implement in Python with `cryptography`.
- Good for programmatic clients.

Cons:

- It is not exactly `ssh-keygen -Y sign` compatible.

### Option 2: OpenSSH `ssh-keygen -Y sign`

The client uses OpenSSH signature files or base64-encoded OpenSSH signatures.

Pros:

- Familiar SSH tooling.
- Clear user workflow from the command line.

Cons:

- More complex server-side verification.
- Header payload is larger.
- Cross-language client implementations are more awkward.

Recommended v1: raw Ed25519 signatures using OpenSSH public key format for registration. We can add OpenSSH signature compatibility later if CLI ergonomics become more important.

## Security Considerations

### Transport Security

Signed requests prove client key possession, but TLS is still required. Without TLS, signed requests can still leak payload data and headers.

Recommended:

- HTTPS at the edge.
- Private network between proxy and Minter.
- If a reverse proxy injects identity headers, it must strip user-provided identity headers first.

### Header Confusion

When signed auth is enabled, ignore legacy identity headers:

```text
X-Authority-Id
X-Authority-UUID
```

Only use:

```text
X-DARK-Authority-Id
```

This avoids mixing signed and unsigned identity sources.

### Body Mutation

The signature covers the body hash. If any proxy rewrites the body, verification fails.

Clients must sign the exact bytes they send.

### Query String

The canonical request includes the path and query string. Clients must sign the actual request target.

### Clock Skew

Clients need reasonably correct clocks. A 5-minute skew window is practical. Larger windows increase replay risk.

### Key Revocation

Revocation is not instant while Minters cache keys. Use a short TTL and provide an operational command to clear key cache in emergencies.

### Rate Limiting

Bad signatures should be rate-limited by source IP, `authority_id`, and `key_id` where possible.

## Rollout Plan

### Phase 1: Data and Admin Registry

- Add Admin DB table for `authority_client_keys`.
- Add Admin endpoints to create/list/get/revoke keys.
- Add validation for SSH public key format and fingerprint generation.
- Add tests for duplicate keys, revoked keys, expired keys and scopes.

### Phase 2: Minter Verification

- Add Minter DB tables for key cache and nonce tracking.
- Add Admin API client for key lookup.
- Add signed-auth middleware/dependency.
- Protect mutating endpoints with signed auth when `MINTER_AUTH_MODE=signed`.
- Keep header mode only for `MINTER_AUTH_MODE=development`.

### Phase 3: Client Tooling

- Add a Python helper to sign requests.
- Update notebooks to use signed requests when configured.
- Document `ssh-keygen` key creation.

### Phase 4: Hardening

- Add rate limiting.
- Add audit logs.
- Add cache invalidation endpoint or command.
- Add optional shared nonce store for multi-Minter deployments.
- Add scope enforcement for rescue and delete operations.

## Test Plan

Admin API:

- Register valid `ssh-ed25519` key.
- Reject invalid key format.
- Compute stable `key_id` fingerprint.
- Reject duplicate `(authority_id, key_id)`.
- List keys by authority.
- Fetch active key by authority and key ID.
- Return inactive/revoked key state.
- Revoke key idempotently.
- Enforce key expiration.

Minter:

- Reject missing signed-auth headers in signed mode.
- Reject legacy `X-Authority-Id` in signed mode.
- Accept valid signed request.
- Reject body hash mismatch.
- Reject signature mismatch.
- Reject path/query mismatch.
- Reject method mismatch.
- Reject expired timestamp.
- Reject future timestamp outside skew.
- Reject nonce replay.
- Fetch key from Admin on cache miss.
- Use local cache when Admin is temporarily unavailable.
- Refresh expired cache entry from Admin.
- Reject revoked key after cache expiry.
- Enforce scopes per endpoint.
- Preserve existing `authority_id` body match behavior.
- Preserve NAAN authorization checks.

Integration:

- One Admin API, two Minter instances.
- Client key registered once in Admin.
- Both Minters can fetch and cache the key.
- Request signed once cannot be replayed against same Minter.
- Revocation propagates after cache TTL.

## Open Decisions

1. Should v1 use local DB nonce tracking only, or introduce Redis/shared nonce storage immediately?
2. Should empty scopes mean no access or full standard authority access?
3. Should Admin API have its own persistent DB, or should key registry initially be file-backed/config-backed?
4. Should key revocation be visible to Minters as `active=false`, or should Admin return `404` for revoked keys?
5. Should we support only raw Ed25519 signatures in v1, or also OpenSSH `ssh-keygen -Y sign`?

Recommended defaults:

- local DB nonce tracking in v1;
- empty scopes mean no access;
- Admin DB table;
- revoked keys returned with `active=false`;
- raw Ed25519 signatures with SSH public key registration.
