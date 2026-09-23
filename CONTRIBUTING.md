# Contributing to dARK

Thank you for your interest in contributing to dARK! dARK is designed as
**public, federated digital infrastructure**, and your contributions help
ensure it remains sustainable and accessible.

By submitting a pull request or other contribution, you agree to license your
contribution under the **GNU Affero General Public License v3.0 (AGPLv3)**.

## Environment

- CPython **3.14** (exact), Docker Engine, Compose v2.
- `./create_venv.sh [--python PYTHON] [--venv PATH] [--with-tui]` creates the
  virtual environment; add `--with-tui` for the Textual editor/TUI extras.
- `./deploy.sh` is the daily launcher: it keeps the venv healthy
  (self-repairing from a requirements stamp) and forwards every
  `deploy.py` subcommand.

## Repository layout for contributors

- `deployment_v3/` — the deployer core: inventory resolver, planner,
  renderer, runner, metrics.
- `examples/operator-inventory/` — maintained compact inventories (preferred
  format); `examples/deployment-v3/` — legacy full-inventory examples.
- `components/` — component checkouts (acquired at runtime, not tracked here;
  each component has its own repository).
- `tests/` — deployer test suites.

## Tests

The deployer uses **unittest**. The eight suites in `tests/` cover
availability, the AWS CloudFormation profile, deployment v3 end to end,
metrics (collector, Prometheus export, TUI view), the operations TUI, and the
operator-inventory format:

```bash
venv/bin/python -m unittest discover -s tests -v
# or a single suite
venv/bin/python -m unittest tests.test_operator_inventory -v
```

The web inventory wizard has its own suite under `web-wizard/tests`,
run with pytest (`web-wizard` requires exactly CPython 3.14 — see
`web-wizard/README.md`).

New features and bug fixes come with a focused test; run the full suite before
opening a pull request.

## Linting

`requirements-dev.txt` pins `ruff` and `pytest`:

```bash
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m ruff check deployment_v3
```

## Component branches and catalog changes

The catalog (`deployment_v3/catalog_data/dark-platform-baseline-v1.0.json`)
pins default component branches — normally `main`. A development branch is
declared in the inventory that needs it (`overrides.components`), never by
editing the global catalog. See
[docs/development.md](docs/development.md) for the component-development
workflow, and `docs/known-issues.md` for the open items a change may touch.

## Commits and documentation

Keep commits short and thematic ("Add per-machine bundles and deployment
verification"). Documentation follows one rule: when text and code disagree,
the code wins — fix the text, or record a genuine code defect in
[docs/known-issues.md](docs/known-issues.md). New and rewritten documentation
is written in English.