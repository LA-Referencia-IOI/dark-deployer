# Plan de implementación: bundles autocontenidos por máquina

## 1. Objetivo y contrato

El controlador seguirá adquiriendo y verificando todos los componentes declarados
por el deployment. La distribución cambia: cada host recibe un bundle inmutable
con solo las fuentes, assets y configuración que requieren sus servicios.

«Autocontenido» significa que el host no necesita fuentes ni configuración del
repositorio del controlador. No incluye secretos ni datos persistentes; las
únicas rutas externas admitidas por Compose son `data_root` y
`secrets_root`. Las imágenes y dependencias de build pueden requerir sus
registries habituales.

Los estados son distintos:

| Estado | Significado |
| --- | --- |
| preparado | revisión local completa, validada y con identidad |
| transferido | bundle de una máquina íntegro en su host |
| aplicado | un servicio usa la revisión objetivo registrada |

Cada archivo tiene una sola responsabilidad:

| Archivo | Pregunta que responde | Autoridad |
| --- | --- | --- |
| `deployment-topology.json` | ¿Qué servicios, conexiones y placement se desean? | única autoridad declarativa |
| `source-evidence.json` | ¿De dónde proceden las fuentes y qué contenido se preparó? | única evidencia global de fuentes |
| `revision.json` | ¿Qué bundle de máquina compone esta revisión? | índice de la revisión preparada |
| `manifest.json` de máquina | ¿Qué archivos exactos contiene ese bundle? | integridad del bundle |
| `status.json` | ¿Qué revisión, bundle y servicios están aplicados? | estado operativo actual |
| `journal.jsonl` | ¿Qué pasos se intentaron y cuál fue su resultado? | historial, no estado actual |

No se repiten commits, repositorios o branches en manifiestos de máquina ni en
`status.json`. `source-evidence.json` registra por componente origen, branch,
commit, estado dirty y hash del árbol filtrado. Ese hash identifica los bytes
reales cuando existen cambios locales sin commit.

## 2. Revisión global, bundle de máquina y layout

`prepare` crea una revisión local en
`.generated/deployment-v3/<deployment-id>/revisions/<revision-id>/`. Su
`revision-id` identifica el snapshot completo de topología y fuentes. Cada
máquina tiene además un `machine-bundle-id`, calculado sobre su manifiesto;
una revisión nueva puede reutilizar bundles de máquinas no afectadas.

`revision.json` es un índice pequeño: contiene versión de formato,
`deployment-id`, hash de `deployment-topology.json`, hash de
`source-evidence.json` y el mapa `machine-id -> machine-bundle-id`. El
`revision-id` es el SHA-256 de ese índice canónico, excluyendo el propio ID.
Imágenes y configuración permanecen en la topología; fuentes en la evidencia;
artefactos y runtime en los manifiestos de máquina. No se copian en el índice.

Un inventario igual no basta para reutilizar una revisión: cambios en fuentes,
assets, imágenes, artefactos o generador crean otra identidad.

La revisión local conserva la topología y evidencia completas. El bundle
remoto contiene solo `machine.json` filtrado, Compose, env, config, artefactos,
fuentes y runtime de esa máquina. No se copian bundles de otras máquinas ni la
evidencia global.

```text
# controlador: estado persistente del deployment
.generated/deployment-v3/<deployment-id>/
├── status.json
├── journal.jsonl
├── revisions/<revision-id>/
│   ├── revision.json
│   ├── deployment-topology.json
│   └── source-evidence.json
└── machine-bundles/<machine-id>/<machine-bundle-id>/
    ├── manifest.json
    ├── machine.json
    ├── compose.yaml, env/, config/, groups/
    ├── sources/components/<component>/
    ├── runtime/
    └── artifacts/contracts/

# host <machine-id>
machines/<machine-id>/
├── bundles/<machine-bundle-id>/
└── current -> bundles/<machine-bundle-id>
```

`machine.json`, los env y config contienen únicamente componentes,
servicios, referencias a secretos y conexiones usados por esa máquina. La
topología y evidencia globales se quedan en el controlador. De este modo,
cambiar un componente o secreto no relacionado no invalida su máquina.

Cada `manifest.json` de máquina es canónico e incluye versión de formato,
deployment, máquina y archivos con SHA-256, modo y tipo
(`file`, `directory` o `symlink`). Los enlaces permitidos llevan destino
relativo; se rechazan enlaces rotos, destinos fuera del bundle, archivos
especiales y rutas no declaradas. `revision.json` guarda el digest esperado de
cada manifiesto; un manifiesto no se certifica a sí mismo.

`machine-bundle-id` cubre solo el bundle de esa máquina. `revision-id` no se
usa para decidir su transferencia.

## 3. Dependencias y materialización

Crear `deployment_v3/staging_manifest.py` con
`SERVICE_BUILD_COMPONENTS`, `SERVICE_RUNTIME_COMPONENTS`,
`COMPONENT_DEPENDENCIES`, `SERVICE_RUNTIME_ASSETS` y funciones puras para
resolver requisitos, validar y materializar bundles. La tabla queda en Python
en esta migración, para no cambiar todavía el digest del catálogo.

| Tipo de servicio | build | runtime | assets externos |
| --- | --- | --- | --- |
| besu-rpc, besu-validator, besu-observer | — | — | `blockchain/scripts/besu-entrypoint.sh`, `blockchain/config/besu-config.toml` |
| ipfs-kubo | — | dark-ipfs | — |
| ipfs-cluster | — | dark-ipfs | — |
| minter-api, minter-worker, minter-migrate | dark-core-minter-api, dark-core-lib | — | — |
| admin-api | dark-core-admin-api, dark-core-lib | — | — |
| resolver-api | dark-core-resolver-api, dark-core-lib | — | — |
| store-api | dark-store-api | — | — |
| dashboard, dashboard-migrate | — | dashboard-web | — |
| explorer | dark-explorador | — | — |
| contracts-deploy, rpc-probe | — | — | `deployment_v3/contracts.py`, `deployment_v3/Dockerfile.contracts` |
| minter-postgres, dashboard-mysql, edge-proxy | — | — | — |

`dark-dapp` se usa solo en el controlador para compilar contratos.
`dark-monitoring` queda fuera de los bundles V3 hasta disponer de una
asignación explícita. Su instalación remota requerirá transferencia explícita
o ejecución remota desde el controlador. `dashboard-redis` debe eliminarse
como tipo legado o rechazarse explícitamente antes de habilitar la tabla.

Se falla con servicio y componente responsables si falta una declaración,
checkout, ruta registrada, dependencia transitiva o asset requerido, o si hay
ciclos. Explorer exige `dist/`.

La materialización parte de una raíz vacía, preserva modos y aplica el mismo
filtro en todos los transportes:
`.git`, `.env`, `.env.integration`, `.generated`, `venv`, `.venv`,
`node_modules`, `__pycache__` y `.pytest_cache`. Los componentes
seleccionados se copian completos tras ese filtro; los assets externos se
copian por archivo. Los hashes se calculan sobre la copia materializada.

## 4. Compose, prepare y comandos

El renderer recibe un layout para obtener rutas relativas:

| Compose | prefijo de fuentes y runtime |
| --- | --- |
| `machines/<id>/compose.yaml` | `./sources/...`, `./runtime/...` |
| `machines/<id>/groups/<group>/compose.yaml` | `../../sources/...`, `../../runtime/...` |

Los builds usan `<prefijo>sources/components`; Explorer usa su
subdirectorio; Besu monta `<prefijo>runtime/blockchain`; IPFS monta sus
scripts; Dashboard monta `dashboard-web`; contracts y RPC probe usan
`context: <prefijo>runtime` y
`dockerfile: deployment_v3/Dockerfile.contracts`. `workspace_root`
permanece para preflight y rutas de bundles, pero no aparece en Compose.

Añadir el comando `prepare`. Bajo el lock del deployment:

1. valida dependencias, topología y fuentes;
2. materializa las entradas filtradas en una raíz temporal;
3. compila contratos desde esa copia;
4. renderiza Compose, configuración y artefactos;
5. escribe manifiestos, verifica los bundles y publica atómicamente la
   revisión local.

El caché de contratos se indexa por hashes de los Solidity materializados,
`Dockerfile.solc-js`, `compile_contracts.js`, versión de solc y sus
opciones. Los artefactos recuperados siempre se verifican.

`push --revision ID` transfiere y verifica los bundles de una revisión
preparada; sin una revisión válida falla y pide `prepare`. No requiere Docker.

`apply` prepara la revisión deseada y compara los fingerprints de servicio con
`status.json`; solo aplica altas, cambios y bajas. `apply --revision ID` aplica
exactamente una revisión preparada tras verificar su integridad, deployment y
versión de esquema; no consulta fuentes actuales ni la regenera.

`resume` toma la revisión objetivo de la ejecución interrumpida y no consulta
checkouts ni llama a `source_evidence()`. Los fingerprints de servicios salen
del bundle: sección propia de Compose, env, config, artefactos y archivos
build/runtime que usa
cada servicio.

## 5. Transferencia, activación y recuperación

Antes de aplicar servicios, se transfieren y verifican solo los bundles de
máquina que han cambiado. Cada host recibe el bundle en
`bundles/<machine-bundle-id>.tmp`, se comprueba íntegramente frente al
manifiesto y solo entonces se renombra a
`bundles/<machine-bundle-id>`. Un fallo de transferencia no modifica ninguna
referencia activa. Una revisión nueva puede reutilizar un bundle remoto ya
verificado.

Durante una aplicación, Compose usa directamente la ruta inmutable del
bundle objetivo. `current` solo se cambia, de forma atómica, cuando todos los
servicios afectados de esa máquina alcanzan los pasos previstos para la
revisión.
Los nombres de proyecto Compose permanecen estables.

El controlador conserva por deployment:

- revisión objetivo y última revisión completamente aplicada;
- bundle y estado por máquina;
- progreso por paso y servicio;
- resultado incierto de acciones no idempotentes.

`status.json` registra `target_revision`, `last_applied_revision`, el bundle
por máquina y el fingerprint por servicio. `managed_plan()` carga la topología
desde
`revisions/<last_applied_revision>/deployment-topology.json`. Durante una
aplicación parcial se permiten consultas y logs, pero se bloquean operaciones
mutantes ordinarias; `resume` usa `target_revision` y el progreso registrado.

La activación multihost no es atómica. Si falla un host o servicio, la
aplicación se detiene y el estado parcial queda registrado. `resume`
verifica el estado remoto y continúa desde el primer paso no confirmado; no
repite a ciegas migraciones, contratos ni otros jobs de una sola ejecución.
No hay rollback automático, porque restaurar Compose no revierte datos,
migraciones ni transacciones blockchain.

Las revisiones y bundles anteriores se conservan durante esta operación. Un
rollback futuro será una operación
explícita y limitada a configuración y contenedores, con advertencia de que no
revierte estado persistente.

Una máquina se transfiere solo si su `machine-bundle-id` no está ya verificado
en el host. Dentro de la máquina, solo se ejecutan servicios cuyo fingerprint
cambió; agregar un servicio no reinicia los demás miembros del grupo.

## 6. Entregas y pruebas

### PR 1: declaración y manifiestos

Añadir dependencias declaradas, validaciones, manifiestos descriptivos y
pruebas de clausura transitiva, sin cambiar el staging.

### PR 2: preparación y bundle autocontenido

Añadir revisiones locales, materialización selectiva, Compose relativo,
identidades de revisión y bundle, y comandos `prepare`/`push`.
El staging global puede existir solo como diagnóstico y no como ruta de
ejecución.

### PR 3: apply, lifecycle y resume por revisión

Transferir bundles, verificarlos, ejecutar desde bundles inmutables,
registrar progreso parcial y retirar el staging global. Cambiar `current`
solo al completar la máquina.

### PR 4: incremental, retención y rollback explícito

Omitir transferencias sin cambios, configurar retención y añadir rollback
limitado. Evaluar `tar.zst` frente a rsync con mediciones reales.

Las pruebas cubren todos los inventarios de `examples/` y verifican:

- selección exacta por máquina y no invalidación por cambios ajenos;
- Compose agregado y por grupo sin `workspace_root`;
- `docker compose config` y builds de APIs, Explorer y contracts;
- `dist/`, `Dockerfile.contracts` y `contracts.py` en sus bundles;
- cambios locales sin commit y uso exclusivo de la revisión por `resume`;
- permisos, enlaces, archivos sobrantes, manifiestos manipulados y
  transferencias truncadas;
- dos deployments en un host sin colisiones;
- fallo parcial entre hosts, persistencia de estado y reanudación segura;
- ausencia de repetición de migraciones y contratos ya confirmados.

La migración se acepta al cerrar PR 3 cuando una máquina blockchain no reciba
apps ni monitoring, una máquina storage no reciba APIs ajenas, y cualquier
servicio pueda construirse o recrearse desde su bundle aplicado.

## 7. Archivos afectados

| Archivo | Cambio |
| --- | --- |
| `deployment_v3/staging_manifest.py` | dependencias, manifiestos y materialización |
| `deployment_v3/render.py` | bundle filtrado y layout de Compose |
| `deployment_v3/runner.py` | revisiones, preparación, aplicación, lifecycle y resume |
| `deployment_v3/services.py` | rutas relativas |
| `deployment_v3/executor.py` | transferencia y verificación de bundle |
| `deployment_v3/cli.py` | `prepare` y `--revision` |
| `deployment_v3/state.py` | `status.json` y journal existentes |
| `deployment_v3/sources.py` | evidencia de fuentes materializadas |
| `tests/test_deployment_v3.py` | pruebas unitarias e integración |
| `docs/` | operación, recuperación y troubleshooting |
