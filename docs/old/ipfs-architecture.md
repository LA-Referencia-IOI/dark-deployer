# Arquitectura IPFS/Cluster dARK

Este es el documento canónico de almacenamiento. dARK utiliza un único IPFS
Cluster CRDT global. Cada nodo de almacenamiento ejecuta exactamente una
pareja Kubo + IPFS Cluster; Store API, Minter y Resolver no implementan
replicación entre sí.

## Fuente de verdad

La topología de producción se escribe una sola vez en la sección `storage` de
`inventory.json`. El instalador distribuye ese mismo archivo; no
crea ni acepta un archivo de topología de storage paralelo en producción:

```json
{
  "version": 3,
  "cluster_name": "dark-global",
  "replication": {
    "target_replicas": 2,
    "publish_after_replicas": 1
  },
  "nodes": [
    {"id": "storage-a", "address": "10.200.1.11"},
    {"id": "storage-b", "address": "10.200.2.11"}
  ],
  "access_groups": {"apps-a": ["storage-a", "storage-b"]}
}
```

`id` es un nombre lógico estable y `address` es una IPv4 privada/VPN. El
instalador deriva Kubo `:5001`, Cluster REST `:9094` y proxy operativo `:9095`.
No se declaran URLs redundantes, sedes, etiquetas, mapas peer→sede ni IDs
criptográficos. El peer ID libp2p se descubre al arrancar y las identidades se
conservan en volúmenes persistentes.

La validación rechaza versión distinta de 3, IDs o direcciones duplicadas,
grupos vacíos o desconocidos y políticas fuera de
`1 <= publish_after_replicas <= target_replicas <= nodos`.

Los grupos son únicamente pools de endpoints para una instancia de Store API.
Si no se declaran grupos, Store API usa todos los nodos. La pertenencia a un
grupo nunca es condición de publicación ni de purga.

## Desarrollo

El perfil developer solo recibe `DEVELOPER_STORAGE_MODE=simple|ha`. El
instalador genera `.generated/storage/runtime.json` y
`.generated/storage/store-endpoints.json`:

- simple: un nodo, publicación 1, objetivo 1;
- ha: dos nodos, publicación 1, objetivo 2.

Los aliases Docker, puertos publicados y endpoints se regeneran al cambiar de
modo. No se eliminan volúmenes, claves ni identidades. Al volver de HA a simple
se ejecuta `docker compose down` únicamente para peers inactivos, sin `-v`.

## Replicación y estados

Cluster recibe `POST /add?pin=true&local=true` y devuelve el CID cuando acepta
la escritura local. No se espera a la primera réplica. Cluster asigna copias
hacia `target_replicas`; el reconciliador puede reparar un CID usando el
payload retenido.

Cada peer usa `CLUSTER_PINTRACKER_CONCURRENTPINS=20` como punto de partida para
las operaciones paralelas de pin/unpin. Este valor controla la capacidad real
de ejecución de Kubo, no la velocidad de consulta del reconciliador. Debe
ajustarse mediante una prueba de carga observando la cola `queued`, el tiempo
hasta `pinned`, CPU, memoria y latencia de disco. Valores más altos pueden
reducir el throughput por contención.

### Incidente de configuración: alias Kubo compartido

Durante una ejecución anterior de developer, los dos peers de IPFS Cluster
usaban en `node_multiaddress` el alias Docker genérico `ipfs`. Como ambos
Kubo estaban en la red compartida, Cluster podía resolver el mismo servicio
desde los dos peers. El resultado era que una asignación parecía pertenecer a
un peer, pero el CID solo estaba realmente fijado en el otro; Store API
observaba estados `queued`, `pinning` o `unexpectedly_unpinned`, y el
reconciliador repetía comprobaciones sin que el trabajo avanzara de forma
fiable. También se detectó que el valor efectivo de concurrencia del
pintracker seguía siendo 10 aunque el entorno declaraba 20, porque el valor
no se estaba aplicando al `service.json` generado.

La corrección fue hacer que cada Cluster apunte explícitamente al alias de su
propio Kubo (`dark-ipfs-...-storage-1` o `dark-ipfs-...-storage-2`) y aplicar
`CLUSTER_PINTRACKER_CONCURRENTPINS` al archivo de servicio durante el arranque.
Después de la corrección los dos peers reportaron IDs Kubo distintos y Store
API dejó de detectar peers duplicados. La limpieza posterior de contenedores,
volúmenes y redes elimina los datos del incidente; una instalación nueva debe
regenerar las identidades y verificar nuevamente los aliases y el
`service.json` efectivo.

El Store API cuenta como réplica confirmada cada elemento `peer_map` cuyo estado
sea `pinned`, incluyendo peers no presentes en el pool local. También expone
por separado asignaciones `pin_queued`, `pinning` y `pin_error`; una asignación
en cola o en progreso no es una pérdida y nunca dispara una reparación. El
Minter aplica:

```text
stored      Cluster aceptó y devolvió CID
available   L1 y L2 tienen al menos una copia real
published   chain worker registró el ARK al alcanzar publish_after_replicas
replicated  L1 y L2 alcanzaron target_replicas
purged      payload local eliminado después de replicated
```

`publish_after_replicas` habilita el chain worker; `target_replicas` solo
controla durabilidad y purga. La distribución geográfica es una posibilidad
del allocator, nunca un requisito funcional.

## Instalador y distribución

En producción, `.env` es generado por el bundle y contiene solo perfil,
selector de grupo o nodo y secretos:

```ini
TYPE=production
PRODUCTION_INSTALL_COMPONENTS=apps
PRODUCTION_DEPLOYMENT_TOPOLOGY_FILE=.generated/deployment-topology.json
PRODUCTION_STORAGE_ACCESS_GROUP=apps-a
```

Un host de almacenamiento usa `PRODUCTION_STORAGE_NODE_ID=storage-a`. El
bundle deriva el JSON desde la sección `storage` y registra su SHA-256.
El instalador genera para Store API un documento mínimo de endpoints montado
como `/config/storage-endpoints.json` (`STORAGE_ENDPOINTS_FILE`).

## Red y seguridad

Kubo swarm (`4001/tcp+udp`) y Cluster swarm (`9096/tcp+udp`) se comunican por
la red privada/VPN. APIs administrativas (`5001`, `9094`, `9095`) no se
publican en Internet. Todas las parejas comparten una swarm key privada y un
Cluster secret de 64 hexadecimales, con permisos 0600; cada nodo conserva una
identidad distinta. Kubo desactiva bootstraps públicos.

Los bootstraps se derivan de los demás nodos declarados. CRDT no tiene líder:
los peers consultan temporalmente la API privada de un peer conocido para
resolver su ID criptográfico y después convergen el pinset global.

## Store API

Store API usa un pool generado de Kubo y Cluster REST. Hace failover entre
endpoints cuando hay timeout, conexión rechazada, 429 o 5xx, y mantiene un
cooldown breve. `/v1/store` retorna el CID inmediatamente; `/v1/status/{cid}`
expone el conteo observado. El proxy Cluster no es una dependencia del Store.

La ruta `/health/live` solo comprueba proceso. `/health/read` comprueba un Kubo
y `/health/write` una ruta Kubo y una REST de Cluster. Ningún health ejecuta
conteos globales ni espera replicación.

## Reconciliación

`ReplicationReconciliationWorker` es el único responsable de observar y
reparar réplicas. Consulta ambos CIDs mediante `POST /v1/status/batch`, en
páginas de hasta 100 ARKs y lotes de hasta 200 CIDs. Availability se procesa
antes que durabilidad. Las esperas normales se programan por ARK mediante
`next_action_at`: primer pin a `15s → 1min → 5min`, y durabilidad a
`5min → 15min → 1h`. Se registran como `CLUSTER_QUEUED`, `CLUSTER_PINNING`,
`INITIAL_VISIBILITY` o `REPLICA_TARGET`, no como errores. Solo los fallos
técnicos de Store API usan una espera de error.

Solo se repara ante evidencia real de error o ausencia de pin; nunca mientras
el CID esté `pin_queued` o `pinning`. Usa heartbeat, PID y advisory lock por
ARK.
Antes de actualizar o purgar, vuelve a bloquear el registro y compara ambos
CIDs; purga solo cuando L1 y L2 alcanzan `target_replicas`.

`/v1/status/{cid}` devuelve `pinned`, `pinning`, `queued`, `unpinned`, `error`
o `unknown`, junto con réplicas confirmadas, en cola, en pinning, con error y
asignadas.

## Operación

```bash
python3.12 install.py validate
python3.12 install.py plan
python3.12 install.py storage audit
python3.12 install.py storage reconcile
```

Añadir nodos consiste en editar la topología compartida, distribuirla de nuevo,
provisionar sus secretos/volúmenes y ejecutar `storage reconcile`. No hay una
topología por sede ni una migración automática de esquemas anteriores.

Referencias: [arquitectura CRDT de IPFS Cluster](https://ipfscluster.io/documentation/deployment/architecture/), [pinning y factores de replicación](https://ipfscluster.io/documentation/guides/pinning/), [Kubo en Docker](https://docs.ipfs.tech/install/run-ipfs-inside-docker/).
### Persistencia en el host

Kubo y IPFS Cluster usan bind mounts, no volúmenes Docker anónimos. Cada host
declara `storage_data_root` para su nodo en `inventory.json`; el
instalador valida que sea una ruta absoluta, crea `<root>/<storage_node_id>/ipfs`,
`export` y `cluster` si faltan y genera `STORAGE_DATA_ROOT` en el entorno del
peer. En developer la ruta se genera bajo `.generated/storage/data`; en
producción se recomienda un filesystem dedicado, por ejemplo
`/srv/dark/storage`. Nunca se comparte el mismo subdirectorio entre peers.
Besu sigue el mismo patrón con `blockchain_data_root` en los hosts de red y un
directorio independiente por nodo.
Durante mantenimiento, una asignación ya aceptada por Cluster no se vuelve a
pedir aunque sus peers aparezcan todavía como `remote`, `queued` o `pinning`.
Solo `assigned_replicas < target_replicas` genera una promoción. La presión de
pins de la página observada limita el siguiente lote a 100, 50 o 20 CIDs; no se
recorre el pinset global ni se persiste un estado adicional por ARK.
