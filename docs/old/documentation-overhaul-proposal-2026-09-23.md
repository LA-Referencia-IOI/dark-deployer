# Propuesta de renovación documental — dark-deployer

Fecha: 2026-09-23 · Base: `main @ 1098474` ("Update deployment and AWS documentation")
Alcance: toda la documentación del repo (raíz, `docs/`, `infrastructure/`, `examples/`, `local-infra/`, `notebooks/`, `web-wizard/`, `blockchain/`) más los READMEs de los 10 componentes, verificada contra el código y contra los commits de los dos últimos meses (23-jul → 23-sep).

Idiomas de trabajo: **la documentación nueva y el README reescrito van en inglés** (siguiendo la reescritura del 13-sep, `e54a1e4`); esta propuesta está en español porque es material de decisión para el operador.

---

## 1. Método

Cinco auditorías paralelas (docs raíz; `docs/` vivos; infrastructure+examples+local-infra; componentes API; componentes UI/ops), cada una contrastando afirmaciones del texto contra el código actual, con los comandos, rutas, endpoints, plantillas y tests reales. Distingo en todo el documento:

- **VERIFICADO** — visto en el código (con `fichero:línea`) o ejecutado empíricamente (los 17 inventarios de ejemplo se resolvieron con el resolver y el planner reales).
- **INFERIDO** — deducción razonable que no se pudo comprobar aquí (se marca explícitamente).

Lo que NO se hizo: despliegues reales (AWS, Lima), ejecución de notebooks, y la validación de `aws cloudformation validate-template` con tipos SSM (se señala como probable defecto, no como hecho).

**Verificación de esta propuesta:** una pasada independiente posterior contrastó 29 afirmaciones cargantes de este documento contra el código (27 confirmadas al dígito, 1 parcial, 1 refutada y corregida aquí: `ARKResponse` sí tiene `level1_cid`/`level2_cid`; y `dashboard-redis` no se "rechaza" en `runner.py:892`, se describe como heredado). Los 12 inventarios de `examples/operator-inventory/` se resolvieron y planificaron con éxito con el código real (12/12).

---

## 2. Qué cambió en los últimos dos meses

### 2.1 El deployer: 215 commits, cuatro olas

**Ola 1 — Jul 23 → Ago 31: era "tiers" y producción.** Setup desacoplado en tres fases (blockchain / apps / storage) con `.env.integration` como artefacto de traspaso, wizard de selección, producción de 4 hosts "inmutable", shoulder policy del minter (24-ago), import directo de ARKs, política de despliegue de replicación IPFS, y el primer stack de monitorización en `dark-monitoring` (28-ago). Con retrospectiva, casi todo este flujo ya no existe como se escribió.

**Ola 2 — Sep 9–12: migración a deployment v3 (la mayor ruptura).** `5029897` (12-sep) migra el deployer a deployment v3: planner → renderer → runner sobre un **inventario operador compacto**, con `2bcee2d` (11-sep) eliminando el paquete legacy `dark_deployer`, `8c13887` retirando installer y compose files legacy, `0a8df10` retirando scripts operacionales legacy. Lima entra como soporte (`9ff01b8`). A partir de aquí, todo lo que describa "el installer", `.env` como configuración maestra o compose fijo es pre-historia.

**Ola 3 — Sep 13–16: operabilidad.** README reescrito a inglés (`e54a1e4`), web inventory workbench sustituye al wizard Textual (`a77da95`, `790d373`), multi-sede con `format_version: 3` (`11c9130`, `709899b`), runtime images centralizadas en el catálogo `dark-platform-baseline-v1.0` (`fdf0a09`), consola TUI de operaciones (`1f22db8`), panel de métricas read-only (`adb5ba1`), launcher `deploy.sh` (`30264e0`), Redis fuera (`61d39ff`), comandos de ciclo de vida gestionado (`b60f0f0`).

**Ola 4 — Sep 17–23: AWS y bundles.** Perfil CloudFormation `dark2-prod-aws` + inventario operador + tests de infraestructura (`a016ffa`, `75a71cd`, `5832270`), Python 3.14 en todo (`1d89a18`), **bundles por máquina** con `prepare`/`push`/`apply --revision` y verificación por fingerprints (`52ce8d5`, 22-sep), integración de monitorización con snapshot aplicado (`9e16808`), helpers SSM y perfil IAM restringido (`e7f5bd5`, `d6b7cd0`, `06e3d12`), preflight endurecido (`f785e90`, `91109ba`, `41ca7ce`).

### 2.2 Los componentes: un único patrón de ruptura + tres features grandes

- **Todos** pasaron a Python 3.14 (17-sep) y **todos los `docker-compose.yml` propios fueron borrados** entre el 8 y el 10 de sep ("align component with centralized deployment runtime"): minter, admin, resolver, store, explorador y dashboard. El despliegue de componentes ya solo existe vía deployment v3.
- **Minter** (18 commits): workers consolidados en tres (metadata / replicación / chain) con backoff explícito, shoulder policy DARK 2 `2xx`, import directo de ARKs + auditoría NAAN, `CHAIN_WORKER_PAGE_SIZE` 100.
- **Store API**: modo read-only (15-sep), pools de endpoints Kubo/Cluster con cooldown, replicación multi-sitio durable obligatoria, `POST /v1/replication/ensure` para promoción de réplicas.
- **dark-monitoring**: de cero a stack completo (Prometheus v3.5, blackbox, node-exporter, cAdvisor, Grafana 12.1) integrado con el snapshot aplicado del deployer, métricas de disco exportadas, viewer anónimo.
- **dashboard-web**: Redis fuera, headers de proxy/prefijo público, hub de liveness con enlaces a Grafana; `install.py` reescrito en modo gestionado.
- **dark-core-lib**: ABI dinámica por env, cliente Store API con pool y estado de replicación, `get_recent` acotado.
- **dark-ipfs**: cadena de fixes de bootstrap/peers (10–15-sep), anuncio en múltiples redes.

**Consecuencia documental central:** cualquier README de componente que instruya `docker compose up` o cite `components/services/...` / `dark-developer` / `dark-env` describe un mundo que se borró hace dos semanas.

---

## 3. Inventario documental con veredictos

Veredictos: **VIGENTE** (solo requiere incorporar lo nuevo) · **ACTUALIZAR** (drift concreto, sigue siendo útil) · **OBSOLETO** (instruye un mundo que ya no existe) · **ARCHIVAR** (informe histórico fechado con valor de registro, no de uso).

### 3.1 Raíz del repo

| Doc | Veredicto | Hallazgos principales (todos VERIFICADOS salvo marca) |
|---|---|---|
| `README.md` | **VIGENTE**, drift menor | Los 25 comandos citados existen en `cli.py:53-150`, pero omite `prepare`, `chain-bootstrap` y `logs`; **`deploy.sh` y `create_venv.sh` no aparecen ni una vez** pese a ser la vía recomendada de arranque; falta `local-ha-monitoring` en la tabla de escenarios mantenidos. Para un recién llegado: no explica qué es dARK, no hay mapa de componentes, no hay flujo visual de despliegue; los DARK_2.0_* (plataforma) quedan fuera del hub. |
| `OPERATIONS-MANUAL.md` | **ACTUALIZAR** | §1 lista 5 plantillas cuando hay 13 (`inventory_editor/templates.py:12-26`) y una de ellas (`operator-aws-active-six-host`) apunta a un `.json` **que no existe**; §3 fases obsoletas: faltan `observers` (`planner.py:14-22`) y `verify` no es fase; §5 incluye Redis en `base.images` (fuera desde `61d39ff`, solo queda como legado `runner.py:892`); omite `prepare`, `chain-bootstrap`, `secrets-init`, `metrics-export`. El resto (TUI, teclas, logs, rutas de render) es correcto. |
| `CONTRIBUTING.md` | **OBSOLETO/INSUFICIENTE** | Sin tocar desde 20-ene-2026; no cita pytest, ruff, `requirements-dev.txt`, ni las 8 suites de `tests/`. El propio README documenta tests mejor. |
| `DARK_2.0_ARCHITECTURE.md` | **VIGENTE en fondo**, drift puntual | La mejor descripción de la plataforma: contratos, funciones, eventos, puertos 8000-8003, workers — todo verificado en los `.sol` y en el código. Drift: rutas sin el prefijo `/api/v1`; §7.1 describe un compose en `components/dark-ipfs/` que no existe (lo genera el renderer); la cabecera "part of the [dARK 2.0 Documentation](README.md)" ya no es cierta (el README no lo menciona). |
| `DARK_2.0_GUIDE.md` | **VIGENTE en fondo**, drift puntual | SDK verificado casi completo (env vars, cliente, excepciones, structs). Enlace roto a `docs/deployer-operations.md` (inexistente); `pip install dark-core-lib` no refleja la vía real (checkout Git adquirido por el deployer); faltan `ARKAlreadyExistsError` y `estimate_operation_gas`. |
| `DARK_2.0_API_REFERENCE.md` | **ACTUALIZAR** (el doc con más drift) | **Falta el prefijo global `/api/v1`** en admin/minter/resolver (store no lo usa) — tal como está, ninguna URL funciona tal cual; `GET /worker/errors/permanent` **no existe** (real: `/api/v1/worker/errors` con filtro `stage` y `POST /worker/retry`, mTLS, sin documentar); redirect default 302, no 301; el ejemplo de `?info` no corresponde (real: `ArkInfoResponse` con `target`, sin `owner`/`cid`); `detail` del worker acepta 4 valores, no 2. Sin documentar: `POST /v1/replication/ensure`, `POST /api/v1/worker/retry`, `GET /health/live`. (Nota: los campos `level1_cid`/`level2_cid` que lista el doc SÍ existen en `ARKResponse` — `responses.py:38-39`; esa parte es correcta.) |

### 3.2 `docs/` (24 vivos + archivo `old/`)

**Columna vertebral actual (mantener y actualizar):**

| Doc | Veredicto | Qué le falta |
|---|---|---|
| `architecture.md` | VIGENTE | Modelo canónico de la plataforma. Huecos: bundles/revisiones, monitorización, `resolver.instances`/`storage.readers` (15-sep). |
| `operations.md` | VIGENTE | Runbook central. Faltan: `prepare`/`push --revision`/`apply --revision`, `metrics-export`, la vía CloudFormation `dark2-prod-aws`. |
| `deployer.md` | ACTUALIZAR | `format_version` real es enum `[2,3]` (`operator_schema.json:9`); hay 13 plantillas (12 utilizables + la rota), el doc lista 6; el flujo real es `prepare → push [--revision] → apply [--revision]` (`runner.py:832-840` falla sin prepare); no menciona la plantilla rota. |
| `development.md` | VIGENTE | Recién tocado (21-sep). Cosmético: la adquisición soporta HTTPS además de `git@` (`3b8682d`). |
| `deployment-v3-inventory-and-artifact-flow.md` | ACTUALIZAR | El modelo mental sigue siendo el mejor, pero el layout de bundles cambió con `52ce8d5`: `revisions/<id>/revision.json`, `current-revision.json`, symlink `current` (`runner.py:352-379`); falta `prepare` en el diagrama. |
| `deployment-v3-service-placement.md` | VIGENTE | Colocación por grupos y co-locación del Minter: coincide con los ejemplos y contratos. |
| `networking-v3.md` | VIGENTE | Verificado: `docker-lab`, `cidrs`, `advertise_*` solo en exposure `private`, puertos P2P 4001/9096. |
| `multi-site-lan-vpn-proposal.md` | VIGENTE | Es la spec de `format_version: 3`; la aceptación física dos-sedes sigue pendiente (valor vivo). |
| `lima-five-host-test.md` | VIGENTE | Enlazar el escenario dos-sedes añadido el 15-sep. |
| `aws-single-az-five-host.md` | ACTUALIZAR | Runbook manual de 5 hosts superado en la práctica por la vía CloudFormation de 6 hosts (`a016ffa`); decirlo explícitamente y reconciliar. |
| `deployer-generalization.md` + `deployer-component-contract-v0.md` | VIGENTES como diseño | Par de diseño de la siguiente fase; verificado que **nada del contrato v0 existe en código** (sin `component.json`, hooks ni bindings). Mantener en vivo mientras la fase esté activa. |

**A archivar en `docs/old/` (informes cerrados o superados):**

| Doc | Motivo (VERIFICADO) |
|---|---|
| `deployment-v3-operator-inventory-proposal.md` | Propuesta ya implementada y operativamente duplicada por `deployer.md`; su propio header lo declara. Su principio "catálogo inmutable por ID" además no se ha sostenido (branches cambiadas dentro del mismo ID). |
| `metrics-panel.md` | Registro fechado del 16-sep; no menciona `metrics/prometheus.py` ni `metrics-export` (llegaron el 18-sep). El contenido operativo vivo va al runbook/README. |
| `metrics-prometheus-proposal.md` | **La decisión ya está tomada e implementada** (híbrido B+D, ver §6); archivar tras registrar la decisión en el doc nuevo de monitorización. |
| `resolver-api-two-active-instances-problem-2026-09-15.md` | Problema resuelto y verificado (`resolver.instances`/`storage.readers` en `inventory_resolver.py:229-382`); el formato ya está en `deployer.md`. |
| `ipfs-private-swarm-bitswap-replication-diagnosis-2026-09.md` + `...-review-2026-09-14.md` | Expediente de incidente **cerrado** (14-sep). Extraer los 4 pendientes a un doc vivo (ver §6) antes de archivar. |
| `aws-single-az-five-host-review-2026-09-14.md` | Revisión fechada: 2 hallazgos ya superados por el código (preflight `91109ba`, `accept-new` `8b53b96`), 2 siguen vivos (verify sin `target_replicas`). |
| `examples-inventory-review-2026-09-14.md` | Su inventario de ejemplos quedó desactualizado; 3 hallazgos siguen abiertos (rutas absolutas del equipo en `examples/deployment-v3/production-six-host.json:290-344`, test con tupla literal `tests/test_deployment_v3.py:460-462`, IPs fijas en test `:116-119`). |
| `per-machine-bundle-implementation-plan.md` | El plan se implementó el 22-sep (`52ce8d5`) **con desviaciones**: nomenclatura (`SERVICE_COMPONENTS`+`COMPONENT_DEPENDENCIES`+`SERVICE_RUNTIME_ASSETS` en `staging_manifest.py:23-50`, no las dos tablas del doc), identidad por `revision-id` (no `machine-bundle-id`), push transfiere siempre todas las máquinas. Opciones: archivar y documentar el flujo real en vivo, o reescribirlo como referencia. Recomiendo archivar + doc nuevo corto (§4.3). |
| `pr-14-review.md` | Pendientes 2.1 y 2.2 **resueltos** (`install.py:102` pasa `--include-disk`; dashboards con `dark_docker_disk_*`). Tachar o archivar tras crear `monitoring.md`. |
| `history.md` | Reescribir como índice nuevo (ver §4.1), no archivar: es la pieza que hoy omite `development.md` y ~20 docs vivos. |

**`docs/old/`:** el criterio de su `README-index.md` sigue siendo el correcto (referencias canónicas + históricos con motivo + precedencia del código sobre el texto); su **contenido** está desalineado (habla de "bundles v2", `examples/deployment-v2/`, `dark-env` — todo pre-12-sep). Se regenera junto con el índice nuevo.

### 3.3 `infrastructure/`, `examples/`, `local-infra/`

| Doc | Veredicto | Hallazgos clave (VERIFICADOS) |
|---|---|---|
| `infrastructure/aws/cloudformation/README.md` | ACTUALIZAR | **(1)** Outputs: sigue citando `AppsUrl`/`ResolverUrl` (`README.md:323-324`); los reales son `AppsHttpUrl/AppsHttpsUrl/ResolverHttpUrl/ResolverHttpsUrl` (`dark2-prod-aws.yaml:613-616`). **(2)** Contradicción interna sobre datos: §Destrucción (`:352-355`) afirma que se conservan EBS por `Retain`, pero `RetainDataVolumes='false'` en parameters y `DeletionPolicy: Delete` condicional (`yaml:211-216`). **(3)** `.gitignore:158` ignora un fichero inexistente y el `dark2-prod-aws.parameters.yaml` real (account ID, ARN ACM, hosted zone) está **trackeado** pese a su cabecera "Do not commit this file". **(4)** README:282-5 manda `aws cloudformation validate-template` sobre una plantilla con tipos `AWS::SSM::Parameter::Value<...>` — INFERIDO: esa API los rechaza; usar `rain`/`cfn-lint`. Corregido desde el 17-sep: SSH/ControllerCidr, python3.14, sello bootstrap bien documentado. |
| `dark2-prod-aws-operator-cli.md` | **VIGENTE** | Todos los comandos coinciden con los wrappers reales post-`d6b7cd0`. Matiz: describe un flujo (instalar el inventario de ejemplo directo) distinto al del README (instanciar con `instantiate-inventory.py`), sin señalarlo. |
| `aws-single-az-six-host-cost-optimized-proposal.md` | ACTUALIZAR | Contradicciones internas del mismo patrón Retain (`:232-234` y `:289` vs decisión `:32`); `management_address` con dos versiones contradictorias (`:116` vs `:121`, la real es IP privadas); DNS dice A/AAAA y la plantilla solo crea A (`yaml:584,591`); párrafo `:414-418` obsoleto (ya implementado); decisión "Diferida" del Dashboard ya existe vía túnel SSM. |
| `examples/deployment-v3/README.md` | VIGENTE | Los 5 templates cargan de verdad con el código actual (ejecutado). Falta señalizar que es el formato legacy frente a `operator-inventory/`. |
| `examples/operator-inventory/README.md` + 12 guías | VIGENTE (2 con drift) | Los 12 inventarios resuelven sin error con el código actual; 10 de 12 están ejercitados por `tests/test_operator_inventory.py`; cero menciones a Redis. Drift puntual: `aws-active-two-site-nine-host.md:22-23` ("published at `/explorer/`" — falso: no hay esa ruta) y `production-six-host.md:23-24` (explorer va en `blockchain-a`; el gateway de apps es `apps-private`, solo LAN). |
| `local-infra/README.md` | ACTUALIZAR puntual | Los 7 scripts y 2 generadores documentados coinciden con el código real (flags verificados). No documenta `instantiate-inventory.py` (el más grande, usado por el README de cloudformation); `storage-topology.json` está huérfano (solo lo referencia un default roto de dark-monitoring). Compatibilidad bash 3.2 verificada estáticamente. |

### 3.4 Componentes

| Componente | README / docs | Hallazgos principales (VERIFICADOS) |
|---|---|---|
| `dark-core-lib` | README **ACTUALIZAR** (23-may, 4 meses); `03-api-and-wrappers.md` y `04-configuration.md` ACTUALIZAR; `06`/`09` históricos; resto VIGENTE | Sin documentar: `DARK_ABI_JSON`/`AUTHORITY_ABI_JSON` (`config.py:57-69`), `get_ark_count`/`get_recent_arks` (`client.py:196-199`), métodos de estado de replicación del Store API client, el bound de `get_recent`. `07-notebooks.md` cita el repo antiguo `dark-developer`. |
| `dark-core-admin-api` | README **ACTUALIZAR** | Sección "Docker Deployment" instruye compose **borrado** (8-sep); rutas muertas (`dark-developer`, `components/services/`, `components/libraries/`); faltan `GET /api/v1/arks/count` y `/recent` (28-ago) y `/health/live`. `admin-architecture.md`: Module Map sin `app/api/arks.py`, §9 con rutas obsoletas. |
| `dark-core-minter-api` | README **VIGENTE con drift puntual** (el más mantenido) | `CHAIN_WORKER_PAGE_SIZE` default real 100 (`config.py:108`), no 20; bloque "Docker Deployment" con compose borrado; enlace roto a `docs/old/publication-latency-control.md`; lista "Documentation Map" con formato roto (bullets huérfanos). `noid.md` y `security.md` VIGENTES (security es roadmap, no descripción). |
| `dark-core-resolver-api` | **VIGENTE** | Sin drift factual; incompleto (sin `/health`, sin sección de instalación local). |
| `dark-store-api` | **VIGENTE** (el mejor) | Última actualización con su feature (15-sep). Solo falta `POST /v1/replication/ensure` en la tabla de endpoints. |
| `dark-dapp` | README **ACTUALIZAR**; sus 3 copias `DARK_2.0_*.md` **OBSOLETAS** | Cita `configure.py`/`clean.py` que no existen, `config.ini.example` que no existe, y `account_priv_key` que no está en el código (real: `master_wallet_address/private_key`). Las copias DARK_2.0_*.md son condensados del 2-sep frente a los canónicos de la raíz (4–6× mayores y más recientes). |
| `dark-explorador` | DRIFT PUNTUAL | Base path bien documentado; Quick Start con compose borrado (8-sep); red `dark-apps`/alias `blockchain-rpc` no existen en v3; default de tabla `RPC_HTTP_URL` ≠ `.env` real. |
| `dark-monitoring` | **VIGENTE** | README al día hasta 22-sep. Matiz: dice "generated/ excluded from Git" y el `.gitignore` solo excluye `generated/*.json` y `*.prom`. |
| `dashboard-web` | README **OBSOLETO** (congelado en 2-ago) | Instalación manual con compose borrado (8-sep); sin documentar: modo gestionado (`DARK_DEPLOYER_MANAGED_COMPOSE`), prefijo público/proxy headers, enlaces Grafana/Prometheus, hub de liveness. |
| `blockchain/` | **VIGENTE** | Besu inventory-driven, errores de migración explícitos en setup/reset, `chain-*` reales en cli.py. |
| `web-wizard/` | DRIFT PUNTUAL | README dice "Python 3.9 or newer" y 34 operaciones; `run.sh:24-40` exige exactamente 3.14 y hay 46 operaciones (`operations.py:775`). DEVELOPMENT.md arrastra el mismo desfase. |
| `notebooks/` | **VIGENTE** | Los 5 notebooks reales coinciden con la tabla del README y referencian inventarios existentes. |

---

## 4. Mapa documental propuesto

### 4.1 Principios

1. **Un hub, un índice, un runbook.** Hoy conviven dos entradas operativas (`OPERATIONS-MANUAL.md` en raíz y `docs/operations.md`) y el índice de `docs/` (`history.md`) omite media biblioteca. Propuesta: runbook único en `docs/operations.md` (absorbe lo vivo de OPERATIONS-MANUAL), que queda reducido a un stub de dos líneas enlazando; índice nuevo en `docs/README.md` (history.md se archiva).
2. **El README cuenta la historia completa de forma resumida** y enlaza; los detalles viven en `docs/`. Nada de "documentation map" sin jerarquía: platform → deployer → runbooks → escenarios → histórico.
3. **El código manda sobre el texto** (regla ya escrita en `docs/old/README-index.md`): cuando una afirmación del texto y el código divergen, se corrige el texto; si el defecto es del código, se anota en `known-issues.md` en vez de "documentar el bug".
4. **Los informes fechados van a `docs/old/`** con motivo y fecha, indexados; los pendientes vivos que contengan se extraen a `docs/known-issues.md`.
5. **Idioma:** lo nuevo y lo reescrito en inglés; no se traduce en masa lo existente.

### 4.2 El README nuevo (outline completo, en inglés, orientado a newcomers)

```markdown
# dark-deployer
> Deployment and operations tooling for the dARK platform.

## What is dARK?
(5-8 líneas, para alguien que llega sin contexto)
dARK is a decentralized authority service for persistent identifiers: registered
authorities mint ARK identifiers (NAAN + shoulder + name) on a private Besu/QBFT
blockchain, resolve them via HTTP, and back the targets with content in a private
IPFS swarm (Kubo + IPFS Cluster) with configurable replication. Bibliographic
metadata is stored, replicated and published by a worker pipeline.
Components: Authority + dARK contracts · Minter API (8001) · Resolver API (8002) ·
Store API (8003) · Admin API (8000) · three minter workers · Dashboard (Laravel) ·
Explorer · IPFS Kubo + Cluster · PostgreSQL/MySQL · edge proxy.

## How the pieces fit
(ASCII diagram: operator machine → ssh → machines; dentro de una máquina:
edge proxy → dashboard / admin / minter / resolver / store → workers → chain + IPFS;
tabla de componentes: rol, puerto, checkout en components/, repositorio propio.)

## What this repository is
The deployer: one operator inventory (YAML/JSON, format v2/v3) drives
resolve → plan → render → preflight → prepare/push/apply → verify.
Machine-scoped bundles with revisions and service fingerprints (52ce8d5).
Component sources are acquired into components/ (branch pinned in the catalog,
overridable per inventory). Two inventory formats: compact operator inventory
(preferred) and full deployment v3 (legacy, examples/deployment-v3/).

## Quick start
- Requirements (Python 3.14, Docker, ssh).
- `./deploy.sh` — launcher that keeps the venv healthy (self-repair, TUI included);
  equivalently `./create_venv.sh --with-tui`. (Today undocumented — this is the
  recommended entry point.)
- First deployment: local-ha scenario, step by step (inventory-create → edit via
  web-wizard → install → verify → notebook).
- Inspecting without mutating: validate / plan / inventory-explain / metrics.

## Operating a deployment
Lifecycle (deployments/stop/start/restart/recreate/logs), TUI console, read-only
metrics panel, metrics-export → Prometheus (dark-monitoring), chain ops
(chain-init/static-nodes/export/verify), secrets-init, preflight messages.

## Scenarios
Maintained table (13 rows, adding local-ha-monitoring) + link to each .md guide.

## Documentation map
Platform: docs/architecture.md · DARK_2.0_ARCHITECTURE / GUIDE / API_REFERENCE
Deployer: docs/deployer.md · deployment-v3-inventory-and-artifact-flow.md ·
  service-placement.md · networking-v3.md · multi-site-lan-vpn-proposal.md
Runbooks: docs/operations.md · infrastructure/aws/cloudformation/README.md ·
  local-infra/README.md · lima-five-host-test.md · aws-single-az-five-host.md
Dev: docs/development.md · deployer-generalization.md + component-contract-v0.md
Reference: docs/README.md (index) · docs/known-issues.md · docs/old/ (archive)

## Repository layout · Tests · Contributing · License
(Tests: unittest en tests/ + pytest en web-wizard/tests; ruff en requirements-dev.
Contributing: se reescribe aparte.)
```

Cambios de fondo respecto al README actual: apertura explicando **la plataforma** (no solo el deployer), mapa de componentes, `deploy.sh` como entrada recomendada, diagrama de fases, escenarios completos (13), hub documental jerárquico que incluye los DARK_2.0_* y `docs/known-issues.md`.

### 4.3 Documentos nuevos a crear (todos en inglés)

| Doc nuevo | Contenido | Cierra |
|---|---|---|
| `docs/README.md` (índice documental) | Tabla canónica por tema (plataforma / deployer / runbooks / escenarios / diseño activo / archivo), siguiendo el criterio de `docs/old/README-index.md` con precedencia del código. | Sustituye a `history.md`. |
| `docs/monitoring.md` | La integración monitoring **como decisión**: qué se implementó (híbrido B+D: `metrics-export` escribe `.prom` para el textfile collector del stack dark-monitoring; targets desde el snapshot aplicado con `generate_v3_targets.py`), cómo operarlo, qué no se implementó (A: exporter HTTP cacheado; C: remote write). | `metrics-prometheus-proposal.md` (archivada) y `pr-14-review.md`. |
| `docs/deployment-v3-revisions.md` | Referencia viva del flujo real de bundles: `prepare` → `push [--revision]` → `apply [--revision]`, layout `revisions/<id>`, `current`, fingerprints por servicio, `status.json`, qué transfiere push. Desviaciones del plan original anotadas. | `per-machine-bundle-implementation-plan.md` (archivado). |
| `docs/known-issues.md` | Pendientes vivos extraídos de los informes que se archivan (lista completa en §6). | Informes archivados dejan de ser "instrucciones". |
| `CONTRIBUTING.md` reescrito | Suites reales (8 en `tests/` con unittest, web-wizard con pytest), ruff (`requirements-dev.txt`), `deploy.sh`/`create_venv.sh`, flujo de branches de componentes (resumen de `development.md`), convención de commits temáticos. | El vacío actual desde enero. |
| `docs/old/README-index.md` regenerado | Mismo criterio, contenido actualizado a la realidad v3, lista completa de los históricos con motivo y fecha. | La desalineación actual. |

### 4.4 Actualizaciones de la columna vertebral (qué añadir/quitar a cada doc)

- `docs/operations.md`: incorporar ciclo de revisiones (`prepare/push/apply --revision`), `metrics-export`, vía CloudFormation `dark2-prod-aws` como referencia para AWS, plantillas reales, `observers` como fase.
- `docs/deployer.md`: `format_version` [2,3], las 13 plantillas (12 utilizables + la rota), flujo de revisiones, nota sobre la plantilla rota, HTTPS acquisition.
- `docs/architecture.md`: bundles, monitorización, `resolver.instances`/`storage.readers`.
- `deployment-v3-inventory-and-artifact-flow.md`: layout con `revisions/` y `current`, `prepare` en el diagrama.
- `aws-single-az-five-host.md`: posición explícita como alternativa manual a la vía CloudFormation.
- `OPERATIONS-MANUAL.md`: fusionar en `operations.md` y dejar stub en raíz.
- **DARK_2.0_***: corregir el drift listado en §3.1 (prefijo `/api/v1`, rutas de worker, ARKResponse, redirect 302, `?info`, compose dark-ipfs, enlaces rotos) y enlazarlos desde el README. Decisión abierta recomendada: moverlos a `docs/` (p. ej. `docs/platform-architecture.md`, `docs/sdk-guide.md`, `docs/api-reference.md`) para limpiar la raíz; riesgo bajo, repo privado. INFERIDO: si hay enlaces externos a estas rutas, romperían.
- **Cloudformation README + propuesta cost-optimized**: los 4 fixes listados en §3.3 (outputs, contradicción Retain, gitignore/secretos trackeados, validate-template → rain/cfn-lint; alinear DNS A/AAAA con la decisión).
- **`examples/`**: corregir las 2 guías con drift (`aws-active-two-site-nine-host.md`, `production-six-host.md`); añadir nota de "formato legacy" en `examples/deployment-v3/README.md`; documentar `instantiate-inventory.py` en `local-infra/README.md`.
- **Componentes** (una pasada común + toques propios): reemplazar los bloques "Docker Deployment in the Monorepo" por una sección común "Deployed via dark-deployer deployment v3" con enlace al deployer; corregir rutas muertas (`dark-developer`, `components/services|libraries`). Propios: admin (+count/recent, +health/live), minter (PAGE_SIZE 100, formato roto, enlace), lib (+ABI dinámica, +replication status, +count/recent), dashboard-web (reescribir instalación: modo gestionado, prefijo, Grafana, liveness), explorador (quick start v3), dark-dapp (scripts reales, keys reales, borrar copias DARK_2.0_*.md locales), web-wizard (Python 3.14, 46 operaciones).
- `dark-monitoring` README: matiz del `.gitignore` (menor, opcional).

### 4.5 Fixes baratos de código que la documentación pide a gritos

Estos son defectos de código detectados durante la auditoría; corregirlos cuesta menos que documentarlos:

1. Plantilla rota: `inventory_editor/templates.py:23` apunta a `examples/operator-inventory/aws-active-six-host.json` que no existe (decidir: añadir el ejemplo o quitar la plantilla).
2. `.gitignore`: quitar la línea del `active-six-host.parameters.yaml` inexistente y añadir `dark2-prod-aws.parameters.yaml` (hoy trackeado con account ID, ARN ACM y hosted zone, contra su propia cabecera "Do not commit").
3. Tabla "Maintained scenarios" del README: añadir `local-ha-monitoring`.
4. README cloudformation: outputs `AppsHttpUrl/...` (4 líneas).
5. INFERIDO: sustituir `aws cloudformation validate-template` por `rain fmt`/`cfn-lint` en el runbook (los tipos SSM probablemente lo rechazan).

---

## 5. Plan de ejecución por fases

Orden por relación valor/esfuerzo; cada fase es un commit independiente:

1. **Fase A — README + hub (el corazón).** README reescrito según §4.2, `docs/README.md` índice, `history.md` archivado, `CONTRIBUTING.md` reescrito, `deploy.sh` documentado. (~1 sesión)
2. **Fase B — columna vertebral al día.** `operations.md` (revisiones + metrics-export + CloudFormation), `deployer.md`, `architecture.md`, `deployment-v3-inventory-and-artifact-flow.md`; OPERATIONS-MANUAL → stub; docs nuevos `monitoring.md` y `deployment-v3-revisions.md`; `known-issues.md`. (~1-2 sesiones)
3. **Fase C — archivado.** Mover los 10 docs señalados a `docs/old/`, regenerar `docs/old/README-index.md`, actualizar enlaces desde el README/índice. (~media sesión)
4. **Fase D — platform docs.** Corregir los 3 DARK_2.0_* (API_REFERENCE primero); decidir ubicación (raíz vs `docs/`). (~media sesión)
5. **Fase E — componentes.** Sección común de despliegue v3 + correcciones propias por componente (admin, minter, lib, dashboard, explorador, dapp, web-wizard, monitoring minor). (~1 sesión)
6. **Fase F — AWS/examples/local-infra + fixes de código.** Los 4 fixes del README cloudformation, contradictorios de la propuesta, 2 guías de examples, `instantiate-inventory.py` documentado, fixes de código de §4.5. (~media sesión)
7. **Verificación transversal.** Re-grep de comandos citados vs `cli.py`, de rutas citadas vs filesystem, y de los endpoints citados vs routers; relanzar `resolve_inventory`+`build_plan` sobre los 12 ejemplos; dejar `docs/known-issues.md` como único sitio donde viven los pendientes.

---

## 6. Pendientes vivos que no deben perderse al archivar

Estos viven hoy en informes fechados; pasan a `docs/known-issues.md`:

1. **Canario Kubo sin promover**: el catálogo sigue anclado a `ipfs/kubo:master-2026-08-17-a73e8c0@sha256:c11759f...` (`catalog_data/dark-platform-baseline-v1.0.json:349`); la promoción a release estable con el arreglo de Bitswap (boxo PR #1201) sigue pendiente.
2. **`pin_error: context canceled` sin dueño**: `components/dark-store-api/app/backends/ipfs_cluster.py:64` mantiene `timeout: float = 30.0` sin override por env sobre la respuesta en streaming de `POST /pins/{cid}` de Cluster.
3. **`verify` no comprueba `target_replicas` ni avance de cadena** (hallazgo 3.2 de la revisión AWS, sigue abierto).
4. **Aceptación prolongada** de Cluster/Store sobre un flujo de aplicación real (criterios 4/5 solo vía probe manual; notebook sin cobertura de bitswap).
5. **`smoke-test.sh` de dark-ipfs no puede detectar el fallo de replicación por construcción** (no hace lectura remota).
6. **Rutas absolutas del operador en `examples/deployment-v3/production-six-host.json:290-344`** y test de ejemplos con tupla literal + IPs fijas (`tests/test_deployment_v3.py:460-462`, `:116-119`).
7. **Registros AAAA**: decisión dice A/AAAA, plantilla crea solo A.
8. **Aceptación física dos-sedes VPN** (`multi-site-lan-vpn-proposal.md`): el formato v3 está implementado y probado offline; la prueba física dos sedes sigue pendiente.
9. **`dashboard-redis`** sobrevive como tipo legado que aún se planifica y valida (`planner.py:20`, `inventory.py:33`); `runner.py:892` lo describe como "Redis heredado; retirar durante la migración" pero no lo rechaza — retirar el tipo durante la migración.

---

## 7. Resumen ejecutivo

- El README raíz está **técnicamente sano pero narrativamente vacío para un recién llegado** y no menciona la vía de arranque real (`deploy.sh`); la propuesta lo reescribe como narrativa completa de la plataforma + deployer con cheatsheet.
- El mayor bloque de obsolescencia no está en `docs/` sino en **los READMEs de los componentes** (admin, dashboard, explorador, dapp) y en las secciones de despliegue con compose que se borraron hace dos semanas.
- Hay **10 documentos archivables** (propuestas implementadas, informes cerrados, revisiones superadas) y **2 decisiones ya tomadas que no están registradas como tales** (híbrido B+D de monitorización; bundles implementados con desviaciones).
- El drift puro y duro se concentra en: DARK_2.0_API_REFERENCE (prefijo `/api/v1` y endpoints), CloudFormation README (outputs + contradicción Retain + secretos trackeados), OPERATIONS-MANUAL (plantillas/fases/Redis) y `deployer.md` (revisiones).
- La renovación cabe en **~5-6 sesiones** con el plan de §5, empezando por el README y el índice.
