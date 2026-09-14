# Scenario acceptance notebooks

These notebooks exercise a deployed scenario rather than describe the generic
HTTP API.  They are deliberately explicit about which endpoints are public and
which checks run inside the deployment's Docker network.

Run them from the repository root after a successful `install` and `verify`.
They create test data in the selected deployment; use a disposable local
environment unless those records are wanted.

| Notebook | Scenario | What it proves |
| --- | --- | --- |
| `scenarios/local-ha-deposit-lifecycle.ipynb` | `examples/operator-inventory/local-ha.json` | The resolved four-validator/two-storage-peer topology, the loopback gateway, a full authority-to-ARK deposit lifecycle, resolver redirect, and Store replication status. |

The generic notebooks at this directory's root remain useful for API
integration work.  They intentionally retain configurable direct endpoint
URLs and are not a substitute for a scenario acceptance notebook.
