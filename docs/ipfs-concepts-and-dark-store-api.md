# IPFS concepts and dARK Store API

This report explains how IPFS works and why `dark-store-api` is shaped as a thin storage facade over IPFS and IPFS Cluster. It is intentionally technical: the goal is to make the storage behavior predictable for developers and operators working on dARK.

For deployment details, Docker networks and local startup order, see [IPFS, IPFS Cluster and Store API in a real environment](ipfs-cluster-store-api-real-environment.md).

## Executive summary

IPFS is not a database, an object store with accounts, or a blockchain. It is a content-addressed data network. Data is addressed by what it is, not by where it is hosted. The address is a CID, derived from the content and the import format.

That distinction is central for dARK:

- the blockchain should store stable references, not large metadata payloads;
- Store API stores and retrieves raw bytes by CID;
- IPFS provides content addressing and transport;
- IPFS Cluster provides operational durability by coordinating pins and replicas;
- dARK services should treat IPFS/Cluster failures as infrastructure problems, not as semantic ARK errors.

The critical operational rule is simple:

```text
CID != availability
```

Having a CID proves how to identify content. It does not prove that the content is currently retrievable from a healthy node. Availability requires at least one provider, and long-term durability requires pinning or another persistence mechanism.

## Content addressing

Traditional HTTP URLs are location-addressed:

```text
https://example.org/path/to/file.pdf
```

The URL says where to ask for the object. If the host moves, deletes the file, changes the file, or serves different bytes under the same URL, the URL alone does not protect the identity of the content.

IPFS uses content addressing:

```text
bafkre...
```

The CID identifies the content itself. In simplified terms, IPFS imports bytes, splits them into blocks when needed, hashes those blocks, and builds a content-addressed graph. The resulting CID points to the root of that graph.

Important consequences:

- The same content imported with the same IPFS import settings should produce the same CID.
- If the bytes change, the CID changes.
- If import settings change, for example CID version, codec, directory wrapping or chunking, the root CID may change even when the source file looks like the same file to an operator.
- A CID is immutable in practice: it identifies a specific content graph, not a mutable document name.
- Deduplication is natural because identical blocks share identifiers.

For dARK, this is useful because the CID can be safely embedded in Level 1 metadata and published on-chain. The blockchain records the exact content reference; IPFS and Store API are responsible for serving the bytes.

## CID, blocks and DAGs

IPFS stores content as blocks. A block has bytes and an identifier. Larger files are represented as graphs of blocks, often using UnixFS. A root CID can point to:

- a raw block;
- a UnixFS file;
- a directory;
- a larger Merkle DAG with links to other blocks.

When an application asks for a CID, IPFS starts from the root and resolves the graph. If all required blocks are available locally or from peers, the content can be reconstructed.

This means the root CID is only the entry point. For a large object, successful retrieval depends on availability of all linked blocks.

In Store API, the public contract intentionally stays simpler:

```text
POST /v1/store -> returns cid
GET /v1/retrieve/{cid} -> returns raw bytes
GET /v1/status/{cid} -> returns pin/replica status
```

The caller should not need to know whether the CID maps to one block or a graph.

## Adding content

Adding content to IPFS means importing bytes into an IPFS node:

```text
client -> IPFS /api/v0/add -> local IPFS blockstore -> CID
```

Depending on the path used, the add operation may also pin the data. In dARK, the preferred path is through IPFS Cluster Proxy:

```text
Store API -> Cluster Proxy /api/v0/add?cid-version=1&pin=true
          -> local IPFS
          -> Cluster pin orchestration
          -> CID
```

This matters because a plain IPFS add only proves that one node accepted the bytes and produced a CID. It does not by itself prove that the content was replicated according to the cluster policy.

The Store API currently uses `cluster_proxy` as the default mode because it makes the write path atomic from the application point of view: add and pin are one operation at the storage boundary.

## Retrieving content

Retrieving content is done by CID:

```text
Store API -> local IPFS /api/v0/cat?arg={cid}
```

The local IPFS node may already have the blocks. If it does not, it can attempt to find providers and fetch the blocks through the IPFS network.

There are several important cases:

- The content is local and pinned: retrieval should be fast.
- The content is not local but another peer has it: retrieval may work but can be slower.
- The content is in the cluster pinset but not yet fully available locally: retrieval may be delayed while blocks are fetched.
- No reachable provider has the blocks: the CID is valid, but retrieval fails.

This is why `GET /v1/retrieve/{cid}` can fail even when the CID is syntactically valid and even when an ARK exists on-chain.

## Providers, routing and transfer

IPFS needs to answer two separate questions:

```text
Who may have this CID?
How do I fetch the blocks from them?
```

Provider discovery can use routing systems such as the DHT in public IPFS networks, or controlled peer connectivity in private infrastructure. Transfer is then handled by IPFS exchange mechanisms between peers.

In a private dARK deployment, operators usually want a more controlled topology:

- known IPFS nodes;
- known Cluster peers;
- private networks between storage nodes;
- Store API talking only to its local IPFS/Cluster entry point.

This avoids making application services depend directly on every remote IPFS peer.

## Pinning and garbage collection

IPFS nodes have local storage. They may cache blocks that were imported or fetched. Unpinned cached blocks can be removed by garbage collection.

Pinning is the mechanism that tells an IPFS node:

```text
Keep this CID and the blocks it needs.
```

The operational implication is:

```text
added but not pinned -> may disappear
pinned on one node -> durable while that node/storage is healthy
pinned on multiple nodes -> more resilient
```

Pinning is local to a node unless a higher-level system coordinates it. If three IPFS nodes exist but only one pins a CID, the other two are not automatically durable replicas.

This is the gap IPFS Cluster fills.

## IPFS Cluster

IPFS Cluster is a coordination layer for multiple IPFS daemons. It does not replace IPFS. Each Cluster peer is paired with an IPFS node and tells that node what to pin.

Conceptually:

```text
Cluster pinset
  -> allocation decision
  -> peer A pins CID on ipfsA
  -> peer B pins CID on ipfsB
  -> peer C does not pin CID
```

The cluster pinset records what should be pinned and with which options. Allocation decides which peers should hold replicas. The pin tracker monitors whether the IPFS nodes actually match the desired pinset.

Replication factors define how many peers should be allocated:

```text
replication_factor_min = minimum acceptable number of replicas
replication_factor_max = maximum desired number of replicas
```

In local dARK development we use a small cluster and typically configure:

```text
CLUSTER_REPLICATION_MIN=2
CLUSTER_REPLICATION_MAX=2
```

That means each stored object should be allocated to exactly two cluster peers. In production, this should be chosen according to node count, failure domains, storage budget and recovery expectations.

## Cluster REST API vs Cluster Proxy

IPFS Cluster exposes different API surfaces. The two most relevant for dARK are:

```text
Cluster REST API
Cluster Proxy API
```

The REST API is cluster-management oriented. Examples:

```text
GET  /id
GET  /peers
GET  /pins/{cid}
POST /pins/{cid}
```

The Cluster Proxy API exposes an IPFS-compatible API through the Cluster peer. For dARK, the key operation is:

```text
POST /api/v0/add?cid-version=1&pin=true
```

When Store API writes through the proxy, Cluster can add the content and register the pin in the cluster workflow. This is preferable to:

```text
IPFS add -> Cluster pin
```

because the two-step path creates an intermediate state where the content exists in local IPFS but may not yet be managed by Cluster.

The legacy `ipfs_then_cluster` mode is useful for debugging and compatibility, but `cluster_proxy` is the intended default.

## Store API in dARK

`dark-store-api` is deliberately narrow. It is not an IPFS orchestration service, not an ARK registry, and not a metadata interpreter.

Its responsibilities are:

- accept bytes;
- return a CID;
- retrieve bytes by CID;
- report storage readiness;
- hide IPFS/Cluster details from minter and resolver.

The minter/resolver should not call IPFS or IPFS Cluster directly because that would spread storage topology knowledge across services. Store API provides one application boundary:

```text
Minter/Resolver -> Store API -> IPFS/Cluster
```

That boundary is important for:

- changing IPFS topology without changing minter/resolver code;
- classifying storage failures as infrastructure failures;
- keeping dARK metadata semantics in `dark-core-lib`, minter and resolver;
- making health/readiness decisions in one place.

## L1, L2, CID and blockchain

dARK separates metadata concerns:

- Level 2 stores the original/raw metadata bytes.
- Level 1 stores the dARK metadata envelope, including references to Level 2.
- The blockchain stores ARK state and the Level 1 CID.

Simplified write path:

```text
source metadata
  -> Store API stores raw L2
  -> Store API returns original_cid
  -> minter builds L1 JSON with original_cid, schema and media_type
  -> Store API stores L1 JSON
  -> Store API returns level1_cid
  -> chain worker publishes level1_cid on-chain
```

Simplified resolve path:

```text
resolver reads ARK on-chain
  -> gets level1_cid
  -> Store API retrieves L1 JSON
  -> L1 points to original_cid
  -> Store API retrieves original bytes
  -> resolver returns content with media type/schema from L1
```

The blockchain does not need to store the raw metadata. It stores the stable reference to the content-addressed L1 object.

## Recommended real topology

The recommended production shape is local-entrypoint based:

```text
Application network
  Minter / Resolver
        |
        v
  Store API
        |
        v
  Local Cluster Proxy / REST
        |
        v
  Local IPFS daemon

Storage network
  Local Cluster Peer <-> Remote Cluster Peer 1 <-> Remote Cluster Peer 2
  Local IPFS Node    <-> Remote IPFS Node 1    <-> Remote IPFS Node 2
```

From the Store API point of view, the remote nodes are substrate. They are not direct application dependencies.

That gives a clean separation:

- Store API needs one local IPFS API for reads.
- Store API needs one local Cluster REST API for status and readiness.
- Store API needs one local Cluster Proxy API for writes.
- Cluster owns the remote replication topology.

This is why the current local Docker setup exposes `ipfs0` and `cluster0` to Store API, while `ipfs1/cluster1` and `ipfs2/cluster2` simulate remote nodes.

## Same cluster vs independent clusters

When considering multiple configured IPFS/Cluster endpoints for Store API, it is important to distinguish two designs.

### Multiple peers from the same Cluster

This means each configured endpoint is another entry point into the same logical Cluster pinset.

```text
Store API -> cluster0/ipfs0
Store API -> cluster1/ipfs1
Store API -> cluster2/ipfs2

All cluster peers share the same cluster state.
```

This is suitable for primary + failover. If `cluster0` is unavailable, Store API can use `cluster1` without changing the meaning of the write. The pinset remains the same logical storage system.

### Independent clusters

This means each configured endpoint belongs to a different Cluster with a different pinset.

```text
Store API -> Cluster A
Store API -> Cluster B
Store API -> Cluster C
```

This is not simple failover. A write to Cluster A is not automatically known by Cluster B. If Store API falls back from A to B, then status and retrieve semantics become multi-backend semantics. Operators must decide whether to multi-write, replicate across clusters, or track which cluster owns each CID.

For dARK, the safer default is:

```text
multiple endpoints = peers of the same Cluster
```

Independent clusters should be treated as a separate architecture decision, not as a small configuration change.

## Configurable nodes idea

A future Store API configuration could declare storage nodes explicitly:

```json
[
  {
    "id": "node-0",
    "ipfs_api_url": "http://ipfs0:5001",
    "cluster_api_url": "http://cluster0:9094",
    "cluster_proxy_api_url": "http://cluster0:9095"
  },
  {
    "id": "node-1",
    "ipfs_api_url": "http://ipfs1:5001",
    "cluster_api_url": "http://cluster1:9094",
    "cluster_proxy_api_url": "http://cluster1:9095"
  }
]
```

The recommended behavior is ordered primary + failover:

- use the first healthy node as primary;
- fail over on infrastructure errors such as timeout, connection refused, DNS failure or 5xx;
- do not fail over on semantic errors such as malformed responses, invalid CIDs or authorization/configuration errors;
- expose active node and node health in `/health`;
- assume all configured nodes are peers of the same IPFS Cluster.

This avoids leaking the full storage topology into minter/resolver while still allowing Store API to survive a local IPFS/Cluster entrypoint failure.

## Failure modes

### Local IPFS node is down

Symptoms:

- `POST /api/v0/id` fails;
- `cat` fails;
- Cluster Proxy may also fail if it depends on the local IPFS daemon.

Store API should report storage readiness unhealthy. Minter metadata worker should pause instead of recording permanent ARK failures.

### Cluster peer is down

Symptoms:

- `GET /id` on Cluster REST fails;
- `GET /peers` fails;
- pin status cannot be checked.

Store API should report readiness unhealthy unless another configured node from the same cluster is available.

### Cluster Proxy is down

Symptoms:

- direct Cluster REST may be healthy;
- writes through `/api/v0/add` fail.

If `IPFS_ADD_MODE=cluster_proxy`, Store API should consider this not ready for writes. Liveness can still be healthy, but readiness should be unhealthy.

### Not enough Cluster peers

Symptoms:

- `/peers` returns fewer visible peers than `IPFS_CLUSTER_MIN_PEERS`;
- writes may fail with allocation errors;
- replication target cannot be satisfied.

Store API should report readiness unhealthy. This is infrastructure pressure, not an ARK-level semantic failure.

### CID exists but content is not local

Symptoms:

- `cat` is slow;
- `cat` may fail if no reachable provider can supply the blocks;
- pin status may still show the CID in the cluster pinset.

This can happen during replication lag, after node restarts, or when provider discovery/connectivity is degraded.

### Pinset is correct but transfer is slow

Symptoms:

- Cluster reports expected pin allocation;
- retrieval takes longer than normal;
- local IPFS must fetch blocks from a remote peer.

This is often a network or IPFS transfer issue, not a metadata correctness issue.

### Store succeeds but downstream chain publish fails

IPFS and blockchain are separate stages. A successful Store API write means the content has a CID and should be managed by storage. It does not mean the ARK was published on-chain. The minter worker pipeline must keep these stages separate.

### Chain publish succeeds but Store retrieval later fails

The blockchain can correctly reference a Level 1 CID while storage is temporarily unavailable. Resolver behavior should distinguish:

- ARK not found or not authorized;
- CID referenced but storage unavailable;
- CID referenced but content missing.

Only the first category is semantic to the ARK. The other two are storage infrastructure failures.

## Operational rules for dARK

- Treat Store API as the only application-facing storage boundary.
- Use Cluster Proxy for writes by default.
- Do not call `/pins/{cid}` after a successful proxy add.
- Keep `replication_min` aligned with expected durability and actual node count.
- Use `/health/live` for container liveness.
- Use `/health` for worker readiness.
- Cache readiness checks to avoid hammering IPFS/Cluster.
- Pause metadata workers when storage readiness is unhealthy.
- Do not mark ARKs permanently failed because IPFS/Cluster is temporarily unavailable.
- Prefer multiple endpoints only when they are peers of the same Cluster.

## Glossary

| Term | Meaning in this architecture |
| ---- | ---------------------------- |
| CID | Content Identifier. Stable address derived from content and import format. |
| Block | Unit of content stored by IPFS. Larger objects are graphs of blocks. |
| DAG | Directed acyclic graph of content-addressed blocks. |
| Provider | Peer that can supply blocks for a CID. |
| Pin | Instruction for an IPFS node to keep content and protect it from garbage collection. |
| Pinset | Set of CIDs a node or cluster is expected to pin. |
| Cluster peer | IPFS Cluster process that coordinates pins for a paired IPFS daemon. |
| Cluster Proxy | IPFS-compatible API exposed by Cluster, used by dARK for add+pin writes. |
| Replication factor | Number of Cluster peers expected to hold a pin. |
| Store API | dARK storage facade over IPFS/Cluster. |

## References

- [IPFS Docs: Lifecycle of data in IPFS](https://docs.ipfs.tech/concepts/lifecycle/)
- [IPFS Docs: Basic CLI operations with Kubo](https://docs.ipfs.tech/how-to/kubo-basic-cli/)
- [IPFS Docs: Pin files](https://docs.ipfs.tech/how-to/pin-files/)
- [IPFS Docs: IPFS Cluster](https://docs.ipfs.tech/install/server-infrastructure/)
- [IPFS Cluster Docs: Architecture overview](https://ipfscluster.io/documentation/deployment/architecture/)
- [IPFS Cluster Docs: Adding and pinning](https://ipfscluster.io/documentation/guides/pinning/)
- [IPFS Cluster Docs: Configuration](https://ipfscluster.io/documentation/reference/configuration/)
