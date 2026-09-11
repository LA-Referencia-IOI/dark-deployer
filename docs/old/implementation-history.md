# Historial de consolidación de la plataforma

Este archivo es el registro histórico del repositorio padre. No es una guía
operativa. Para instrucciones actuales consulte [`README.md`](README.md),
[`deployer-operations.md`](deployer-operations.md),
[`production-single-site-four-server-installation.md`](production-single-site-four-server-installation.md)
y [`ipfs-architecture.md`](ipfs-architecture.md).

Los documentos de propuesta, diagnóstico y verificación fechados se conservan
como evidencia de decisiones y pruebas. Si contienen nombres antiguos como
`dark-env`, archivos separados de topología de storage, Recovery Worker o cuotas históricas,
deben interpretarse dentro de su fecha y no como configuración vigente.

Este documento resume los cambios acumulados durante la actualización de la plataforma dARK y sirve como guía para leer los commits temáticos de los componentes.

## Deployer y topología

El repositorio padre concentra la configuración por ramas, la generación de entornos y el inventario `deployment-topology.json`. Ese inventario es la fuente de verdad para hosts, peers IPFS/Cluster y réplicas; los entornos conservan únicamente el perfil, el selector local y los secretos. El instalador valida selectores, peers, direcciones y el mínimo de réplicas, genera `.env.integration` y aplica las ramas configuradas con fallback controlado a `main`.

La documentación consolidada cubre operación del instalador, despliegue de producción, arquitectura IPFS, resolución de timeouts y el contrato de configuración. Los artefactos de ejecución locales, notebooks generados y directorios de build no forman parte de estos commits.

## Minter API

La arquitectura vigente del minter tiene API, metadata worker, replication
worker y chain worker. Los workers comparten heartbeats y locks advisory por
ARK en PostgreSQL. El recovery worker automático fue retirado.

El status simple consulta únicamente `worker_runtime_status`. El status full
agrega diagnósticos operativos acotados. Las esperas temporales se expresan como
`WAITING`; solo `FAILED` requiere revisión y un reintento administrativo mTLS.

## Store API y core library

Store API deriva sus endpoints locales de la topología, usa pools de peers y reporta la aceptación inicial y el estado de las réplicas. La biblioteca compartida conserva el contrato de almacenamiento y limita consultas costosas, con pruebas para respuestas incompletas y disponibilidad parcial.

## Dashboard

El dashboard consume el status simple en la portada y el status full solo en la pantalla de Workers. El diagnóstico full se cachea durante 30 segundos con lock para evitar consultas concurrentes. La pantalla dedicada de errores muestra estados, etapas, códigos, filtros, CIDs y paginación, pero no ejecuta recovery administrativo.

## Verificación

Antes de publicar cada commit se ejecutan las suites focalizadas del componente correspondiente y `git diff --check`. La ausencia de PHP en el entorno local impide ejecutar `php -l`; el código Laravel queda pendiente de validación en CI o en el contenedor de dashboard.

## Cambios posteriores de rendimiento y diagnóstico

Se añadió programación explícita de Availability mediante `next_action_at`,
backoff con jitter para estados normales de Cluster, batches de status
deduplicados, límites de concurrencia en Store API y prioridad del primer pin
sobre la durabilidad. El dashboard y el monitor distinguen ahora trabajo listo,
espera programada, motivo de espera y mantenimiento bloqueado por backlog; no
tratan `queued` o `pinning` como errores.

La reevaluación del 2026-09-08 concluyó que el cuello residual está en Chain y
en la estabilidad de los validadores Besu (reinicios `Killed`, CPU elevada y
cadencia QBFT irregular), no en el polling de IPFS. La página de Chain sigue
limitada a 20 hasta estabilizar la red; cualquier aumento debe hacerse después
de una prueba sostenida y gradual.

## Commits temáticos

| Componente | Commit | Contenido |
| --- | --- | --- |
| repositorio padre | `955ba09` | Instalador, topología, documentación operativa y hallazgos de timeout |
| dashboard-web | `dfebd1e` | Status lite/full, cache de diagnóstico y revisión de errores |
| dark-core-lib | `901b5c4` | Cliente Store API y pruebas de disponibilidad/replicación |
| dark-core-minter-api | `e1c3935` | Workers, locks PostgreSQL, recovery, errores y status |
| dark-store-api | `5d7d4c5` | Topología centralizada, pools y estado de réplicas |
| dark-ipfs | `5f6a3eb` | Allocator por capacidad y configuración IPFS sin etiquetas obligatorias |

Los commits se hicieron sobre `main` y agrupan los cambios pendientes por componente. Los componentes sin cambios pendientes ya estaban limpios y no recibieron commits vacíos.
