# Mapa de configuración: instalador actual → despliegue v2

Estado: inventario de transición. Fecha: 2026-09-10.

Este documento delimita qué variables existentes debe absorber
`deployment-topology.json` v2 y cuáles deben dejar de ser entradas manuales.
No autoriza todavía retirar `.env` ni `install.py`: la migración ocurrirá solo
cuando el renderer v2 pueda generar todos los entornos y jobs operativos.

## Regla de propiedad

| Tipo de valor | Propietario v2 | Forma de entrega |
| --- | --- | --- |
| Colocación, IP, red, puertos, repositorios y ramas | Inventario | Derivado en plan/bundle |
| Política de almacenamiento y tuning operativo | `settings` del inventario | `.env` generado por servicio |
| Credencial o clave | `secrets` del inventario | Archivo preaprovisionado bajo `secrets_root` |
| Dirección de contrato/ABI producida por despliegue | Estado público del deployment | Handoff generado, nunca entrada repetida |
| Selección temporal del instalador heredado | `.env` actual | Se elimina en el corte |

## Infraestructura y código

| Variable actual | Consumidor | Campo v2 | Tratamiento final |
| --- | --- | --- | --- |
| `TYPE`, `*_INSTALL_COMPONENTS` | `install.py` | `machines`, `groups` | Retirar: placement sustituye perfiles/roles. |
| `RPC_URL`, `CHAIN_ID` | APIs, dapp, explorer | `blockchain.chain_id`, endpoint derivado `blockchain-rpc` | No pedir URL manualmente. |
| `*_REPOSITORY_URL`, `*_REPOSITORY_BRANCH` | instalador | `components.<id>.repository_url`, `.branch` | Rama por defecto `main`; registrar hash resuelto. |
| `DEPLOYER_BRANCH`, `COMPOSE_PROJECT_PREFIX` | instalador | `deployment.id`, estado del controller | Retirar del entorno de servicios. |
| `*_BLOCKCHAIN_HOST`, `*_BLOCKCHAIN_ENODES` | instalador heredado | máquinas, grupos, nodos y artefactos derivados | Retirar. |
| `*_STORAGE_MODE`, `*_STORAGE_DATA_ROOT` | instalador heredado | grupos storage, paths de máquina | Retirar. |
| direcciones de peers/endpoints IPFS | Store/IPFS | `storage.nodes` + placement | Derivar; no listas en `.env`. |

## Secretos

| Variable actual | Consumidor | Referencia v2 | Nota |
| --- | --- | --- | --- |
| `MASTER_WALLET_KEY_FILE`, `MASTER_PRIVATE_KEY` | contratos/Admin/Minter | `secrets.master-wallet` | Solo archivo; nunca contenido en JSON, bundle o manifiesto. |
| `ADMIN_PRIVATE_KEY(_FILE)`, `MINTER_PRIVATE_KEY(_FILE)` | Admin/Minter | secretos por firmante, si se mantienen separados | Deben materializarse solo en apps. |
| `DEPLOYER_PRIVATE_KEY_FILE` | controller SSH | `defaults.ssh.private_key_file` | No es secreto de aplicación ni se transfiere. |
| `*_IPFS_SWARM_KEY_FILE` | Kubo | `secrets.ipfs-swarm-key` | Montaje de solo lectura en storage. |
| `*_IPFS_CLUSTER_SECRET_FILE` | Cluster | `secrets.ipfs-cluster-secret` | Montaje de solo lectura en storage. |
| contraseña PostgreSQL minter | PostgreSQL/Minter | `secrets.minter-db-password`, `secrets.minter-runtime-env` | `secrets-init` genera `DATABASE_URL` en un archivo `0600`; el bundle público no lo contiene. |
| MySQL/Redis dashboard | dashboard | `secrets.dashboard-runtime-env` | `secrets-init` crea password, root password y APP_KEY en entorno privado `0600`; el job Laravel ya está modelado. Falta prueba Docker real. |

## Variables generadas por servicio

| Servicio | Variables que el renderer debe emitir | Fuente v2 |
| --- | --- | --- |
| Store API | `STORAGE_ENDPOINTS_FILE`, `REPLICATION_TARGET_REPLICAS`, `STORE_*_CONCURRENCY` | storage y settings.store |
| Minter API y workers | `DATABASE_URL`, `DARK_RPC_URL`, `DARK_CHAIN_ID`, contratos, `METADATA_STORE_API_URL`, `MINTER_*`, `METADATA_*`, `REPLICATION_*`, `CHAIN_*` | endpoints, blockchain, contratos publicados y settings.minter |
| Admin API | `DARK_RPC_URL`, chain ID, contratos, firmante y mTLS | blockchain, contratos, secrets, settings.security |
| Resolver API | `DARK_RPC_URL`, chain ID, contrato, `METADATA_STORE_API_URL` | blockchain, contratos publicados, endpoints |
| Dashboard | URLs privadas de Admin/Minter/Resolver/Store, DB/Redis y contratos | endpoints, contratos publicados, secretos dashboard |
| Kubo | `NODE_NAME`, bootstrap, anuncio, swarm key | placement, endpoints, secrets |
| Cluster | peer name, bootstrap, cluster name, secret, factor de réplica | storage, placement, secrets |
| Besu | genesis, node key, static nodes, P2P/RPC roles | artefactos de cadena y placement |

## Tuning a trasladar a `settings`

Los defaults actuales permanecen como referencia hasta que se tipen en schema:

- `minter.metadata`: página, concurrencia, backoff y descanso.
- `minter.replication`: tamaño de página/batch, intervalos de primer pin y
  durabilidad, presión de promociones y máximo idle.
- `minter.chain`: página, lote RPC, backoff y adaptación.
- `store`: concurrencias de add/status/promoción y TTL de health.
- `ipfs`: concurrencia de pins Cluster.
- `dashboard`: timeout HTTP y caché de diagnósticos.

No se deben trasladar variables de diagnóstico heredadas sin identificar su
consumidor. La configuración v2 solo genera una variable cuando el código del
servicio la consume.

## Huecos que bloquean el corte

1. Tipar `settings` y `exposure` en schema; ahora se conservan como objetos
   libres para no bloquear el prototipo.
2. Comparar sistemáticamente los Compose generados contra los actuales:
   comandos, healthchecks, builds, permisos y volúmenes.
3. Reducir `.env.example` al puntero de topología solo después de que los tres
   puntos anteriores hayan pasado una instalación limpia local y una por SSH.
