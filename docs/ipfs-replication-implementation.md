# Implementación de persistencia y reconciliación IPFS

## Propósito

Esta es la referencia de la implementación actual para Store API, Minter, Resolver y deployer.

La topología continúa siendo un IPFS Cluster global con dos nodos de almacenamiento por sede.

## Decisión

| Decisión | Regla |
| --- | --- |
| Aceptar escritura | Store API observa un peer `PINNED`. |
| Habilitar minting | PostgreSQL contiene los CIDs L2 y L1. |
| Purgar payload | L1 y L2 cumplen el objetivo derivado de topología. |

La separación evita bloquear minting por la propagación completa y conserva una fuente de reparación en PostgreSQL.

## Flujo por ARK

```text
PostgreSQL guarda L1 y L2.
Metadata Worker almacena L2 y confirma un pin.
Metadata Worker crea L1 con el CID L2, lo almacena y confirma un pin.
Guarda ambos CIDs con conteos iniciales de una copia y retiene los payloads.
Chain Worker publica L1 en blockchain.
Metadata Worker reconcilia ambos CIDs en segundo plano.
Purge ocurre solo cuando L1 y L2 alcanzan el objetivo.
```

L2 → L1 es obligatorio dentro de un ARK. Los ARKs distintos usan `METADATA_WORKER_CONCURRENCY`, por defecto 4.

## Política automática

Store API recibe el mapa global de peers y `IPFS_CLUSTER_LOCAL_SITE_ID` desde el deployer.

| Topología | Objetivo para purgar cada CID |
| --- | --- |
| Developer, un peer | 1 copia local |
| Una sede, dos peers | 2 copias locales |
| Dos o más sedes | 2 copias locales y 1 remota |

Solo los peers `PINNED` cuentan. `PINNING`, `PIN_ERROR`, asignaciones y peers visibles no son copias durables.

## Store API

Store API mantiene un `httpx.AsyncClient` y pools separados para Kubo, Cluster REST y Cluster Proxy.

- Cada pool usa round-robin.
- Timeout, conexión rechazada, `429` y `5xx` generan 30 segundos de cooldown.
- El endpoint vuelve automáticamente después del cooldown.
- El polling de pin empieza en 100 ms y crece hasta 2 s.
- El primer `PINNED` confirma la escritura; el objetivo de purga no bloquea.

`POST /v1/store` y `GET /v1/status/{cid}` devuelven conteos total, local, remoto y por sede, junto con `purge_target_met` y `checked_at`.

## Estado mínimo en PostgreSQL

No hay tabla de réplicas, historial, objetivos, reintentos ni distribución por sede.

`ark_metadata` incorpora solamente `level1_replica_count`, `level2_replica_count`, `replication_checked_at` y `replication_last_error`.

| Estado derivado | Criterio |
| --- | --- |
| `complete` | Los payloads L1 y L2 fueron purgados. |
| `error` | Hay error y payload retenido. |
| `degraded` | Algún conteo es cero y payload retenido. |
| `pending` | Payload retenido, sin error y conteos positivos. |

`worker_runtime_status` solo conserva el resumen del último ciclo.

## Reconciliación y reparación

El worker reconcilia inmediatamente cuando no hay trabajo nuevo y cada cinco minutos bajo carga continua.

Los lotes tienen como máximo 50 ARKs y usan la misma concurrencia del worker.

Para cada ARK retenido, empezando por la comprobación más antigua:

1. consulta L2 y L1 en Store API;
2. actualiza conteos, fecha y error;
3. reconstruye desde PostgreSQL cualquier CID con cero copias;
4. rechaza una reparación con CID diferente;
5. bloquea y verifica de nuevo la fila antes de purgar;
6. purga ambos payloads solo si ambos CIDs tienen `purge_target_met=true`.

Ante cualquier fallo se conserva el payload y se registra el error.

## APIs operativas

`GET /api/v1/worker/status` incluye el resumen de réplicas y del último ciclo de reconciliación.

`GET /api/v1/worker/replication` lista CIDs L1/L2, conteos, fecha, error y retención.

La distribución actual por sede se consulta con `GET /v1/status/{cid}`. El dashboard no cambió en esta entrega.

## Configuración y compatibilidad

La única nueva configuración del worker es `METADATA_WORKER_CONCURRENCY=4`.

El deployer genera `IPFS_CLUSTER_LOCAL_SITE_ID`.

Se retiraron `IPFS_ADD_MODE`, `IPFS_CLUSTER_EXPECTED_PEERS`, `IPFS_CLUSTER_WRITE_MIN_PEERS`, `IPFS_CLUSTER_WRITE_MIN_SITES`, `IPFS_REPLICATION_CONFIRM_INTERVAL_SECONDS` y `strict_single_site`.

Para facilitar upgrades, una topología legacy con `strict_single_site=false` se
acepta y el campo se ignora. `strict_single_site=true` se rechaza porque su
garantía no puede conservarse con confirmación de un pin. El cambio asume una
base PostgreSQL nueva y no incluye backfill de datos.

## Cierre y verificación

`dark-core-lib` mantiene un `httpx.Client` persistente. Minter API, Metadata Worker y Resolver API llaman a `close()` al apagarse.

La cobertura automática valida round-robin, cooldown, reincorporación, conteo de `PINNED`, política de purga, retención, reparación y programación ociosa.

El registro E2E normal ya fue validado con IPFS real en el ambiente local, sin
interrumpir peers. Las mediciones de throughput y la validación de una purga
multisede permanecen como actividades del rollout operativo y no forman parte
del test simple de registro.

## Documentos relacionados

- [Arquitectura IPFS global](ipfs-architecture.md)
- [Propuesta de rendimiento](ipfs-metadata-worker-throughput-proposal.md)
- [Instalación de producción](production-single-site-four-server-installation.md)
