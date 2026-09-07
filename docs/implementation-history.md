# Historial de consolidación de la plataforma

Este documento resume los cambios acumulados durante la actualización de la plataforma dARK y sirve como guía para leer los commits temáticos de los componentes.

## Deployer y topología

El repositorio padre concentra la configuración por ramas, la generación de entornos y la topología de almacenamiento. La topología compartida es la fuente de verdad para los peers IPFS/Cluster; los entornos conservan únicamente el perfil, el selector local y los secretos. El instalador valida selectores, peers, direcciones y el mínimo de réplicas, genera `.env.integration` y aplica las ramas configuradas con fallback controlado a `main`.

La documentación consolidada cubre operación del instalador, despliegue de producción, arquitectura IPFS, resolución de timeouts y el contrato de configuración. Los artefactos de ejecución locales, notebooks generados y directorios de build no forman parte de estos commits.

## Minter API

El minter queda dividido en API, metadata worker, replication worker, chain worker y recovery worker. Los workers comparten heartbeats y locks advisory por ARK en PostgreSQL. El estado interno usa códigos compactos, mientras las APIs exponen etiquetas legibles.

El status simple consulta únicamente `worker_runtime_status`. El status full agrega diagnósticos operativos mediante consultas agregadas y bounded. Los errores se clasifican como `recoverable` o `permanent`; el recovery worker solo reencola casos recuperables cuando los workers normales están sanos y sin trabajo pendiente. La aprobación administrativa sigue siendo una operación mTLS separada.

## Store API y core library

Store API deriva sus endpoints locales de la topología, usa pools de peers y reporta la aceptación inicial y el estado de las réplicas. La biblioteca compartida conserva el contrato de almacenamiento y limita consultas costosas, con pruebas para respuestas incompletas y disponibilidad parcial.

## Dashboard

El dashboard consume el status simple en la portada y el status full solo en la pantalla de Workers. El diagnóstico full se cachea durante 30 segundos con lock para evitar consultas concurrentes. La pantalla dedicada de errores muestra estados, etapas, códigos, filtros, CIDs y paginación, pero no ejecuta recovery administrativo.

## Verificación

Antes de publicar cada commit se ejecutan las suites focalizadas del componente correspondiente y `git diff --check`. La ausencia de PHP en el entorno local impide ejecutar `php -l`; el código Laravel queda pendiente de validación en CI o en el contenedor de dashboard.

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
