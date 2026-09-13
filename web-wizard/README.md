# web-wizard

A local web workbench for dARK **operator inventories**: draw the topology,
re-place it, and have every change validated by the deployer itself — then save
the result as a new file.

Edits stay in memory until you save. **The inventory you open is never modified,
and saving can only ever create a new file** — there is no code path that
overwrites one.

This directory is fully self-contained. It declares its own dependencies in
`web-wizard/requirements.txt` (installed by `run.sh` into a private
`web-wizard/.venv`), touches no file outside `web-wizard/`, and uses
`deployment_v3` strictly read-only. It never deploys, never runs SSH/Docker/Git,
and never reads secret material.

## Phase 0 — see

An operator inventory rendered as a topology:

* one box per **machine**;
* the services that live on each machine, nested inside their **groups**;
* **only the connections that cross a host boundary** are drawn as edges
  (intra-host links use the Docker network and are hidden). Every card counts
  the links it keeps inside its own machine in a small badge, so a connection
  never looks like it vanished when two services end up colocated;
* a rail with the availability summary (validators, quorum, tolerated losses,
  chain copies, primary RPC), the declared networks, the routing decisions and
  any resolver warnings.

## Phase 1 — edit

Edits are **typed operations**, never JSON patches, and each one is committed
only if the whole compact inventory still resolves into a valid v3 plan. A
rejected change leaves the previous draft untouched and reports why, so the
canvas never shows an impossible deployment.

* **Drag a group onto another machine** to re-place it. A group is the unit of
  placement, so everything it hosts moves with it.
* **Drag a machine header** to move the box — that is view state only, and is
  remembered per inventory in `localStorage`.
* Selecting a **group** shows its services and lets you scale it (validator or
  observer count), re-place it, or remove it.
* Selecting a **machine** shows its addresses and what its loss would cost, and
  lets you add a validator group, an observer group, an RPC node or a storage
  peer there.
* The **Structure** panel carries the profile, the primary RPC, the routing of
  each link class, the storage replication targets and the networks.
* **Undo** and **Reset** unwind the draft. The bar above the canvas says which
  sections differ from the file — and that nothing has been written.
* Long editing panels are collapsible, so the topology and operational summary
  remain visible while working through a large inventory.
* The toolbar can find a machine, group or service, zoom in and out, or fit the
  whole topology into the viewport. Zoom and layouts are remembered per
  inventory. Machines and groups can also be selected with the keyboard.

Machines that would take QBFT quorum down with them are called out on the
canvas and in the inspector, using the deployer's own failure scenarios.

## Phase 2 — the palette and the edge

* The **Add component** panel places anything the compact format can carry:
  machines (with their addresses), validator groups, observer groups, RPC nodes,
  storage peers and web proxies — anywhere, in any application group.
* The **Web edge** panel edits each proxy: its listener (bind, port, network),
  its TLS mode, its site host and public origin, and its routes.
* **Structure** gained the RPC bindings (which node answers each consumer, when
  it is not the primary), the availability objectives with their live status and
  their acknowledgements, and the announce overrides used behind NAT.
* Selecting a machine lets you retarget its addresses per network.
* Machines whose loss would break quorum — or take the primary RPC with it — are
  now marked on the canvas, not just in the inspector.

Several of the deployer's own rules surface here as clear refusals rather than
silent breakage: a private proxy listener is pinned to the web policy port, a
proxy may only live in an application group and only one per machine, a route
must target an application service, a direct-TLS listener needs its secret
names, and a site needs at least one route.

### Operations

34 named operations are exposed through `POST /api/session/<id>/op` (see
`webwizard/operations.py`, and `GET /api/operations` for the list): placement,
machines and their addresses, networks and the routes between them,
validator/observer groups and their counts, RPC nodes, the primary RPC and
per-consumer bindings, storage peers and replication, routing,
explorer/resolver overrides, profile, deployment label, availability
acknowledgements, announce overrides, and the web edge (proxies, listeners,
TLS, sites and routes).

## Phase 3 — save a copy

The **Save a copy** panel writes the draft to a brand new file:

* the destination is prefilled with a new sibling name
  (`<name>.wizard-<timestamp>.json`), and you can type any path instead;
* the file is created with `O_CREAT | O_EXCL`, so an existing file can never be
  replaced — a second save to the same path is refused, not silently clobbered;
* the inventory being edited is a refused destination, by name;
* the draft is re-resolved immediately before writing, so what lands on disk is
  something the deployer can read back;
* the draft *is* the loaded document plus the edits, so every section the canvas
  does not touch is carried over unchanged, and an **unmodified** inventory is
  written back byte for byte;
* key order is preserved deliberately rather than sorted: validator and observer
  instance names are positional (`observer01` is whichever group is declared
  first) and an RPC binding refers to them by name, so sorting the keys would
  silently re-point it at another machine;
* a saved copy reopens in the workbench as an ordinary inventory.

Relative destinations resolve next to the inventory being edited; a missing
directory is refused rather than created.

## Phase 5 — the network fabric

A connection does not run between two services; it runs over a **network**. The
canvas used to imply point-to-point cabling, which is misleading — in the
six-host example every machine sits on both networks, so a cable's label comes
from the four-line `routing` policy rather than from topology.

* Every network is drawn as a **band** above the fleet, and each machine is
  attached to the bands whose network it holds an address on. A missing
  attachment is the signal.
* A **Services / Fabric** switch changes what the canvas is for. Fabric mode
  drops the service layer entirely: machines list their addresses, and the
  reachability between networks is drawn.
* **`routes`** — the one thing that lets a machine reach a network it is not on
  — is now visible and editable, instead of being the invisible part of the
  inventory that decided whether a move was legal.

## Run

> Continuing this work? Read **[DEVELOPMENT.md](DEVELOPMENT.md)** — it explains
> the mental model (the two documents), every module, the transactional
> contract, the domain rules and how to test and extend it.


```bash
./run.sh --inventory examples/operator-inventory/local-ha.json
```

On first run `run.sh` creates `web-wizard/.venv` and installs
`web-wizard/requirements.txt`; the deployer's own environment is never touched.
Then it prints a `http://127.0.0.1:<port>/?token=...` URL. The server binds only
to `127.0.0.1` and requires a per-run token.

Requires Python 3.9 or newer. `run.sh` picks the newest usable interpreter for
the workbench's own venv, in this order: `DARK_PYTHON` if set, then
`python3.13…3.10` on your `PATH`, then the deployer's own `venv/bin/python`
(Homebrew 3.12 on macOS), then the system `python3`. The code itself runs under
3.9 too, so no annotation or syntax needs a newer interpreter.

If you already have a `web-wizard/.venv` from an older interpreter and want to
rebuild it on a newer one, remove it first:

```bash
rm -rf web-wizard/.venv && ./web-wizard/run.sh --inventory <inventory>
```

## How it stays safe

The workbench borrows only the deployer's read-only entry points, all imported
in `webwizard/loader.py`:

* `resolve_inventory` — expands a compact operator inventory, purely;
* `build_plan` — resolves, validates and plans, with no side effects;
* `availability.analyze` — the failure scenarios and quorum arithmetic.

`deployment_v3.runner` (apply/push) is never imported — a test asserts it is
absent from `sys.modules` after resolving an inventory, and another asserts that
`loader.py` is the only module allowed to import `deployment_v3` at all.

The draft session, its operations, undo and saving are the workbench's own
(`session.py`, `operations.py`) — it does not reuse the deployer's earlier
wizard. Validation builds a real plan from the resolved draft through a private
temporary file, exactly as an offline preview, so the workbench sees precisely
what `deploy.py` would.

Saving is the only write the workbench performs, and it can only create a new
file. Tests pin that down: saving refuses an existing path and a destination
equal to the inventory being edited, an untouched round trip preserves every
field, and no test leaves a file behind in the examples directory.

## Layout

```
web-wizard/
  run.sh                 launcher; bootstraps web-wizard/.venv
  requirements.txt       workbench-only dependencies (fastapi, uvicorn, jsonschema)
  webwizard/
    loader.py            read-only access to deployment_v3 (the only module that imports it)
    graph.py             pure plan -> canvas graph projection, with failure domains
    session.py           the draft: apply / undo / reset over a compact inventory
    operations.py        the typed operations, and their parameter validation
    server.py            FastAPI app, 127.0.0.1 + session token
    static/              index.html, canvas.js, style.css (no CDN)
  tests/
    frontend/            optional headless canvas checks; npm install here to enable
```

The canvas checks (`tests/frontend`, driven by `tests/test_frontend.py`) exercise
the layouts, both drag gestures, rejection handling, undo and persistence
against graphs built by the real draft session. They are optional: pytest skips
them unless `node` can resolve `jsdom`.
