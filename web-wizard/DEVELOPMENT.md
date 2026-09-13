# web-wizard — development guide

This document is for whoever continues this work. It explains what the workbench
is, the mental model it is built on, every module, the contracts between the
pieces, the rules of the domain that shape what is even possible, how to test
and extend it, and what is deliberately missing.

It assumes you have read `web-wizard/README.md` (what it does, how to run it).
This one is about *why* it is built the way it is and how to change it safely.

**Status:** phases 0 (view), 1 (edit + live validation), 2 (palette and web edge),
3 (save a copy) and 5 (network fabric and declared routes) are implemented.
Phase 4 (a full availability overlay and a resolved-diff review at save time) is
not. Five backend modules and three front-end files, 34 typed operations, a
pytest suite and an optional headless canvas layer — no counts are quoted here
on purpose (§10).

---

## 1. What web-wizard is

A local, single-user web workbench that:

1. loads a dARK **compact operator inventory**;
2. draws the topology it resolves to, including availability and failure domains;
3. lets you edit it through **typed operations**, each validated by the deployer's
   own resolver before it is committed;
4. saves the result to a **new file**, never overwriting anything.

It is not a deployment tool. It never renders, applies, pushes, verifies or
touches a host.

---

## 2. The constraints that shape every decision

These are not preferences; they are the reason several obvious designs were
rejected. Read them before proposing a change.

| Constraint | Why | How it is enforced |
|---|---|---|
| Everything lives under `web-wizard/` | The workbench must not entangle the deployer | All application code, deps and tests are inside this directory |
| The deployer is consumed **read-only** | Editing deployer modules to fit a UI couples the two forever | `webwizard/loader.py` is the only module that imports `deployment_v3`; a test greps for import statements elsewhere |
| `deployment_v3.runner` is never imported | "Cannot deploy" should hold by construction, not by intention | A test asserts `deployment_v3.runner` is absent from `sys.modules` after resolving an inventory |
| Edits live in memory; the loaded file is never modified | The operator must never lose their inventory to a preview tool | `DraftSession` only writes in `save()`, and `save()` cannot target the loaded path |
| Saving can only create a new file | User rule: *"always save a new file, never overwrite"* | `os.open(..., O_CREAT \| O_EXCL)` — there is no overwrite code path at all; tests cover the refusals |
| The workbench implements its **own** session/operations/undo | User rule: do not reuse the deployer's earlier wizard or editor | Nothing imports `deployment_v3.inventory_editor` |
| Must run on **Python 3.9** | The user's system `python3` is 3.9 | FastAPI evaluates route annotations at runtime, so signatures use `typing.Optional[...]`, never `X \| None`; the suite is run under 3.9 and 3.10 |
| No CDN, no bundler, no Node at runtime | It is an offline local tool | Plain `<script>`/`<link>` from `webwizard/static`; Node and `jsdom` are used only by the optional frontend test |
| The workbench never reads secret material | It shows topologies, not credentials | No code path reads a secret file; secrets appear only as ids in the resolved document |

---

## 3. The mental model: two documents, and you edit the small one

This is the single most important idea in the project. Get it wrong and every
subsequent decision is wrong.

### 3.1 The compact operator inventory — *what you edit*

`examples/operator-inventory/*.json`, `"format": "dark-operator-inventory"`,
`"format_version": 2`. It is small, hand-written and about **decisions**:

```jsonc
{
  "deployment": {"id": "dark-operator-production-six"},
  "networks":   {"lan": {"kind": "lan", "cidr": "192.0.2.0/24"}, "vpn": {...}},
  "machines":   {"apps": {"execution": "ssh", "management_address": "...", "addresses": {...}}},
  "placement":  {"apps": "apps", "blockchain-a": "blockchain-a", "explorer": "blockchain-a"},
  "routing":    {"blockchain_p2p": "vpn", "storage_api": "vpn", "application_api": "lan"},
  "blockchain": {"validator_groups": {"blockchain-a": {"validator_count": 2}},
                 "observer_groups": {},
                 "rpc": {"primary": "rpc01", "nodes": {"rpc01": {"group": "apps"}}}},
  "storage":    {"peers": {"storage-a": {"group": "storage-1"}}, "replication": {...}},
  "proxies":    {"resolver-public": {"group": "resolver", "listener": {...}, "sites": [...]}},
  "overrides":  {"explorer": {"group": "explorer"}}
}
```

### 3.2 The resolved v3 document — *what the deployer consumes*

The expansion of the above into a complete execution contract: every service
instance, every connection with its network, protocol, host and port, every
exposure, the resolved `groups`, `secrets`, `components`, `settings`,
`infrastructure`. It is large and entirely derived.

`deployment_v3.inventory_resolver.resolve_inventory(raw, source_path=path)` does
that expansion **purely** — no filesystem access beyond resolving relative secret
sources against `source_path.parent`. That purity is what makes live validation
possible without writing anything.

### 3.3 The consequences you cannot escape

Because you edit the compact document, not the resolved one:

* **Placement is per group, not per service.** `placement` maps a group to a
  machine, and every service the group hosts goes with it. So the central gesture
  is *move a group*, not *move a service*. `explorer` and `resolver` are the
  exception: `overrides.{explorer,resolver}.group` lets them live away from
  `apps`, which is why they look like singletons.
* **Cardinality is per group.** You do not add a validator; you raise a group's
  `validator_count`, and the resolver names the instances `validator01…NN` in
  declaration order across all validator groups. Observers work the same way.
* **Connections are derived.** You cannot draw an edge between two services. The
  catalogue declares each service's connections; the resolver fills in the
  network and protocol from `routing`, and re-points RPC consumers through
  `blockchain.rpc.bindings`.
* **Exposure is derived.** There is no "port" to set for a service. The resolver
  derives a private exposure when a remote consumer needs one, taking the port
  from its own `_PORTS` table. What you *can* influence: `routing` (which network
  a link class uses), proxy `listener`s (the public edge), `networking.services.*`
  announce overrides (NAT), and `blockchain.rpc.bindings`.
* **`contracts-deploy` is not a network link.** Consumers depend on it for
  ordering; it never exposes a port. `build_graph` models those as
  `transport: "dependency"` and never draws them.

---

## 4. Architecture at a glance

```
                 browser  (webwizard/static/*)
                     │  fetch, JSON, same-origin cookie
                     ▼
          FastAPI app  (webwizard/server.py)          ← 127.0.0.1 + per-run token
                     │
        ┌────────────┼─────────────────┐
        ▼            ▼                 ▼
   DraftSession   operations.py    load_topology()
   (session.py)   (typed ops)      (stateless read)
        │            │                 │
        └────────────┴─────────────────┘
                     │
                     ▼
            loader.py  ← the ONLY module that imports deployment_v3
                     │
        resolve_inventory · build_plan · availability.analyze
```

An **edit** travels: browser → `POST …/op` → `operations.perform` →
`DraftSession.apply` → `resolve_inventory` (candidate) → commit or reject →
`build_graph` → back to the browser as `{ok, error, draft, graph}`.

A **save** travels: browser → `POST …/save` → `DraftSession.save` → re-resolve →
`os.open(O_EXCL)` → the path written, back to the browser.

---

## 5. Backend, module by module

### 5.1 `loader.py` — the read-only boundary

The only place `deployment_v3` is imported. Everything is funnelled through a
single lazily-built `Core` dataclass:

```python
@dataclass(frozen=True)
class Core:
    build_plan: Callable[[Path], Any]
    analyze: Callable[[Any], Any]
    resolve_inventory: Callable[..., Any]
    validate_inventory: Callable[[dict], Any]   # exposed, currently unused
```

* `REPO_ROOT` is derived from `__file__` (`web-wizard/…` → repo root) and
  inserted into `sys.path` on first use, so the workbench works no matter where
  it is launched from.
* `read_inventory(path)` → `(concrete_path, raw_dict)`, with a relative path
  resolved against the CWD and then the repo root.
* `list_inventories()` lists `examples/operator-inventory/*.json`.
* `load_topology(path)` is the **stateless** read used by `GET /api/graph`: plan
  → availability → graph, plus the resolver's warnings. It is what the browser
  falls back to when a document is not editable (a full v3 inventory).

**If you add anything here, keep the invariants:** no import outside this file,
nothing that writes, nothing that touches `runner`.

### 5.2 `graph.py` — the pure projection

Turns `(plan, availability, source, warnings)` into the JSON the canvas draws.
No I/O, no side effects. `build_graph(..., inventory_path=...)`.

It is a *view model*, deliberately not a copy of the plan:

* `families` — `blockchain | storage | applications | web | other`, from
  `FAMILY_BY_TYPE`. Colour, legend and the "by function" layout all key off this.
* `node_kind` — `scalable` (the five templates a catalogue can multiply:
  `besu-validator`, `besu-rpc`, `besu-observer`, `ipfs-kubo`, `ipfs-cluster`) or
  `singleton`. This is what tells the palette what can be added.
* `edges` — one per declared connection, with `transport`:
  * `docker` — both ends on one machine; **not drawn**, but counted;
  * `private` — crosses a host; drawn, with `host`/`port` taken from the plan's
    derived endpoints;
  * `dependency` — into `contracts-deploy`; never a network link.
  `routed` is `False` for `dependency` edges; `crosses_host` is only ever true
  for `private` ones.
* `if_lost` on machines and groups — taken from `availability.scenarios` (the
  deployer's own failure analysis): `{consensus, storage, observers,
  applications_rpc}`. This is what drives the risk markers and the objective
  status. It is computed by the deployer, never re-derived here.
* `operator` — an `OperatorView` carrying the *authored* compact facts
  (`validator_groups`, `storage_peers`, `proxies`, `advertise`, `bindings`,
  `objectives`, …) verbatim. The panels need what the operator wrote, not only
  what it resolved to.

`FAMILY_ORDER` fixes the family order everywhere (bands, legend, card ordering).

### 5.3 `session.py` — the draft

`DraftSession` is the heart of the editing model. Nothing else writes to the
document.

```python
@dataclass
class DraftSession:
    path: Path                  # the inventory being edited (never written)
    original: dict              # as loaded
    raw: dict                   # the current draft
    resolution: Any             # last successful ResolutionResult
    stage: str
    last_error: str | None
    undo_stack: list[dict]
    saved_path: Path | None
```

* `open(path)` reads, requires `dark-operator-inventory`, and resolves once to
  prove the document is valid before the UI ever sees it.
* `apply(change, *, stage)` — **the transactional core**:

  ```python
  candidate = deepcopy(self.raw)
  try:
      change(candidate)
      resolution = self._resolve(candidate)      # resolve_inventory
  except Exception as exc:
      self.last_error = _pretty(exc)
      return False                               # draft untouched
  self.undo_stack.append(deepcopy(self.raw))
  self.raw = candidate
  self.resolution = resolution
  return True
  ```

  A rejected change is *not* applied. That is why the canvas never shows an
  impossible deployment, and why the UI has no "invalid draft" state to design.
* `undo()` pops a snapshot and re-resolves; `reset()` returns to `original`.
* `plan()` — `build_plan` is path-based, so the **resolved** document is written
  to a private `TemporaryDirectory` and planned from there. Writing the resolved
  (not the compact) document keeps relative secret sources pointing where the
  real resolution put them, so the preview matches what `deploy.py` would see.
  The inventory file is never involved.
* `view()` — plan → `availability.analyze` → `build_graph`.
* `suggested_destination()` — `<stem>.wizard-<UTC timestamp>.json` next to the
  original, computed once and cached so the form does not jump between renders.
* `save(destination=None)`:

  ```python
  self.resolution = self._resolve(self.raw)          # never write an unreadable file
  if self.changed: data = (json.dumps(self.raw, indent=2) + "\n").encode()
  else:            data = self.original_bytes        # a faithful copy
  descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
  ```

  Refusals: the loaded path, a directory, a missing parent directory, and any
  existing file (`FileExistsError`). Relative destinations resolve next to the
  inventory being edited. No directory is ever created.

  **Never sort the keys here.** Instance names are positional — validators and
  observers are numbered in the declaration order of
  `blockchain.{validator,observer}_groups` — so `sort_keys=True` silently moves
  an instance to another group, and an RPC binding that names it then points at
  a different machine. The document that results does not resolve at all. This
  is not hypothetical: it was a live bug, caught by the round-trip test in
  `test_session.py`. Key order is meaning in this format; preserve it.
* `state()` — the single payload shape the browser consumes:
  `{ok, error, draft: {path, changed, sections, can_undo, stage, written,
  saved_path, suggested_path}, graph}`. `changed`/`sections` compare the draft
  against the **loaded** file (not against a saved copy), in `SECTION_ORDER`.

### 5.4 `operations.py` — the typed operations

34 named operations in a registry, each a plain function
`(session: DraftSession, params: dict) -> None` that either commits a change or
raises `OperationRejected`.

```python
_HANDLERS = {"move_group": _move_group, "add_machine": _add_machine, ...}

def perform(session, kind, params=None):
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise OperationRejected(f"unknown operation '{kind}'")
    handler(session, params if isinstance(params, dict) else {})
```

Parameter helpers (`_identifier`, `_text`, `_choice`, `_count`, `_optional_port`,
`_optional_address`) do the cheap, explicit validation so the user gets a clear
message; anything structural is left to the resolver, which is the authority.
`_commit(session, stage, change)` calls `session.apply` and re-raises the
resolver's message as `OperationRejected`.

| Operation | Parameters |
|---|---|
| `move_group` | `group`, `machine` |
| `add_machine` | `id`, `execution` (`local`/`ssh`/`auto`), `management_address`\*, `addresses`\* |
| `remove_machine` | `id` |
| `set_machine_address` | `id`, `network`, `address` |
| `add_network` / `remove_network` | `id`, `kind`, `cidr` / `id` |
| `add_route` / `remove_route` | `from`, `to`, `via`? / `from`, `to` |
| `set_validator_count` | `group`, `count` |
| `set_observer_count` | `group`, `count` |
| `add_validator_group` / `add_observer_group` | `group`, `machine`, `count` |
| `remove_validator_group` / `remove_observer_group` | `group` |
| `add_rpc_node` | `id`, `group` |
| `remove_rpc_node` | `id` (refuses the last one) |
| `set_primary_rpc` | `id` |
| `set_binding` | `consumer`, `provider` (empty string clears) |
| `set_routing` | `role` (`blockchain_p2p`/`storage_p2p`/`storage_api`/`application_api`), `network` |
| `add_storage_peer` / `remove_storage_peer` | `id`, `group` / `id` |
| `set_replication` | `publish_after_replicas`, `target_replicas` |
| `set_override` | `service` (`explorer`/`resolver`), `group` |
| `set_profile` | `profile` (`local`/`lab`/`production`) |
| `set_deployment_label` | `label` |
| `set_acknowledgements` | `objectives` (list; empty clears) |
| `set_advertise` | `service`, `advertise_address`?, `advertise_port`?, `p2p_advertise_port`? |
| `add_proxy` | `id`, `group`, `bind`, `port`, `network`\* (private only), `tls_mode`, `host`, `public_origin`, `route_id`, `path`, `service`, `upstream_path` |
| `remove_proxy` | `id` (refuses the last one) |
| `set_proxy_listener` | `id`, `bind`, `port`, `network`\* |
| `set_proxy_tls` | `id`, `mode`, `certificate_secret`\*, `key_secret`\* (direct only) |
| `set_proxy_site` | `id`, `site_index`, `host`, `public_origin` |
| `add_proxy_route` / `remove_proxy_route` | `id`, `site_index`, `route_id`, `path`, `service`, `upstream_path` / `id`, `site_index`, `route_id` |

\* required only in the stated case.

Several helpers exist because the resolver's rules are better stated up front
than discovered by an opaque error: `_group_is_apps`, `_service_ids` (every
service id a compact document can produce), `_proxy_site`. They are *convenience*,
never authority — the resolver still decides.

### 5.5 `server.py` — the local API

FastAPI, bound to `127.0.0.1` by `uvicorn.run(..., host="127.0.0.1")`. A
per-run `secrets.token_urlsafe(24)` is accepted from a header
(`X-Wizzard-Token`), a cookie (`wizard_token`, HTTP-only, `SameSite=Strict`) or
a query parameter, compared with `secrets.compare_digest`. `GET /?token=…` sets
the cookie and serves the page; every `/api/*` route depends on `authorize`.

Sessions live in a plain dict closed over by `create_app`
(`sessions: dict[str, DraftSession]`), so drafts are per-process and die with it.
That is intentional: it is a single-user local tool.

A middleware sets `Cache-Control: no-store` on `/static/*` so a stale
`canvas.js` never survives a reload during development.

`POST` handlers return `200` with `{"ok": false, "error": "…"}` for **domain
refusals** (a rejected operation, a refused save) and 4xx only for transport or
programming errors. The browser therefore treats "your change was refused" as
normal data, not an exception.

---

## 6. The transactional contract

This is the property everything else is designed around:

> **The draft is always a valid operator inventory.** A change that would break
> that is never applied; the user is told why and keeps the last good state.

Consequences worth understanding before changing anything:

* The canvas never has an "invalid" rendering, and there is no rollback UI to
  design — rejection *is* the rollback.
* `undo` is a stack of whole-document snapshots. At the scale of a compact
  inventory (a few kB) that is simpler and safer than inverse operations.
* Because every commit re-resolves, the availability report, the failure domains
  and the graph are recomputed on every edit — the "live validation" is not a
  separate feature, it is a side effect of committing.
* Validation happens **before** the write and again **inside** `save()`, so a
  file on disk is always loadable by the deployer.
* A test asserts a rejected operation leaves `session.raw` byte-identical
  (`json.dumps(sort_keys=True)`), and that many edits never change the hash of
  the file on disk.

---

## 7. Domain rules that shape what is possible

These were all discovered by *trying* to operate and being refused. They are the
deployer's rules, not the workbench's, and they are why several operations look
more constrained than the data model suggests. When you add an operation, expect
to find more of these.

| Rule (as the resolver phrases it) | Why it exists | What triggers it |
|---|---|---|
| `placement.<group> must name a declared machine` | Placement is the only source of machine assignment | `remove_machine` on a machine that still hosts a group |
| `routing selects <net> but it is not available on <machine>` | A link class is routed over a network both ends must have | Moving a storage group to a fresh **local** machine: a local machine only gets one address, on the *first* network, so storage P2P has no VPN |
| `routing selects <net> but it is neither shared nor routed from A to B` | Same, across hosts | Moving a group to a machine that lacks the routed network |
| `storage replication must satisfy 1 <= publish <= target <= storage nodes` | Durability cannot exceed what backs it | `remove_storage_peer` while `target_replicas` is higher |
| `services.<id>.exposure.port must match infrastructure policy (80)` | A **private** listener is published on the site network, so the web policy pins its port | `set_proxy_listener` on a private proxy with any other port. Public and loopback listeners are free |
| `groups.<g>.validators may contain only besu-validator services` | The resolver derives a group's `kind` **before** proxies are attached | `add_proxy` into a validators/observers/storage group — hence `_group_is_apps` |
| `machine <id> may run only one edge-proxy` | One web edge per host | `add_proxy` onto a machine that already has one |
| `a proxy site needs at least one route` | A site with no routes has nothing to serve | `remove_proxy_route` on the last route |
| `services.<id>.connections.<name> must reference <type>` | Connections come from the catalogue contract | A proxy route targeting anything other than `dashboard`, `explorer`, `minter-api`, `resolver-api` |
| `minter-api, minter-postgres and all minter workers must share one machine` | Metadata storage is local to the minter | Any attempt to split the minter stack across machines |
| `production availability objective(s) not met and not acknowledged` | Production demands explicit availability decisions | Moving validators so a declared objective fails, without listing it in `availability.acknowledgements` |
| `availability.acknowledgements contains satisfied or unselected objective(s)` | You may only acknowledge what is genuinely unmet | Acknowledging an objective that currently holds — the UI disables those checkboxes |
| `blockchain.primary_rpc must name a besu-rpc node` | The application RPC must be a real RPC node | `set_primary_rpc` to an observer |
| *(silent)* a binding starts pointing at another machine | Validator and observer instance names are **positional**: `observer01` is whichever group is declared first | Any re-emission of the document that reorders `blockchain.{validator,observer}_groups` — including `json.dumps(..., sort_keys=True)`. See §5.3 |
| `requires <network> for cross-host <policy> P2P` | Chain and storage peers must genuinely hold the address; they are dialled there | Moving a validator or storage group to a machine without the P2P network. **A route does not help** |
| `routing selects <net> but it is not available on <machine>` | A *provider* must hold the network to be reachable on it | Moving a group whose service is consumed remotely (e.g. the explorer, routed to by the proxy) onto a machine without that network |
| `routing selects <net> but it is neither shared nor routed from A to B` | The *consumer* side, which a route **can** rescue | Same move, when only the consumer side is missing |

Two guards are the workbench's own, not the resolver's: `remove_rpc_node`
refuses to remove the last RPC node, and `remove_proxy` refuses to remove the
last proxy. Both would otherwise fail later with a much less helpful message.

---

## 8. Frontend

Three files, no framework, no build step: `index.html`, `style.css` and
`canvas.js`. Everything is plain DOM plus hand-built SVG.

### 8.1 Why no framework

The interesting part of the UI is a small, deterministic topology graph whose
layout must be recomputed from scratch whenever the inventory changes. A
component framework would mostly get in the way of that, and any bundler would
break the "no Node at runtime, no CDN" constraint. The DOM here is small enough
(tens of machines, ~30 cards, a handful of edges) that a full rebuild on every
state change is the simplest correct thing.

### 8.2 The graph JSON contract

`canvas.js` is written against the shape `graph.py` emits. If you change a field,
change it here too.

| Field | Consumed by |
|---|---|
| `machines[].{id, execution, management_address, addresses, docker_subnet, group_ids, service_count}` | machine header, inspector |
| `machines[].if_lost` | risk markers, objective status, inspector notes |
| `groups[].{id, kind, machine_id, service_ids, members}` | box nesting, inspector, palette filters (`kind === 'apps'` for proxies) |
| `groups[].if_lost.consensus` | group risk note, `tolerate_validator_group` |
| `services[].{id, type, family, node_kind, machine_id, group_id, subtitle, exposure}` | cards; `family` for colour and layout bands; `node_kind` and `type` for the palette |
| `edges[].{consumer, provider, name, transport, crosses_host, network, port}` | drawn edges; `transport === 'docker'` drives the local-link badges |
| `networks[].{id, kind, cidr, machine_ids}` | rail, network layout, address editors |
| `availability.*` | rail stats and the objective computation |
| `routes[]` | the fabric view's links between network bands, and the rail's route editor |
| `operator.*` | every control panel (see below) |
| `inventory_format`, `deployment_id`, `label`, `profile`, `warnings` | masthead, rail |

`operator` is the important one: it carries the **authored** compact facts
verbatim (`validator_groups`, `observer_groups`, `storage_peers`, `replication`,
`routing`, `rpc_nodes`, `bindings`, `overrides`, `objectives`,
`acknowledgements`, `proxies`, `advertise`, `machines`, `networks`), so the
panels can show and edit what the operator wrote rather than re-deriving it from
the resolved document.

### 8.3 Layout: local geometry, then placement

```
buildBoxes(graph, fabric)              → per machine: its size and local geometry
layoutBoxes(boxes, mode, graph, top)   → sets box.x / box.y, returns layout captions
finalizeLayout(boxes, bands, fabricBands, gutter) → card rects, edge anchors, canvas size
layoutFor(graph, fabric, mode, positions)         → the above in the right order
```

**There are two box models**, chosen by the view (§8.8): the *service* model
nests groups and cards, and the *fabric* model is a compact machine card listing
where it sits on the network. `finalizeLayout` handles both — the fabric model
has no groups, so it contributes no edge anchors.

Four layout strategies: `columns` (default), `grid`, `function` (bands by
dominant family), `network` (bands by network signature). Machines always start
below the fabric layer, so `layoutBoxes` takes the `top` it may begin at.

**Positions are view state, never part of the inventory.** They live in
`localStorage` under `webwizard.layout.v2`, keyed by inventory path:

```jsonc
{ "<inventory path>": {
    "view": "services",                                  // or "fabric"
    "services": { "mode": "manual", "positions": { "<machineId>": {"x": …, "y": …} } },
    "fabric":   { "mode": "columns" }
} }
```

The two models have different geometry, so their arrangements are kept apart.
Dragging a machine sets that view's `mode` to `"manual"` and stores every box;
clicking a layout button clears its positions. `localStorage` is wrapped in
try/catch and mirrored in a module variable so it degrades instead of throwing,
and the old flat shape (`v1`) is migrated into `services` on read.

### 8.4 The two gestures

They must not be confused, and the code separates them explicitly:

* **Machine header** (`g.machine-header`, the top `MHEAD` px band) → moves the
  box. Pure view state. During the drag the machine's `<g>` gets a `transform`
  and `placeEdges` re-anchors every edge from shifted centers, so the lines
  follow live. On release the offset is committed and the whole scene re-renders.
* **Group** (`g.group`) → `move_group`. The group's `<g>` is transformed, the
  machine under the pointer is highlighted via `boxAt()`, and on release the
  operation is posted with the drop target. If the target is the same machine, or
  there is none, the transform is simply removed.

Both are driven from one `pointerdown`/`pointermove`/`pointerup` set on the
`<svg>`, with `drag.kind` discriminating. A `suppressClick` flag set on a
successful drag stops the subsequent `click` from also changing the selection —
a plain boolean, not a timestamp, so it is deterministic.

Selection is a `click` on the `<svg>` resolved through
`event.target.closest('g.group' | 'g.machine')`, with `null` clearing it.

### 8.5 Drawing

Draw order is bands → edges → machines → groups → cards, so edges emerge from
under the boxes. Only `crosses_host` edges are drawn; each is one `<g>` holding
the path and an optional label, positioned by `placeEdges(entries, centers)`.

`hover` on a card adds `hot`/`dim` to the edge groups and `rel` to the far
endpoint, which is the cheapest way to answer "what does this service talk to".

Two annotations are worth knowing about because they encode insights rather than
decoration:

* **Local-link badges** — `localLinks(graph)` counts the `docker` edges touching
  each service and draws a small circled number with a `<title>` naming them.
  Without it, moving a group next to its peer looks like the link vanished: in a
  six-host example, of 51 declared connections only 8 cross a host, 6 are
  ordering dependencies and **37 are Docker-local** — and 14 services have no
  drawn edge at all. `internalLinks(graph, ids)` is the same idea for a group or
  a machine, used by the inspector.
* **Risk markers** — `machineRisk(machine)` maps `if_lost` to a coloured dot in
  the machine header (quorum loss in red, primary-RPC loss in ochre, storage
  publication in grey) with an explanatory tooltip. The rail legend explains it.

### 8.6 Panels

Every panel is a `render*` function that rebuilds its DOM from scratch on each
`render()` call. They are pure functions of the graph plus `session`:

| Panel | Function | Drives |
|---|---|---|
| Save a copy | `renderSavePanel` | `POST /save`, prefilled with `draft.suggested_path` |
| Selection | `renderSelection` | `move_group`, `set_*_count`, `remove_*_group`, `add_*` (palette-in-place), `set_machine_address`, `remove_machine` |
| Structure | `renderStructure` | profile, primary RPC, routing, replication, networks, RPC bindings, objectives/acknowledgements, announce overrides |
| Web edge | `renderWebEdge` | proxy listener, TLS, site, routes |
| Palette | `renderPalette` | adds machines, validator/observer groups, RPC nodes, storage peers, proxies |

Because the panels rebuild, any control that needs to survive a re-render must
take its value from the graph — which is exactly why `operator` carries the
authored facts.

The three long panels (`structure`, `webedge`, `palette`) are marked
`class="panel collapsible"`. `wireCollapsiblePanels()`, called once from
`boot()`, turns each heading into a toggle and starts the panel collapsed
(`.panel.collapsed > :not(h2) { display: none }`). Two things follow:

* The collapsed state lives on the **`<section>`**, not on the body that
  `render*` rebuilds, so re-rendering a panel never loses its expanded state.
* The headless canvas checks query the DOM directly, and jsdom does not apply
  the stylesheet — so panel contents are found by `querySelector` whether the
  panel is collapsed or not. Do not read a passing assertion there as "the
  control was visible".

### 8.7 The network fabric, and the Services/Fabric switch

The service view shows *dependencies*; it does not show what makes them
possible. A connection rides a **network**, and in a fleet where every machine
holds an address on every network (as in every maintained example) the network
never appears — so a cable labelled `vpn` looks arbitrary, because the only
thing deciding that label is the four-line `routing` policy. Worse, when two
machines do *not* share a network, what makes the connection work is a declared
`routes` entry, and that was invisible entirely.

`drawFabric()` puts the network layer on the canvas:

* one **band** per network above the fleet, tinted (`NETWORK_TINT`, deliberately
  quieter than the family hues) and labelled with its kind and CIDR;
* an **attachment stub** from every machine up to every band whose network it
  holds an address on, staggered so several do not overlap. The *absence* of a
  stub is the signal;
* in the fabric view only, a dashed **route** between the two bands it joins,
  drawn in a gutter on the right, with `via` in its tooltip.

The toolbar's `Services | Fabric` switch (`data-view`) picks the box model and
how loud the fabric is. In the service view the bands are drawn quietly and the
service layer is what you read; in the fabric view the service cards and edges
are absent altogether and the machines list their addresses — which is the
question "who is on what, and what can reach what" with nothing else in the way.

### 8.7 Read-only fallback

`openInventory()` first tries `POST /api/session`. If that 400s (the document is
a full v3 inventory, not an editable compact one) it falls back to
`GET /api/graph` and shows a read-only view: the save/selection/structure/palette
panels stay hidden and the draft bar says so. Only the layout and machine-drag
gestures remain.

---

## 9. The HTTP API

All `/api/*` routes require the token. `POST` bodies are JSON objects.

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/?token=…` | — | the page, and sets the session cookie |
| `GET` | `/static/<name>` | — | `index.html`, `canvas.js` or `style.css` (the only three allowed), `no-store` |
| `GET` | `/api/inventories` | — | `{inventories: [{name, path}]}` for `examples/operator-inventory` |
| `GET` | `/api/graph?path=…` | — | the graph, read-only and stateless (400 if unreadable) |
| `GET` | `/api/operations` | — | `{operations: [...32 names]}` |
| `POST` | `/api/session` | `{path}` | `{session_id, …state}` |
| `GET` | `/api/session/<id>` | — | state |
| `POST` | `/api/session/<id>/op` | `{kind, params}` | state, with `ok:false` + `error` on refusal |
| `POST` | `/api/session/<id>/undo` | `{}` | state |
| `POST` | `/api/session/<id>/reset` | `{}` | state |
| `POST` | `/api/session/<id>/save` | `{path}` (optional) | state + `saved` (the written path), or `ok:false` |

`state` is always:

```jsonc
{
  "ok": true,
  "error": null,
  "draft": {
    "path": "…/production-six-host.json",   // what was loaded, never written
    "changed": true, "sections": ["placement"],
    "can_undo": true, "stage": "placement", "written": false,
    "saved_path": null, "suggested_path": "…/production-six-host.wizard-20260913-201500.json"
  },
  "graph": { … }
}
```

---

## 10. Testing

A pytest suite plus an optional headless browser layer. Deliberately, **no test
totals or file sizes are quoted anywhere in these docs**: they rot with every
unrelated edit. What is checked instead is structure (§10.4).

| File | Layer |
|---|---|
| `tests/test_session.py` | the draft: open, every operation's happy path and refusals, undo, reset, routes, saving, the no-write invariants |
| `tests/test_server.py` | the API through `fastapi.testclient`: token gate, session lifecycle, rejection semantics, save endpoint |
| `tests/test_graph.py` | the projection: families, node kinds, transports, failure domains |
| `tests/test_readonly.py` | the boundaries: `deployment_v3.runner` never imported; only `loader.py` imports `deployment_v3` |
| `tests/test_docs.py` | this document against the code: operations, endpoints, invariants |
| `tests/test_frontend.py` | runs the jsdom harness (skipped when `node`/`jsdom` are absent) |

### 10.1 The boundaries are tests, not conventions

`test_readonly.py` is the guard rail for the two most important invariants. One
imports the world and asserts `deployment_v3.runner` is not in `sys.modules`; the
other scans the package for real `import deployment_v3…` statements outside
`loader.py`. If someone adds a convenient import elsewhere, these fail.

### 10.2 The frontend harness

`tests/frontend/canvas.test.js` boots the real `index.html` + `canvas.js` in
`jsdom` and drives it with real events. `tests/test_frontend.py` generates its
fixtures **with the real `DraftSession`** — the initial graph, and the graph after
a `move_group` — so the browser side is exercised against documents the deployer
actually produced, not hand-written JSON.

Four things are worth knowing before you write in it:

* `fetch` is stubbed; there is no server. The stub records every call so the
  tests can assert *which operation was requested*, which is the point: the UI's
  job is to send the right typed operation.
* `getBoundingClientRect()` is always zero in jsdom, so `svgPoint()` degrades to
  identity scaling and pointer coordinates are used as SVG coordinates directly.
  Geometry is therefore read from the SVG attributes (`x`, `y`, `width`,
  `height`) rather than measured.
* Machines must be grabbed **in the header band**; a synthetic pointerdown in the
  middle of a tall box will not start a machine drag (by design).
* The stub must mirror the server's semantics: a rejected operation returns
  `ok:false` **with the current draft state**, not with a clean one.

Run it with either

```bash
cd web-wizard/tests/frontend && npm install   # once; node_modules is gitignored
NODE_PATH=… python -m pytest                    # or just run pytest
```

Without `jsdom` the test skips, and the Python suite is unaffected.

### 10.3 What to add when you add an operation

1. A happy path asserting the resulting `session.raw` **and** something visible in
   `session.view()` (availability, failure domains, edges) — the view is what the
   user sees.
2. A refusal case, asserting the message fragment and that `session.raw` is
   unchanged.
3. A case in the invalid-parameter table of
   `test_invalid_parameters_are_rejected_before_any_change`.
4. If it is reachable from the UI, a check in `canvas.test.js` asserting the
   operation that gets posted.

**Never let a test write into `examples/`.** `save()` resolves a relative path
next to the inventory being edited, so always save into `tmp_path` with an
absolute path.

### 10.4 This document is tested too

`tests/test_docs.py` checks DEVELOPMENT.md against the code:

* the **operations table** must list every handler in `_HANDLERS` and nothing
  else — add an operation without documenting it and the test fails, naming it;
* the **API table** must document every route `server.py` declares (paths are
  normalised, so the doc may write `/api/session/<id>` while FastAPI uses
  `{session_id}`);
* the **invariants that were expensive to discover** must still be written
  down: the `sort_keys` warning and positional naming, the never-overwrite rule,
  and the `deployment_v3.runner` boundary.

Totals are deliberately *not* asserted, because a test that says "the docs claim
N tests" breaks the moment you add a test — which is exactly why those numbers
are gone from the prose.

---

## 11. Extension recipes

### 11.1 Add an operation

1. In `operations.py`, write `def _do_thing(session, params) -> None`.
   Validate the cheap things first with the helpers (identifiers, enumerations,
   counts) so the user gets a clear message; check membership with `_known`.
2. Do the actual mutation inside `_commit(session, "<stage>", change)`, and make
   `change` a *minimal* lambda over `raw`. Anything structural — networks,
   exposures, group kinds — is the resolver's job; do not duplicate it.
3. Add the name to `_HANDLERS`. The stage string is the section shown in the
   draft bar; pick one from `SECTION_ORDER` in `session.py`.
4. Tests, per §10.3.
5. If it is reachable from a panel, wire a control to
   `applyOp('<name>', {...})`. Remember panels are rebuilt on every render, so
   the control's value must come from the graph.

If the operation needs a compact fact the graph does not carry, add it to
`OperatorView` in `graph.py` (§11.2) rather than reading the raw document from
the browser — the browser never sees the draft.

### 11.2 Add a field to the graph

Add it to the relevant dataclass in `graph.py` and populate it in
`build_graph`. Prefer values the *deployer* computed (`plan`, `availability`)
over re-deriving them here: `if_lost` is the model to copy — it is lifted
straight out of `availability.scenarios`. Add an assertion to `test_graph.py`,
add a row to the table in §8.2, and consume it in `canvas.js`.

### 11.3 Add a layout strategy

`layoutBoxes(boxes, mode, graph)` in `canvas.js`. Return the band labels you
want drawn; set `box.x`/`box.y`. Add a button with `data-layout="<mode>"` to the
toolbar — `boot()` wires every `.layouts button` automatically. Modes must be
deterministic: they are recomputed on every render, and `manual` positions build
on top of `columns`.

### 11.4 Add a panel

Add a `<section class="panel" id="…-panel" hidden>` to `index.html`, a
`render<Name>(graph)` that rebuilds its body, and call it from `render()`.
Guard it with `if (!graph.operator || session.readOnly)` so it disappears for a
read-only document. Use `field`, `selectControl`, `numberInput`, `miniButton`,
`el` and `riskNote` — they exist so the panels stay consistent and small.

### 11.5 Change the transactional model

Probably don't. The current model (candidate → resolve → commit or reject) is
what makes the rest of the system simple: no invalid states, no rollback UI,
undo as snapshots, and live validation for free. If you need *previewing* a
change that may be invalid (for example, showing what a move would break before
committing), add it as a separate read-only endpoint that resolves a candidate
without committing — do not weaken `apply()`.

### 11.6 Advance a phase

Phase 4 as scoped is: (a) a full availability overlay, i.e. selecting a machine
and seeing the complete scenario list rather than a single risk dot; and (b) a
review step at save time showing the *resolved* diff (before vs after), which is
what an operator actually approves. Both are read-only additions: (a) is a graph
field plus a panel (`availability.scenarios` already carries everything); (b) is
`resolved_inventory_diff`-shaped work you would write yourself — the deployer's
copy lives in the module you are not allowed to reuse, but it is 10 lines and
its shape (added/removed/changed per section) is worth copying.

---

## 12. Running and troubleshooting

```bash
./web-wizard/run.sh --inventory examples/operator-inventory/local-ha.json
```

`run.sh` picks the newest usable interpreter (`DARK_PYTHON`, then
`python3.13…3.10` on `PATH`, then the deployer's `venv/bin/python`, then
`python3`), creates `web-wizard/.venv` from `requirements.txt` if missing, and
executes `python -m webwizard` **without changing directory** — it puts the
package on `PYTHONPATH` instead, so a relative `--inventory` resolves exactly as
typed.

| Symptom | Cause and fix |
|---|---|
| `403` when opening the page | The token is only in the printed URL. Open the URL `run.sh` printed; the cookie is then set |
| `deployment_v3 is not importable` | The workbench is not inside a dark-deployer checkout. `REPO_ROOT` is derived from `webwizard/…` |
| `Unable to evaluate type annotation 'str \| None'` | A FastAPI route signature used `X \| None`. FastAPI evaluates annotations at runtime, which fails on 3.9 — use `typing.Optional[...]` |
| The page shows old behaviour after an edit | It shouldn't: `/static/*` is served `no-store`. Hard-reload once if you changed `index.html` structure |
| `address already in use` | `--port` (default 8765); the server falls back to an ephemeral port if busy, and prints it |
| The frontend test is skipped | `node` missing, or `jsdom` not resolvable from `tests/frontend` (`npm install` there), or `NODE_PATH` unset |
| A relative `--inventory` is "not found" | Relative paths resolve against your CWD, then the repo root — check you are where you think you are |
| `save` refused | By design: the target exists, is the loaded inventory, is a directory, or its parent does not exist. Pick a new name |

---

## 13. Known limitations and next steps

**Deliberate limitations**

* **Single user, in-memory sessions.** Sessions live in a dict on the process;
  restarting the server loses every open draft. There is no TTL, no persistence
  and no multi-user notion. That is appropriate for a local tool; do not add a
  database without a reason.
* **The whole draft is sent back on every state change.** `state()` includes the
  full graph. At this scale (a few hundred kB) it is simpler than patching, and
  it makes the browser stateless. Revisit only if inventories get much larger.
* **Saving does not sort keys, on purpose.** An unmodified draft is written back
  byte for byte; an edited one is re-emitted with `indent=2` and the draft's key
  order. Sorting would be a correctness bug, not a style choice — see §5.3. A
  consequence worth knowing: a saved file's *formatting* (inline objects, key
  order) follows whatever the source had plus your edits, rather than being
  normalised.
* **No CSRF token beyond the session cookie.** The cookie is `HttpOnly` +
  `SameSite=Strict`, the app is bound to loopback, and every mutating route
  requires it. Adequate here; not a pattern to lift into a networked service.
* **No secrets are ever read**, so the workbench cannot show you whether a
  secret exists on disk — only that the inventory references it.

**Gaps in the UI** (the operations exist and are exercised by tests)

* `remove_rpc_node`, `remove_storage_peer` — the palette can add them but not
  remove them.
* `set_override` — moving `explorer`/`resolver` out of `apps` on their own.
* `set_deployment_label` — cosmetic, deliberately left out.

**Next: phase 4**

1. **Availability overlay.** `availability.scenarios` already carries a
   `FailureScenario` per validator, per validator group, per machine, per
   storage peer and for the primary RPC, each with `consensus`, `storage`,
   `observers` and `applications_rpc` verdicts. `if_lost` on the graph is only
   the single worst verdict per machine. Surfacing the full list — "select a
   machine, read what the deployment loses" — is a graph field plus a panel.
2. **Resolved-diff review before saving.** Right now the draft bar says *which
   sections* changed. What an operator approves is the *resolved* consequence:
   which services appear, disappear, or change machine, exposure or network.
   `resolved_inventory_diff(before, after)` is the shape to reproduce over
   `session.resolution.document` versus a resolution of `session.original`.
3. Then, optionally: a read-only JSON view of the resolved document, per the
   original plan.

---

## 14. File map

Sizes are not quoted (see §10); `canvas.js` is by far the largest file and
`loader.py` the smallest.

```
web-wizard/
  README.md               what it is and how to run it
  DEVELOPMENT.md          this document
  run.sh                  interpreter selection, venv bootstrap, launch
  requirements.txt        fastapi, uvicorn, jsonschema (workbench-only)
  requirements-dev.txt    pytest, httpx
  pyproject.toml          requires-python >= 3.9; static/** as package data
  .gitignore              .venv/, __pycache__/, .pytest_cache/, node_modules/
  webwizard/
    __init__.py           package docstring: this is a read-only consumer
    __main__.py           python -m webwizard
    loader.py             the ONLY deployment_v3 import; Core; read_inventory; load_topology
    graph.py              pure plan -> canvas graph projection, failure domains, routes, OperatorView
    session.py            DraftSession: apply / undo / reset / view / save
    operations.py         the typed operations, parameter validation, _HANDLERS
    server.py             FastAPI app, token gate, session registry, save endpoint
    static/
      index.html          masthead, rail, toolbar (layouts + views), draft bar, canvas, overlay
      style.css           drafting-table theme; all tokens in :root
      canvas.js           layout engine, fabric, drawing, gestures, panels, persistence
  tests/
    conftest.py           puts the package on sys.path
    test_session.py       the draft, every operation, undo/reset, routes, saving, invariants
    test_server.py        the API surface
    test_graph.py         the projection
    test_readonly.py      the import boundaries
    test_docs.py          this document against the code
    test_frontend.py      runs the jsdom harness (skips without node + jsdom)
    frontend/
      package.json        jsdom, test-only
      canvas.test.js      the headless browser layer
```

**Keeping this document honest:** when you change a contract — a graph field, an
endpoint, an operation's parameters, an invariant — update the relevant table
here in the same commit. Several tables in this document exist precisely because
they were expensive to discover.
