# Instalación de producción: cuatro servidores

Esta guía instala un blockchain, un host de aplicaciones y dos nodos de
almacenamiento que forman un único Cluster CRDT. La topología no contiene
“sedes” ni URLs duplicadas; puede ampliarse posteriormente con más nodos.

## Inventario

Use `deployment-inventory.example.json` como plantilla. Mantenga en el
inventario la dirección de administración SSH y las direcciones VPN de
blockchain/apps. Para storage, use únicamente el selector lógico:

```json
"storage": {
  "topology_file": "storage-topology.json",
  "swarm_key_target": "/opt/dark/secrets/ipfs-swarm.key",
  "cluster_secret_target": "/opt/dark/secrets/ipfs-cluster-secret"
}
```

Cada host storage declara `storage_node_id` (`storage-a`, `storage-b`); el host
apps declara `storage_access_group` (`apps-a`). No copie una dirección storage
en el inventario: se obtiene del JSON de topología.

## Topología

Cree `storage-topology.json` junto al inventario y distribuya exactamente esos
bytes a los hosts de aplicaciones y storage:

```json
{
  "version": 3,
  "cluster_name": "dark-global",
  "replication": {"target_replicas": 2, "publish_after_replicas": 1},
  "nodes": [
    {"id": "storage-a", "address": "10.20.30.31"},
    {"id": "storage-b", "address": "10.20.30.32"}
  ],
  "access_groups": {"apps-a": ["storage-a", "storage-b"]}
}
```

Las direcciones son IP privadas/VPN estáticas. El instalador deriva Kubo
`:5001`, Cluster REST `:9094` y proxy operativo `:9095`. Valide y registre:

```bash
python3.12 -m json.tool storage-topology.json >/dev/null
sha256sum storage-topology.json
```

`publish_after_replicas` permite publicar en blockchain; `target_replicas`
controla la convergencia y la purga. El Store API devuelve el CID al aceptar
el `add` local y el replication worker verifica después las copias reales.

## Secretos y prerrequisitos

En todos los hosts instale Docker Compose v2, Python 3.12 y acceso al VPN.
Genere fuera del repositorio una swarm key Kubo y un secreto Cluster distinto,
ambos con permisos 0600. Nunca copie identidades entre nodos ni use `down -v`
durante una actualización.

## Bundle determinista

En el deployer:

```bash
cp deployment-inventory.example.json deployment-inventory.json
python3.12 install.py deploy render --inventory deployment-inventory.json --output dist/production
```

El bundle valida topología, grupos y selectores, copia el JSON byte a byte,
registra su hash y genera `.env.public` por host. En cada servidor:

```bash
python3.12 install.py host validate --config dist/production/hosts/<host>/host.json
python3.12 install.py host plan --config dist/production/hosts/<host>/host.json
python3.12 install.py host apply --config dist/production/hosts/<host>/host.json
```

La instalación de storage genera Kubo/Cluster desde `storage_node_id`. La de
apps genera `.storage-endpoints.json`, montado como
`/config/storage-endpoints.json`, a partir de `storage_access_group`.

## Orden recomendado

1. Instale blockchain y distribuya el handoff público a apps.
2. Instale `storage-a`; si no existe un peer, será el seed inicial.
3. Instale `storage-b` y compruebe que se une al mismo Cluster.
4. Ejecute `python3.12 install.py storage audit` desde un host con acceso REST.
5. Instale apps, Store API, Resolver y Minter.
6. Ejecute `storage reconcile` y confirme que los dos CIDs de una prueba
   alcanzan el objetivo configurado.

## Variables de referencia

Host storage:

```ini
TYPE=production
PRODUCTION_INSTALL_COMPONENTS=storage-node
PRODUCTION_STORAGE_TOPOLOGY_FILE=storage-topology.json
PRODUCTION_STORAGE_NODE_ID=storage-a
PRODUCTION_IPFS_SWARM_KEY_FILE=/opt/dark/secrets/ipfs-swarm.key
PRODUCTION_IPFS_CLUSTER_SECRET_FILE=/opt/dark/secrets/ipfs-cluster-secret
```

Host apps:

```ini
TYPE=production
PRODUCTION_INSTALL_COMPONENTS=apps
PRODUCTION_STORAGE_TOPOLOGY_FILE=storage-topology.json
PRODUCTION_STORAGE_ACCESS_GROUP=apps-a
```

Todos los repositorios usan las ramas `*_REPOSITORY_BRANCH` del `.env`. Las
ramas son referencias móviles; registre los hashes efectivamente desplegados
para trazabilidad.

## Verificación y fallos

Compruebe `/health/live` para liveness, `/health/read` para lectura Kubo y
`/health/write` para la ruta de escritura. Store API hace failover entre los
endpoints generados; no espera la primera réplica. El status del Minter debe
mostrar metadata, replication y chain con heartbeat reciente.

Si un nodo cae, no cambie el objetivo ni elimine su volumen: Cluster mantiene
las copias disponibles y el reconciliador repara cuando vuelve. Si el peer
restante tampoco está disponible, se pausa la ingesta y se conserva el payload
en PostgreSQL. Consulte [arquitectura IPFS](ipfs-architecture.md) y
[operaciones del deployer](deployer-operations.md) antes de intervenir.
