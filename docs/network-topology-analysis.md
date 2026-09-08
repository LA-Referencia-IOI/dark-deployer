# Análisis de topología de redes por sede

Documento de referencia para la conectividad de una sede. La simulación
developer ya implementa las redes separadas descritas aquí; en producción el
backbone corresponde a la VPN privada, no a un bridge Docker compartido.

## Alcance

Este documento analiza una única sede. No define todavía la comunicación entre sedes por VPN. La comunicación inter-sede se tratará posteriormente, principalmente para blockchain e IPFS.

La red developer utiliza `dark-bc-a-local`, `dark-bc-b-local`, `dark-apps` y
un `dark-backbone` fijo (`172.31.0.0/24`) para simular los hosts. Las direcciones
que aparecen en los enodes pertenecen exclusivamente al backbone. Producción
debe reemplazarlas mediante `BLOCKCHAIN_BACKBONE_IPS` con IPs privadas/VPN.

## Modelo físico de una sede

La producción debe contemplar al menos cuatro servidores físicos:

1. Un servidor de aplicaciones.
2. Un servidor blockchain o de red.
3. Un primer servidor de almacenamiento IPFS.
4. Un segundo servidor de almacenamiento IPFS.

### Servidor de aplicaciones

El servidor de aplicaciones ejecuta un Docker Compose con:

- Dashboard web.
- Admin API.
- Minter API.
- Metadata worker.
- Replication worker.
- Chain worker.
- Resolver API.
- Store API.
- PostgreSQL del minter.
- MySQL y Redis del dashboard, si corresponden a la instalación actual.

Todos estos servicios pertenecen a la misma red Docker local del servidor de aplicaciones. Los nombres Docker internos deben ser suficientes para la comunicación entre ellos:

```text
http://admin-api:8000
http://minter-api:8001
http://resolver-api:8002
http://store-api:8003
```

El dashboard no necesita conocer directamente los peers IPFS ni ejecutar RPC de blockchain para realizar sus operaciones normales. Consulta las APIs del servidor de aplicaciones.

### Servidor blockchain

Los dos servidores blockchain ejecutan:

- servidor A: `validator01` y `validator02`;
- servidor B: `validator03` y `validator04`.

Cada pareja comparte únicamente una red Docker local de su servidor y todos
los validadores alcanzan a los demás por la VPN privada. `rpc01` no valida:
vive con Apps, pertenece a su red Docker local y se une a la VPN para seguir la
cadena. Es el único servicio que expone RPC privado en `8545` y, opcionalmente,
WebSocket en `8546`.

El nombre `rpc01` solo es válido dentro de la red Docker del servidor blockchain. En el servidor de aplicaciones de producción no debe asumirse que `rpc01` es resoluble. Las APIs deben recibir la dirección privada/VPN del servidor blockchain, por ejemplo:

```text
http://10.20.0.20:8545
```

### Servidores IPFS

Cada servidor IPFS ejecuta una pareja local:

```text
Servidor storage-a
  Kubo A
  IPFS Cluster A

Servidor storage-b
  Kubo B
  IPFS Cluster B
```

Kubo y Cluster de cada servidor deben compartir una red Docker local para que Cluster acceda al Kubo local mediante un nombre interno estable.

Los dos servidores de almacenamiento no pueden compartir una red Docker real: Docker bridge es local a cada host. Se comunican mediante la red privada/VPN de la sede utilizando sus direcciones privadas.

Por tanto, cuando se dice que los dos servidores IPFS están “en la misma red”, debe entenderse que están en la misma red privada/VPN o LAN de nivel IP, no que comparten una red Docker.

## Red troncal de la sede

La red troncal es la red privada que conecta los cuatro servidores físicos. No debe modelarse como una cuarta red Docker en producción.

Ejemplo conceptual:

```text
10.20.0.10  servidor apps
10.20.0.20  servidor blockchain
10.20.0.31  servidor storage-a
10.20.0.32  servidor storage-b
```

La red troncal transporta únicamente el tráfico autorizado entre servidores:

- Apps → blockchain RPC.
- Apps/Store API → endpoints IPFS autorizados.
- Storage A ↔ Storage B para Kubo swarm y Cluster.
- Acceso administrativo y probes autorizados.

El firewall debe continuar controlando los puertos por rol. La red troncal no implica que todos los contenedores tengan acceso indiscriminado a todos los servicios.

## Estado actual del repositorio

El despliegue ya reconoce los roles físicos en `dark_deployer/deployment.py`:

- `blockchain`: `dark-env`, dApp y explorador.
- `apps`: core-lib, Admin API, Resolver API, Store API, Minter API y dashboard.
- `storage-node`: dark-ipfs.

El inventario de producción exige actualmente:

- Un host blockchain.
- Un host apps.
- Dos hosts storage-node.

También genera direcciones VPN para el RPC, las APIs y los nodos de almacenamiento, y define reglas de firewall por rol. Esta parte ya se aproxima al modelo físico deseado.

## Estado actual de las redes Docker

La implementación actual utiliza una red externa global llamada `dark-net` para mezclar varios roles:

```text
dark-net
 ├─ dashboard
 ├─ Admin API
 ├─ Minter API y workers
 ├─ Resolver API
 ├─ Store API
 ├─ validadores y RPC
 └─ Kubo y peers de IPFS Cluster
```

Además existen redes auxiliares:

- `minter-net`: PostgreSQL y servicios del minter.
- Red `default` del dashboard: aplicación, MySQL y Redis.
- Red `storage` independiente por cada Compose de dark-ipfs.

Esta mezcla funciona en developer porque todos los Compose se ejecutan sobre la misma máquina. Sin embargo, no representa correctamente la separación física de producción.

### Problemas de `dark-net`

1. `dark-net` es conceptualmente propiedad del Compose de blockchain, pero también se usa para aplicaciones y almacenamiento.
2. Si se crean redes llamadas `dark-net` en servidores diferentes, no se conectan entre sí: cada una es un bridge local distinto.
3. Los nombres Docker de un servidor no son resolubles desde otro servidor.
4. La subred fija `172.30.0.0/24` pertenece al Compose de blockchain, pero otros servicios dependen de ella indirectamente.
5. El nombre `rpc01` funciona únicamente cuando las APIs están realmente conectadas al mismo bridge Docker que el RPC.
6. Los aliases de IPFS generados para developer dependen de que Kubo, Cluster y Store API compartan `dark-net`.

## Topología Docker objetivo

La separación recomendada por rol es:

```text
dark-apps
dark-blockchain
dark-storage-<node>
dark-backbone       # solo para simular la red troncal en developer
```

### `dark-apps`

Red local del servidor de aplicaciones:

```text
dark-apps
 ├─ dashboard
 ├─ admin-api
 ├─ minter-api
 ├─ metadata-worker
 ├─ replication-worker
 ├─ chain-worker
 ├─ resolver-api
 ├─ store-api
 ├─ PostgreSQL
 ├─ MySQL
 └─ Redis
```

El dashboard debe usar esta red para resolver las APIs por nombre Docker. No debe conectarse directamente a la red IPFS.

### `dark-blockchain`

Red local del servidor blockchain:

```text
dark-blockchain
 ├─ validator01
 ├─ validator02
 ├─ validator03
 └─ rpc01
```

En una instalación developer todos los contenedores pueden convivir en una máquina, pero deben conservar esta separación lógica.

### Redes de almacenamiento

Cada pareja Kubo/Cluster debe tener su propia red local:

```text
dark-storage-a
 ├─ kubo-a
 └─ cluster-a

dark-storage-b
 ├─ kubo-b
 └─ cluster-b
```

En producción estas redes son locales a sus respectivos servidores. La comunicación entre peers se realiza con IPs privadas/VPN, no con aliases Docker.

## Store API como puente

Store API es el único componente que necesita conocer la infraestructura IPFS. En producción se ejecuta en el servidor apps y recibe de `storage-topology.json` los endpoints privados/VPN de los peers de almacenamiento.

El dashboard, minter, resolver y admin no deben conocer directamente Kubo o Cluster.

En developer, Store API puede tener una segunda conexión lógica hacia la red troncal simulada para alcanzar los peers IPFS. Esto convierte a Store API en el puente entre:

```text
dark-apps  ↔  dark-backbone  ↔  dark-storage-a / dark-storage-b
```

En producción no se intenta unir redes Docker de distintos hosts. Store API utiliza las direcciones privadas derivadas de la topología.

## Simulación developer

Developer debería simular tres servidores y una red troncal aunque todos los contenedores estén en una sola máquina.

### Red troncal simulada

`dark-backbone` sería una red Docker externa común para los endpoints que representan comunicación entre servidores:

```text
dark-backbone
 ├─ interfaz de RPC
 ├─ interfaz de Store API
 ├─ interfaz de storage-a
 └─ interfaz de storage-b
```

No debe confundirse con una red de aplicación. Su propósito es representar la conectividad inter-host.

### Developer simple

Debe mantener la misma estructura lógica, con un único peer de almacenamiento:

```text
dark-apps
dark-blockchain
dark-storage-a
dark-backbone
```

La política de replicación usa un objetivo de una réplica.

### Developer HA

Debe añadir un segundo servidor de almacenamiento simulado:

```text
dark-apps
dark-blockchain
dark-storage-a
dark-storage-b
dark-backbone
```

Kubo/Cluster A y Kubo/Cluster B tienen redes locales separadas y ambos se conectan a `dark-backbone` para simular la red privada entre servidores. La política de replicación usa el objetivo de dos réplicas.

Apps y blockchain deben conservar la misma topología en simple y HA. HA solo debe cambiar el número de peers de almacenamiento.

## Interfaces múltiples en developer

Para aproximarse a producción, algunos contenedores pueden estar conectados a más de una red:

- RPC: `dark-blockchain` y `dark-backbone`.
- APIs que necesitan RPC: `dark-apps` y `dark-backbone`.
- Store API: `dark-apps` y `dark-backbone`.
- Kubo/Cluster: su red local de storage y `dark-backbone`.
- Dashboard: únicamente `dark-apps`.

Esto permite distinguir:

- Comunicación intra-servidor por nombres Docker.
- Comunicación inter-servidor por aliases de la red troncal o por IP privada.

No es necesario introducir Docker Swarm, un overlay ni un router adicional para esta simulación.

## Resolución de endpoints

La resolución debe depender del perfil:

### Developer

Usar aliases Docker generados por el instalador, por ejemplo:

```text
http://rpc:8545
http://storage-a-cluster:9094
http://storage-b-cluster:9094
```

Estos nombres solo deben existir en la red developer correspondiente.

### Producción

Usar direcciones privadas/VPN derivadas del inventario y `storage-topology.json`:

```text
http://10.20.0.20:8545
http://10.20.0.31:9094
http://10.20.0.32:9094
```

No deben copiarse nombres Docker developer a los `.env` de producción.

## IPFS y la red compartida

La pareja Kubo + Cluster de cada servidor debe comunicarse por una red Docker local. Los peers de Cluster de distintos servidores deben utilizar las direcciones anunciadas sobre la red privada/VPN.

La topología no debe declarar peers remotos como aliases Docker globales. El peer ID criptográfico de libp2p sigue descubriéndose en runtime; el identificador lógico de topología solo sirve para generar configuración y observabilidad.

## Estado de los archivos de topología

Existe una inconsistencia que debe resolverse antes de implementar las redes:

- `dark_deployer/storage.py` valida esquema v3.
- `storage-topology.example.json` usa v3.
- `storage-topology.json` usa v2.
- `storage-topology.developer-ha.json` usa v2.

Aunque el runtime developer se genera internamente, mantener ambos formatos produce confusión y puede provocar fallos al validar bundles. La futura implementación debe elegir una única versión vigente y actualizar o retirar los ejemplos obsoletos.

## Producción y red inter-sede

Este documento no define la conexión entre sedes. Como principio futuro:

- Las APIs de cada sede permanecen locales a su servidor apps.
- El blockchain puede conectarse entre sedes mediante la VPN autorizada.
- IPFS Cluster puede formar un pinset global entre peers remotos mediante la VPN.
- Store API de una sede no necesita conocer directamente la red de aplicaciones de otra sede.

La política inter-sede debe definirse después de estabilizar la topología de una sola sede.

## Puntos que debe implementar el siguiente agente

1. Separar la red global `dark-net` en redes por rol.
2. Definir claramente qué Compose crea cada red y cuál la declara como externa.
3. Mantener una red local común para dashboard y APIs del servidor apps.
4. Mantener una red local separada para blockchain.
5. Mantener una red local por pareja Kubo/Cluster.
6. Introducir una red troncal simulada únicamente para developer.
7. Conectar Store API al dominio de almacenamiento en developer y a endpoints VPN en producción.
8. Hacer que RPC use alias developer o IP privada/VPN según el perfil; no asumir `rpc01` fuera de la red blockchain.
9. Cambiar el instalador para crear las redes correctas según el rol.
10. Actualizar los Compose de dark-env, APIs, dashboard y dark-ipfs.
11. Mantener las redes locales de bases de datos sin exponerlas innecesariamente a la red troncal.
12. Actualizar `clean.py` para limpiar las nuevas redes sin borrar redes de otros despliegues.
13. Actualizar documentación, ejemplos de `.env` y diagramas.
14. Unificar la versión de `storage-topology.json` y eliminar referencias al esquema obsoleto.
15. Probar developer simple, developer HA y un bundle de producción con cuatro hosts.

## Criterios de aceptación

- El dashboard resuelve todas las APIs por nombres Docker en `dark-apps`.
- `rpc01` no se usa como endpoint de producción desde el servidor apps.
- Store API puede alcanzar los peers IPFS configurados sin que el dashboard conozca sus endpoints.
- Developer simple y HA tienen la misma separación lógica que producción.
- Los dos peers IPFS developer pueden comunicarse por la red troncal simulada.
- En producción cada servidor usa únicamente sus redes Docker locales y la VPN real para comunicarse con los otros servidores.
- No existe una red Docker global que mezcle aplicaciones, blockchain y almacenamiento.
- Los archivos de topología y los endpoints generados corresponden al mismo esquema y perfil.

## Plan de implementación ejecutable

Este plan está pensado para un agente ejecutor. Debe respetar el orden de las fases y detenerse si una prueba contradice una decisión fijada.

### Decisiones invariables

1. Producción tiene cuatro hosts: `apps`, `blockchain`, `storage-a` y `storage-b`.
2. Cada host de producción tiene solamente redes Docker locales; la VPN privada es la comunicación entre hosts.
3. Developer simula los hosts con redes Docker por rol y una red `dark-backbone` compartida.
4. Dashboard solo se conecta a `dark-apps` y no accede directamente a RPC, Kubo o Cluster.
5. Store API es el único servicio de apps que conoce endpoints de almacenamiento.
6. No se mantiene compatibilidad con el nombre `dark-net`.
7. No se usa `rpc01` como endpoint fuera del Compose blockchain.
8. No se implementa Swarm, overlay, routers adicionales ni otra infraestructura de red.

### Fase 0 — Preparar el trabajo

1. Ejecutar `git status --short` en el repositorio padre y en cada componente modificado.
2. Registrar `docker ps -a` y `docker network ls` como referencia inicial.
3. No editar manualmente los archivos generados: `.env.integration`, `.storage-endpoints.json`, `.env.node*` ni `.generated/storage/*`.
4. No borrar contenedores, redes ni volúmenes durante esta fase.

### Fase 1 — Centralizar nombres de red

Archivo: `install.py`.

1. Reemplazar `ensure_dark_net()` por `ensure_docker_network(name)`.
2. Declarar en un solo lugar las constantes:

```python
APPS_NETWORK = "dark-apps"
BLOCKCHAIN_NETWORK = "dark-blockchain"
BACKBONE_NETWORK = "dark-backbone"
```

3. Crear `storage_network_name(node_id)` que devuelva `dark-storage-<node_id>`.
4. Crear helpers `ensure_apps_network()`, `ensure_blockchain_network()`, `ensure_backbone_network()` y `ensure_storage_network(node_id)`.
5. Cada instalación debe crear solo las redes correspondientes a su rol y perfil.

Prueba: añadir pruebas unitarias de nombres y confirmar que no quedan llamadas a `ensure_dark_net`.

### Fase 2 — Aislar blockchain

Archivo: `components/blockchain/dark-env/docker-compose.yml`.

1. Renombrar `dark-net` a `dark-blockchain` para validadores y RPC.
2. Mantener las IP fijas actuales únicamente dentro de `dark-blockchain` si QBFT las requiere.
3. En developer, conectar solamente `rpc01` además a `dark-backbone`.
4. Asignar a esa interfaz el alias `blockchain-rpc`.
5. No conectar los validadores a `dark-backbone`.
6. Mantener la publicación de `8545` y `8546` sobre `BLOCKCHAIN_BIND_ADDRESS` en producción.

Prueba: desde un contenedor en backbone, resolver `blockchain-rpc:8545`; desde fuera de `dark-blockchain`, `rpc01` no debe ser un endpoint requerido.

### Fase 3 — Consolidar aplicaciones

Archivos:

- `components/services/dark-core-minter-api/docker-compose.yml`.
- `components/services/dark-core-admin-api/docker-compose.yml`.
- `components/services/dark-core-resolver-api/docker-compose.yml`.
- `components/services/dark-store-api/docker-compose.yml`.
- `components/frontend/dashboard-web/docker-compose.yml`.

1. Conectar dashboard y todas las APIs a `dark-apps`.
2. Conservar `minter-net` para PostgreSQL del minter y la red default del dashboard para MySQL/Redis.
3. Mantener los nombres Docker `admin-api`, `minter-api`, `resolver-api` y `store-api` en `dark-apps`.
4. Dashboard no se conecta a backbone ni a storage.
5. En developer, conectar a `dark-backbone` solo Admin API, Resolver API, Minter API/chain worker y Store API cuando necesiten recursos externos.
6. Metadata y replication worker no deben usar endpoints de backbone; pueden conservar la red si el Compose común lo exige, pero debe quedar documentado.

Prueba: desde dashboard resolver todas las APIs por sus nombres Docker; confirmar que no puede resolver peers IPFS ni RPC directamente.

### Fase 4 — Aislar cada nodo IPFS

Archivo: `components/storage/dark-ipfs/docker-compose.yml`.

1. Eliminar `dark-net` de Kubo y Cluster.
2. Usar una red local `dark-storage-${NODE_ID}` para la pareja Kubo + Cluster.
3. En developer, conectar Kubo y Cluster a `dark-backbone` con aliases derivados del ID lógico, por ejemplo `storage-a-ipfs` y `storage-a-cluster`.
4. En producción, no unirlos a `dark-backbone`; anunciar y bootstrapear con IPs VPN derivadas de la topología.
5. Mantener Cluster → Kubo local mediante el nombre de servicio `ipfs` en la red local.

Prueba developer HA: los dos peers Cluster se descubren por backbone y ambos siguen usando su Kubo local.

### Fase 5 — Generar endpoints por perfil

Archivos: `install.py` y `dark_deployer/storage.py`.

1. Crear `apps_rpc_url(env)`.
2. Para developer debe devolver `http://blockchain-rpc:8545`.
3. Para sandbox y producción debe devolver `RPC_URL`, que es la IP VPN del host blockchain.
4. Usar esta función para core-lib, Admin API, Resolver API, Minter y dashboard.
5. Nunca generar `http://rpc01:8545` para servicios apps.
6. En developer, `developer_runtime()` debe producir endpoints IPFS alcanzables por Store API sobre `dark-backbone`.
7. En producción, `production_runtime()` continúa generando endpoints VPN desde `storage-topology.json`.
8. Renombrar variables históricas `IPFS_DARK_NET_ALIAS` y `CLUSTER_DARK_NET_ALIAS` por nombres neutrales de backbone.

Prueba: generar `.env.integration` para developer y producción y comparar que cada URL corresponde al perfil correcto.

### Fase 6 — Unificar `storage-topology`

Archivos: `storage-topology.json`, `storage-topology.developer-ha.json`, `storage-topology.example.json`, `dark_deployer/storage.py` y `tests/test_storage.py`.

1. Adoptar exclusivamente el esquema v3 que valida `load_storage_topology()`.
2. Actualizar o eliminar los archivos v2 existentes.
3. Mantener solamente `cluster_name`, `replication`, `nodes` y `access_groups`.
4. No reintroducir sedes, tags de sede ni mapas peer→sede como condición funcional.
5. Confirmar que `.storage-endpoints.json` de Store API no contiene aliases Docker de producción.

Prueba: v2 debe fallar con un mensaje claro; v3 debe generar runtime y endpoints válidos.

### Fase 7 — Instalación y limpieza

Archivos: `install.py`, `clean.py` y Makefiles que mencionen `dark-net`.

1. Developer blockchain crea `dark-blockchain` y `dark-backbone`.
2. Developer apps crea `dark-apps` y `dark-backbone`.
3. Developer storage crea su `dark-storage-<node>` y `dark-backbone`.
4. Producción apps crea solo `dark-apps`.
5. Producción blockchain crea solo `dark-blockchain`.
6. Producción storage crea solo `dark-storage-<node>`.
7. Actualizar `clean.py` para eliminar únicamente las redes dARK nuevas, ignorando de forma segura redes que sigan ocupadas.

Prueba: una instalación de un rol de producción no crea backbone ni redes de otros roles.

### Fase 8 — Documentación y configuración

Actualizar `README.md`, `docs/deployer-operations.md`, `docs/production-single-site-four-server-installation.md`, `docs/ipfs-architecture.md` y READMEs de dark-env, minter, Admin API y dark-ipfs.

1. Eliminar referencias operativas a `dark-net`.
2. Explicar que Docker DNS no atraviesa hosts.
3. Explicar que producción usa VPN/IP privada y developer aliases de backbone.
4. Documentar que dashboard solo usa APIs locales y Store API es el puente a storage.
5. Cambiar ejemplos de `BLOCK_NUMBER` y `LIVENESS` a `blockchain-rpc` en developer y a URL VPN en producción.

Prueba: `rg -n "dark-net|http://rpc01:8545"` no devuelve referencias de configuración o documentación que contradigan el modelo.

### Fase 9 — Pruebas automáticas

1. Añadir pruebas de helpers de nombres de red.
2. Añadir pruebas de `apps_rpc_url()` por perfil.
3. Añadir pruebas de `developer_runtime()` y `node_environment()` para aliases de backbone y direcciones VPN.
4. Probar todos los generadores de `.env.integration`.
5. Ejecutar la suite del deployer y las suites de los componentes alterados.

### Fase 10 — Validación developer simple

1. Instalar con `DEVELOPER_STORAGE_MODE=simple`.
2. Inspeccionar `dark-apps`, `dark-blockchain`, la red local storage y `dark-backbone`.
3. Confirmar que dashboard ve Admin, Minter, Resolver y Store.
4. Confirmar que APIs usan `blockchain-rpc`, no `rpc01`.
5. Ejecutar el notebook E2E y publicar un ARK con una réplica.

### Fase 11 — Validación developer HA

1. Cambiar solo `DEVELOPER_STORAGE_MODE=ha`.
2. Regenerar archivos con el instalador; no editarlos a mano.
3. Confirmar dos pares Kubo/Cluster, dos redes storage y un backbone.
4. Confirmar que Store API ve ambos peers.
5. Publicar un ARK y verificar aceptación, disponibilidad, dos réplicas y purga al alcanzar el objetivo.
6. Detener un peer sin borrar volumen, comprobar degradación controlada y reiniciarlo para comprobar reconciliación.

### Fase 12 — Validar bundle de producción

1. Crear inventario de prueba con cuatro hosts y direcciones VPN válidas.
2. Ejecutar `install.py deploy render`.
3. Verificar que apps recibe RPC VPN y nombres Docker solo para APIs locales.
4. Verificar que storage recibe selector de nodo, secretos y topología v3.
5. Verificar que ningún host recibe aliases Docker remotos ni `rpc01` como URL externa.
6. Ejecutar `install.py host validate` e `install.py host plan` para los cuatro hosts.

### Regla al cerrar cada fase

1. Ejecutar `git diff --check`.
2. Ejecutar la prueba más cercana al código modificado.
3. Buscar `dark-net` y `rpc01` con `rg` para detectar referencias equivocadas.
4. No ejecutar `docker compose down -v`, no borrar volúmenes y no hacer limpieza destructiva sin autorización explícita.
5. Si una dependencia técnica exige apartarse del plan, detenerse y documentar el caso antes de cambiar el diseño.

## Estado implementado: Compose central por host

La operación ya no depende de un Compose local de `dark-env` ni de Compose
dispersos en cada componente. Las definiciones operativas están copiadas bajo
`compose/` del deployer y se seleccionan por rol: `apps`, `blockchain-a` y
`storage`. En producción los overlays crean una red Docker local por host; la
conectividad entre hosts se realiza por VPN. La sección histórica siguiente
describe la simulación anterior y se conserva sólo para entender la evolución.

## Estado histórico: simulación de dos servidores blockchain

La simulación developer fue aplicada y probada el 2026-09-08. El Compose de
`dark-env` conserva un único archivo para que el instalador local pueda
levantar todo el entorno, pero sus redes representan tres hosts separados:

```text
dark-bc-a-local
  validator01
  validator02

dark-bc-b-local
  validator03
  validator04

dark-apps
  rpc01 (no validador)
  APIs y dashboard

dark-backbone (172.31.0.0/24)
  enlace privado entre los grupos
```

Los validadores tienen dos interfaces: una red local privada de su servidor y
el backbone. `rpc01` pertenece a `dark-apps` y al backbone, y se publica allí
con el alias `blockchain-rpc`. El Explorer también usa `dark-apps`; no depende
de la red privada de los validadores.

La cadena se regeneró desde cero con Besu `26.6.1`, cuatro validadores QBFT y
un RPC no validador. Se verificó que los cuatro nodos aparecen en
`qbft_getValidatorsByBlockNumber` y que el RPC avanzó desde el bloque génesis.

## Direccionamiento del backbone

Los enodes no anuncian direcciones de las redes locales Docker. El generador
de `static-nodes.json` usa exclusivamente las direcciones del backbone, en el
orden fijo `validator01`, `validator02`, `validator03`, `validator04`,
`rpc01`:

```env
BLOCKCHAIN_BACKBONE_IPS=172.31.0.11,172.31.0.12,172.31.0.21,172.31.0.22,172.31.0.31
```

En producción este valor debe contener las IP privadas o VPN reales de los
hosts. No debe escribirse en cada Compose ni editarse manualmente en
`static-nodes.json`. El instalador debe recibirlo desde la topología central y
generar el `.env` local y los enodes correspondientes.

La variable local solo selecciona el conjunto de cinco direcciones; no define
secretos, claves ni políticas de consenso. Las claves de nodo permanecen en
los directorios protegidos de cada host y nunca se incorporan al archivo de
topología.

## Fuente central prevista para producción

La fuente de verdad debe ser `deployment-topology.json`, compartida por el
deployer y distribuida byte a byte a los hosts. Debe declarar, por sede:

- identificador de host y rol (`apps`, `blockchain`, `storage`);
- IP privada/VPN del host;
- validadores asignados a cada servidor blockchain;
- IP y puerto P2P de cada validador;
- host del RPC no validador y sus endpoints privados;
- redes o grupos locales que el instalador debe crear.

El `.env` de cada host conservará únicamente:

```env
DEPLOYMENT_TOPOLOGY_FILE=/opt/dark/config/deployment-topology.json
DEPLOYMENT_HOST_ID=bc-a
```

El instalador derivará desde ese JSON `BLOCKCHAIN_BACKBONE_IPS`,
`static-nodes.json`, la URL RPC para las APIs y la selección de servicios del
host. Las claves SSH, wallets, claves IPFS, certificados y secretos seguirán
fuera del JSON.

## Diferencia entre developer y producción

En developer, `172.31.0.0/24` es un bridge Docker fijo que simula la VPN. En
producción, `dark-backbone` no será una red Docker compartida: será la red
privada/VPN existente entre servidores. Los servicios locales seguirán usando
sus redes Docker propias y solo expondrán al backbone los puertos necesarios:

- P2P TCP/UDP de los validadores;
- P2P TCP/UDP del RPC, si debe participar como peer;
- HTTP/WS del RPC únicamente hacia Apps y administración privada.

Esta separación evita que una IP de Docker local se confunda con una IP
alcanzable desde otro servidor y permite mover los validadores entre hosts sin
regenerar manualmente el modelo de red.

## Checklist de paso a producción

Antes de desplegar una sede en producción deben completarse estos puntos.

### 1. Topología y direccionamiento

- Crear y versionar el archivo central de topología de despliegue, separado de
  los `.env` de cada host.
- Definir allí los hosts de Apps, Blockchain y Storage, sus IPs privadas/VPN,
  los validadores, el RPC no validador y los puertos permitidos.
- No reutilizar `172.31.0.0/24` ni otras IPs de Docker developer como
  direccionamiento productivo.
- Verificar que no haya IPs duplicadas y que cada host tenga un identificador
  único.

### 2. `.env` de cada host

El `.env` productivo solo debe seleccionar perfil, host y topología. Como base:

```env
TYPE=production
PRODUCTION_INSTALL_COMPONENTS=apps
PRODUCTION_DEPLOYMENT_TOPOLOGY_FILE=/opt/dark/config/deployment-topology.json
PRODUCTION_HOST_ID=apps-a
APPS_BIND_ADDRESS=0.0.0.0
```

En un servidor Blockchain se seleccionará `blockchain`; en un servidor de
Storage, `storage-node`; y en Apps, `apps`. No se deben copiar aliases Docker
como `blockchain-rpc`, `rpc01`, `admin-api` o `store-api` entre servidores.

### 3. RPC y blockchain

- El instalador de Apps debe recibir la URL privada/VPN del RPC, por ejemplo
  `http://10.20.0.31:8545`.
- `blockchain-rpc:8545` solo es válido cuando Blockchain y Apps comparten el
  mismo bridge Docker developer.
- El RPC productivo debe aceptar tráfico HTTP/WS únicamente desde Apps y la
  red de administración.
- Los puertos P2P de validadores deben limitarse a los peers definidos en la
  topología.
- Confirmar el `CHAIN_ID`, contratos desplegados y signer antes de iniciar
  APIs y workers.

### 4. Redes y firewall

- Apps: red Docker local para dashboard y APIs.
- Blockchain: redes Docker locales para validadores y RPC.
- Storage: redes Docker locales para Kubo y Cluster de cada nodo.
- Backbone: VPN/red privada real entre servidores; no una red Docker externa
  con el mismo nombre en cada máquina.
- Abrir solo los puertos necesarios: API internas, RPC privado, P2P y probes
  de administración.

### 5. Secretos y credenciales

- Montar wallets, claves de signer, claves de Cluster, swarm keys y claves SSH
  desde un gestor de secretos o rutas locales con permisos `0600`.
- No poner claves privadas, tokens ni secretos en `.env.example`, JSON de
  topología ni repositorios.
- Verificar que los paths configurados existan en el host destino antes de
  ejecutar el instalador.

### 6. Dashboard y APIs

- El dashboard debe conectarse solo a las APIs mediante la red Apps local.
- No debe llamar directamente al RPC, Kubo, IPFS Cluster ni a una IP de otra
  sede.
- El instalador debe generar `.env.integration` con nombres Docker únicamente
  para servicios co-localizados y con URLs privadas/VPN para servicios remotos.
- Validar `/health/live` para los probes de Docker y reservar `/health` para
  diagnóstico funcional.

### 7. Validación antes de declarar la sede operativa

1. Ejecutar el instalador con `--no-start` y revisar los bundles generados.
2. Confirmar que cada servicio recibe la URL RPC correcta y no
   `localhost`, `rpc01` ni `blockchain-rpc` cuando el RPC es remoto.
3. Comprobar resolución y conectividad desde Apps al RPC y desde Apps a los
   peers de Storage autorizados.
4. Arrancar Blockchain, comprobar consenso y el avance de bloques.
5. Arrancar Storage y verificar peers, pinning y salud del Cluster.
6. Arrancar APIs, workers y dashboard; validar los cuatro workers del minter.
7. Guardar hashes de los bundles y de los repositorios efectivamente
   desplegados.

La instalación no debe considerarse lista si solo funcionan los probes de
localhost: debe comprobarse la conectividad privada entre servidores y la
separación de redes prevista para producción.
