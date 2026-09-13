# Instalación de producción: cinco hosts

Una sede usa cinco hosts VPN: `apps`, `blockchain-a`, `blockchain-b` y dos
`storage-node`. El único archivo que un operador edita es
`inventory.json`; contiene direcciones de gestión/VPN, referencias
SSH, ramas de componentes, política IPFS y rutas de secretos. No contiene
secretos, claves privadas ni identidades de nodos.

## Preparación

```bash
venv/bin/python deploy.py inventory-create --template production-five-host --output inventory.json
# Ajuste hosts, ramas y rutas de secretos.
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py render --inventory inventory.json --output dist/production
venv/bin/python deploy.py verify --inventory inventory.json
```

El renderer genera exactamente cinco bundles. Deriva una topología compacta de
storage, las reglas de firewall, los endpoints publicados y el `.env` de cada
host. Los artefactos privados de la cadena son una precondición: cada host
recibe sólo `genesis.json`, `static-nodes.json` y las claves de sus propios
nodos con permisos 0600.

## Entrega y orden

Revise primero el plan seguro, que no abre conexiones:

```bash
venv/bin/python deploy.py push --inventory inventory.json
venv/bin/python deploy.py apply --inventory inventory.json
```

Agregue `--execute` sólo después de revisar las direcciones SSH y las rutas
remotas impresas. El orden es fijo: `blockchain-a`, `blockchain-b`, `apps`
(RPC no validador y contratos), y después los dos nodos de storage. El
explorador se instala en `blockchain-a`; las APIs y dashboard, en `apps`.

## Red

Cada host tiene su bridge Docker local. La red Docker `dark-backbone` existe
sólo en developer HA. En producción, los cinco Besu se conectan por P2P sobre
VPN; el RPC no validador vive en apps. RPC HTTP/WS sólo se expone en VPN y el
explorador de `blockchain-a` lo usa por la dirección VPN de apps.

IPFS Cluster y Kubo se ejecutan solamente en los hosts storage. El bloque
`storage.replication` de la topología determina un pin inicial y el objetivo
de durabilidad. Apps recibe endpoints derivados, nunca una lista manual en
`.env`.

## Operación y recuperación

El runtime Besu es parte de este repositorio bajo `blockchain/`; no se clona
el runtime Besu interno. Sus perfiles son `rpc`, `validators-a`, `validators-b` y `all`.
Use sus scripts de status, exportación/importación de artefactos y reset sólo
con procedimientos de cambio aprobados. No use `docker compose down -v` sobre
un host de producción para una actualización normal.
