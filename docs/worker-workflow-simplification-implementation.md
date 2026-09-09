# Implementación de la simplificación del workflow del minter

**Fecha:** 2026-09-09
**Estado:** implementado y verificado en el entorno developer actual.

## Alcance

Se simplificó el procesamiento interno de ARKs sin cambiar los estados
públicos (`R`, `D`, `U`, `P`, `T`). Los cambios afectan al minter API, sus
workers, el esquema inicial, el dashboard, el instalador y la documentación.

No se mantiene compatibilidad con el esquema interno anterior. La base debe
crearse nuevamente usando la migración inicial consolidada.

## Arquitectura resultante

El minter queda compuesto por:

- API HTTP.
- `metadata-worker`: persiste Level 1 y Level 2 y guarda sus CIDs.
- `replication-worker`: observa la disponibilidad y replicación en IPFS
  Cluster, avanza el ciclo y purga payloads cuando corresponde.
- `chain-worker`: publica o actualiza el ARK en blockchain.

Se eliminó el `recovery-worker`. No existe una cola automática secundaria que
vuelva a introducir registros en los workers normales.

## Actualización de observabilidad y replicación

La implementación vigente distingue los estados que devuelve IPFS Cluster:
`pinned` (réplica confirmada), `pin_queued`, `pinning`, `pin_error`,
`unpinned` y `unknown`. Las asignaciones en cola o en pinning son esperas
normales y no provocan reparaciones. Store API expone esos conteos y ofrece
`POST /v1/status/batch`, que el replication worker usa para consultar L1 y L2
en una sola ronda acotada.

La disponibilidad y la durabilidad tienen cadencias independientes y explícitas:
primer pin a `15s → 1min → 5min`, y durabilidad a `5min → 15min → 1h`.
Las reparaciones requieren gracia y cooldown, y solo se ejecutan ante
ausencia confirmada o error de Cluster. El heartbeat persiste `next_wake_at`,
los ciclos informan observados/avanzados/en espera/reparados/fallidos y el
status completo marca posibles ciclos sin progreso. Store API también invalida
la salud cuando varios Cluster peers anuncian la misma identidad Kubo.

## Estados internos

Los estados internos ahora son:

| Código | Estado | Significado |
|---:|---|---|
| 1 | `READY` | El worker propietario puede intentar el registro. |
| 2 | `WAITING` | La operación está esperando una acción normal programada. |
| 3 | `FAILED` | Fallo permanente que requiere revisión administrativa. |
| 4 | `DONE` | La etapa terminó correctamente. |
| 5 | `CANCELLED` | El registro fue cancelado por tombstone. |

Las etapas siguen siendo `NONE`, `METADATA`, `AVAILABILITY`, `CHAIN`,
`REPLICATION` y `COMPLETE`.

`WAITING` se explica mediante dos campos nuevos:

- `next_action_at`: momento más temprano en que debe volver a observarse o
  intentarse el registro.
- `processing_wait_reason`: catálogo numérico con motivos como
  `cluster_pinning`, `initial_visibility`, `replica_target`,
  `storage_backoff`, `rpc_backoff` y `chain_confirmation`.

`REPLICATION_IDLE_SLEEP_SECONDS` solo despierta el proceso para detectar nuevas
entradas. No autoriza una consulta remota: cada ARK debe haber alcanzado su
`next_action_at`. La durabilidad limita cada ronda a 50 ARKs/100 CIDs y no vuelve
a enviar una promoción cuando Cluster ya tiene la asignación objetivo.

Los catálogos de etapas, estados, motivos de espera y errores son tablas SQL
sembradas por `0001_initial_schema.py`. La aplicación utiliza los códigos
compactos y las APIs los convierten a etiquetas legibles.

## Transiciones de los workers

### Metadata

Selecciona únicamente registros `METADATA/READY` o esperas vencidas. Cuando
guarda ambos CIDs, avanza a `AVAILABILITY/READY` y limpia la programación
anterior.

Los fallos temporales se registran como `WAITING` con `storage_backoff`. Los
fallos permanentes se registran como `FAILED`.

### Replication

Selecciona registros con ambos CIDs y una acción lista o vencida.

- Si Cluster todavía está haciendo pinning, permanece en `WAITING` con
  `cluster_pinning`.
- Si aún no alcanza el mínimo para publicar, usa `initial_visibility` o
  `replica_target` según el caso.
- Cuando alcanza el objetivo de publicación, avanza a `CHAIN/READY`.
- Cuando un ARK publicado alcanza el objetivo de retención, purga el payload y
  marca `COMPLETE/DONE`.

La reparación mantiene la comprobación con lock y comparación de CIDs antes
de modificar réplicas o purgar datos.

### Chain

Selecciona únicamente `CHAIN/READY` o esperas vencidas. Los fallos transitorios
se reprograman en la misma etapa. Los fallos permanentes pasan a `FAILED` y no
son reintentados automáticamente.

## Reintento administrativo

El endpoint es:

```text
POST /api/v1/worker/retry
```

Está protegido por el mismo mTLS administrativo. Recibe una lista explícita de
ARKs y una razón. Solo permite `FAILED -> READY` en la etapa actual, reinicia
el contador de intentos y conserva intactos el estado público, autoridad,
target, metadata y CIDs.

No se publica directamente en blockchain y no existe endpoint de rescate
automático.

## API de status

### Status simple

```text
GET /api/v1/worker/status
```

Realiza una única consulta a `worker_runtime_status`. No consulta RPC, Store
API, IPFS, blockchain ni `ark_records`.

Siempre devuelve `metadata`, `replication` y `chain`, incluyendo:

- `alive` y `enabled`;
- `process_state`: `RUNNING`, `SLEEPING`, `PAUSED`, `DOWN` o `DISABLED`;
- antigüedad del heartbeat;
- último ciclo;
- error o motivo de pausa;
- `wake_at` y `wake_in_seconds` cuando está durmiendo.

### Diagnóstico completo

```text
GET /api/v1/worker/status?detail=full
```

Agrega consultas SQL acotadas y probes externos una vez por respuesta. Incluye:

- carga `ready`, `waiting`, `failed` por worker;
- próxima acción programada;
- motivos de espera agrupados;
- errores permanentes agrupados por etapa y código;
- RPC y storage;
- último ciclo y heartbeat.

Un registro `WAITING` vencido se contabiliza como `ready`, porque ya puede ser
tomado por el worker. Un registro con fecha futura permanece en `waiting`.

### Errores

```text
GET /api/v1/worker/errors
```

Devuelve únicamente `FAILED`, con resumen SQL y paginación. Permite filtrar por
etapa, autoridad y código estructurado. Las esperas normales de IPFS o RPC no
aparecen como errores.

## Dashboard

La web ahora:

- usa status simple para indicadores de portada;
- usa status full solo en la pantalla Workers;
- conserva cache de 30 segundos para diagnóstico full;
- muestra proceso y carga en filas distintas;
- explica cuánto falta para la próxima acción;
- muestra los tres workers reales;
- separa la pantalla de fallos permanentes;
- no muestra recovery worker ni ofrece botones de reintento.

El test E2E del dashboard valida únicamente metadata, replication y chain.

## Configuración y despliegue

Se eliminaron las variables del recovery worker. El instalador ahora genera y
propaga únicamente la configuración de metadata, replication y chain.

También se renombró la configuración de crecimiento adaptativo del chain worker
a `CHAIN_WORKER_HEALTHY_CYCLES_BEFORE_GROWING`.

Los servicios Docker esperados son:

```text
minter-api
minter-metadata-worker
minter-replication-worker
minter-chain-worker
```

No debe existir `minter-recovery-worker`.

## Base de datos nueva

La migración `alembic/versions/0001_initial_schema.py` es ahora la fuente
inicial completa. Incluye:

- estados internos simplificados;
- `next_action_at`;
- `processing_wait_reason`;
- catálogo de motivos de espera;
- índices de selección por etapa, estado y próxima acción;
- catálogos de errores con política de permanencia.

No se debe ejecutar una migración incremental sobre la base antigua. El entorno
debe recrearse y luego aplicar Alembic desde cero.

## Archivos principales modificados

- `components/services/dark-core-minter-api/app/models/processing.py`
- `components/services/dark-core-minter-api/app/database/models.py`
- `components/services/dark-core-minter-api/app/repositories/ark_repository.py`
- `components/services/dark-core-minter-api/app/workers/publisher.py`
- `components/services/dark-core-minter-api/app/main_worker.py`
- `components/services/dark-core-minter-api/app/api/worker.py`
- `components/services/dark-core-minter-api/alembic/versions/0001_initial_schema.py`
- `compose/components/services/minter.yml` (owned by the deployer)
- `components/services/dark-core-minter-api/docker-entrypoint.sh`
- `components/frontend/dashboard-web/app/Http/Controllers/Dashboard/WorkerController.php`
- `components/frontend/dashboard-web/resources/views/dashboard/workers/index.blade.php`
- `components/frontend/dashboard-web/resources/views/dashboard/workers/errors.blade.php`
- `install.py` y `.env.example`

## Verificación pendiente

La recreación, migración, arranque de servicios y pruebas no fueron ejecutadas
como parte de esta implementación. El siguiente agente debe seguir
[el procedimiento de verificación](worker-workflow-simplification-verification.md),
usando Python 3.12 y una base nueva.
