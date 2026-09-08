# Central Compose orchestration

All operational Compose definitions live in this directory. Component
repositories still provide Dockerfiles and application code, but the
installer never discovers or starts a component-local Compose file.

The host roles are deliberately the same in developer and production:

| file | simulated/real host |
| --- | --- |
| `apps.yml` / `apps-production.yml` | APIs, dashboard and (in production) `rpc01` |
| `blockchain-a.yml` / `blockchain-production.yml` | validator group A (and explorer) |
| `storage.yml` / `storage-production.yml` | one Kubo + one Cluster peer |

Developer uses the shared `dark-backbone` Docker network to simulate the
private link. Production overlays create only a host-local bridge; Besu and
Cluster cross-host traffic uses the VPN addresses rendered from
`deployment-topology.json`.

`host.yml` is retained as a diagnostic aggregate, not as the production
entrypoint. `install.py`, `clean.py`, `stop.py` and `restart.py` select the
role-specific file and project name from the canonical topology.
