# Deployment v3: inventario, artefactos y conectividad

## Propósito

Este documento describe el comportamiento implementado actualmente en
`dark-deployer`. Está pensado como contexto técnico para otro agente que
necesite analizar, modificar o diagnosticar un despliegue v3.

La fuente de verdad es el código de `deployment_v3/` y los inventarios de
`examples/deployment-v3/`. La documentación histórica de v2 sirve como
contexto, pero no debe interpretarse como el flujo ejecutado hoy: el CLI
actual importa `deployment_v3` y los comandos operativos principales son
`validate`, `plan`, `render`, `preflight`, `push`, `apply`, `install`,
`status`, `verify`, `chain-*` y `secrets-init`.

## 1. Modelo mental

El inventario es un grafo declarativo tipado. No es solamente una lista de
variables para Docker.

```text
JSON de inventario
        |
        v
load_inventory()
  - schema estructural
  - tipos y valores
  - máquinas, redes y servicios
  - contratos de conexiones
        |
        v
groups_for_inventory()
  - grupos lógicos de servidor
        |
        v
build_plan()
  - endpoints
  - subredes Docker
  - dependencias y fases
  - pasos de ejecución
        |
        v
render_plan()
  - Compose por máquina/grupo
  - .env públicos derivados
  - configuraciones auxiliares
  - firewall-suggestion.json
  - manifest.json
        |
        +--> secrets-init / chain-init / operador
        |       - material privado
        |
        v
apply()/install()
  - preflight
  - adquisición/evidencia de componentes
  - distribución de secretos
  - distribución selectiva del artefacto de cadena
  - staging de fuentes y bundle
  - ejecución por dependencias
  - readiness por fase
  - verify final
```

El inventario describe intención y topología. El plan convierte esa intención
en pasos. El renderer convierte el plan en archivos públicos reproducibles. El
runner combina esos archivos con secretos y artefactos privados y ejecuta el
resultado en Docker local o mediante SSH.

## 2. Estructura de alto nivel del inventario

Los ejemplos mantenidos son:

- `examples/deployment-v3/local-simple.json`;
- `examples/deployment-v3/local-ha.json`;
- `examples/deployment-v3/production-five-host.json`;
- `examples/deployment-v3/lima-five-host.json`.

Las secciones principales son:

```json
{
  "deployment": {},
  "defaults": {},
  "networks": {},
  "machines": {},
  "groups": {},
  "services": {},
  "components": {},
  "blockchain": {},
  "infrastructure": {},
  "secrets": {},
  "settings": {}
}
```

El cargador rechaza claves desconocidas en las partes tipadas. Esto es
intencional: un typo en un nombre de red, puerto o servicio debe fallar antes
de crear contenedores.

### 2.1 `deployment`

Contiene el identificador estable del despliegue. Se usa para:

- nombrar la raíz de ejecución bajo `.generated/deployment-v3/<id>`;
- formar nombres de proyectos Compose;
- escribir `DARK_DEPLOYMENT_ID` en los entornos generados;
- identificar estado, journal, bundle e informe de instalación.

No se debe reutilizar el mismo `deployment.id` para dos topologías que deban
mantener datos separados.

### 2.2 `defaults`

Define valores aplicables a máquinas cuando no existe un override específico:

- `defaults.ssh`: usuario, puerto, clave privada y archivo `known_hosts`;
- `defaults.paths`: `workspace_root`, `data_root` y `secrets_root`;
- `defaults.docker`: `subnet_pool` y `subnet_prefix`.

Las rutas se validan como absolutas después de resolver el override. Para un
despliegue SSH son rutas dentro de la máquina remota. Para un despliegue local,
el runner las reemplaza internamente por rutas bajo el run root generado.

### 2.3 `networks`

Cada red física o lógica disponible para comunicación entre máquinas tiene:

```json
"lab": { "kind": "lan", "cidr": "192.168.105.0/24" }
```

Actualmente el modelo acepta redes `lan` y `vpn`. La red debe tener un CIDR
válido. Cada máquina declara una dirección IPv4 por red disponible. El
cargador comprueba que:

- la dirección pertenece al CIDR;
- no haya direcciones duplicadas en la misma red;
- una exposición privada use una red declarada en la máquina;
- una conexión entre hosts indique red y protocolo.

La dirección de gestión SSH es independiente de las direcciones de servicio.
Esto es relevante para Lima: `management_address` puede ser `127.0.0.1`, con
un puerto SSH diferente por VM, mientras que `addresses.lab` contiene la IP
guest que usan los contenedores entre sí.

### 2.4 `machines`

Una máquina representa un daemon Docker y su filesystem de ejecución. Sus
campos efectivos son:

```text
id
execution: local | ssh | auto
management_address
addresses: { network_id: ipv4 }
paths.workspace_root
paths.data_root
paths.secrets_root
docker.subnet (opcional)
ssh.user / port / private_key_file / known_hosts_file
```

`execution` selecciona el transporte:

- `local`: ejecuta Docker en el host del controlador;
- `ssh`: ejecuta comandos Docker en la máquina remota;
- `auto`: se resuelve según la disponibilidad del executor.

`workspace_root` contiene las fuentes transferidas y el bundle generado.
`data_root` contiene datos persistentes por despliegue y servicio. Se crea
`<data_root>/<deployment_id>/<service_id>`. `secrets_root` contiene secretos y
artefactos privados, separados del bundle público.

Para SSH, el preflight verifica Docker, Compose, arquitectura, espacio y
permisos de los padres de las rutas. No exige que `data_root` ya exista: la
comprobación de disco se hace sobre su directorio padre y el runner crea la
ruta durante `apply`.

### 2.5 `groups`

Los grupos son unidades lógicas de servidor. Cada grupo tiene:

```text
kind: apps | validators | storage
machine: machine_id
members: roles o IDs de servicio
services: IDs explícitos u opcionales
```

Un grupo no crea una red física nueva. Determina qué servicios forman un
proyecto Compose autocontenido y conserva la topología conceptual de cinco
servidores aunque varios grupos se ejecuten sobre un solo Docker local.

Si no existe `groups`, el cargador crea un grupo sintético por máquina con sus
servicios. Si existe, cada servicio debe pertenecer a un único grupo. Los
`members` pueden ser IDs de servicio o, para compatibilidad, `node_id` de
Besu/`peer_name` de storage. Cuando una máquina tiene un único grupo, los
servicios restantes de esa máquina se asignan implícitamente a él. Cuando hay
varios grupos en una máquina, la asignación debe ser inequívoca.

La separación grupo/máquina permite:

- production: normalmente un grupo por host;
- local-ha: cinco grupos sobre un daemon Docker;
- Lima: cinco grupos sobre cinco VMs.

### 2.6 `services`

Cada instancia declara:

```text
type
machine
connections
configuration
exposure
```

La instancia, no solamente el tipo, es la unidad del grafo. Por ejemplo,
`validator01` y `validator02` son dos servicios `besu-validator` distintos.

Los tipos reconocidos están en `deployment_v3/inventory.py` y son los que
`services.py` sabe renderizar: Besu RPC/validator, contratos, probe RPC,
PostgreSQL, Minter API/workers/migration, Admin API, Resolver API, Store API,
MySQL, Redis, dashboard/migration, Kubo, IPFS Cluster, explorer y edge proxy.

#### Conexiones

Una conexión tiene esta forma:

```json
"rpc": {
  "service": "rpc01",
  "network": "lab",
  "protocol": "tcp"
}
```

El `service` es el proveedor. `network` y `protocol` son obligatorios para
conexiones entre máquinas y se omiten para conexiones dentro de la misma
máquina. La validación comprueba que el proveedor exista, que el protocolo sea
`tcp` o `udp`, y que la red esté disponible.

La política de endpoints es:

- mismo host: Docker DNS usa el ID del servicio y su puerto interno;
- hosts distintos: el proveedor debe tener `exposure.mode=private`, la red
  de exposición debe coincidir con la red de la conexión y el consumidor usa
  la IP de la máquina proveedora en esa red y el puerto publicado.

Esto evita generar accidentalmente URLs con nombres Docker que no resuelven
entre hosts físicos.

#### Exposiciones

`exposure.mode` puede ser `none`, `loopback`, `private` o `public`.

- `none`: no publica puerto en el host;
- `loopback`: publica en `127.0.0.1`;
- `private`: publica en la dirección de la red indicada;
- `public`: publica en `0.0.0.0`.

Las exposiciones privadas deben indicar `network`. Los puertos de APIs tienen
una política centralizada en `network.py`; por ejemplo RPC 8545, Admin 8000,
Minter 8001, Resolver 8002 y Store 8003. La validación rechaza una exposición
privada con un puerto distinto al esperado por la política.

### 2.7 `components`

Cada componente público declara al menos repositorio y branch. `acquire.py`
usa `COMPONENT_PATHS` para mapear el ID lógico al checkout local:

```text
dark-core-admin-api    -> components/dark-core-admin-api
dark-core-lib          -> components/dark-core-lib
dark-core-minter-api   -> components/dark-core-minter-api
dark-core-resolver-api -> components/dark-core-resolver-api
dark-dapp              -> components/dark-dapp
dark-explorador        -> components/dark-explorador
dark-ipfs              -> components/dark-ipfs
dark-store-api         -> components/dark-store-api
dashboard-web          -> components/dashboard-web
```

Antes de aplicar, `source_evidence()` registra branch, commit, dirty state,
origin y la referencia del inventario. En una instalación SSH, el controlador
transfiere las fuentes validadas; el host remoto no elige otra branch.

`push` guarda esa evidencia en el bundle. Un `apply` posterior exige que la
evidencia actual sea idéntica, incluyendo el estado dirty. Si cambia cualquier
checkout, hay que generar un nuevo push.

### 2.8 `blockchain`

Contiene la identidad pública de la red y la política de Besu:

- `chain_id`;
- imagen Besu;
- configuración QBFT;
- lista ordenada de nodos `validator01`..`validator04` y `rpc01`;
- `artifact.path`, ruta relativa bajo `secrets_root`;
- opcionalmente `artifact.source`, directorio del controlador para despliegues
  SSH.

El inventario define qué nodo vive en qué máquina mediante la configuración de
cada servicio Besu. El generador deriva las direcciones anunciadas:

- local: direcciones estables dentro de la subred Docker asignada;
- remoto: dirección de la máquina en la red Besu del inventario.

Los puertos P2P internos siempre son 30303. En remoto se publican puertos
deterministas derivados de `p2p_port_start` y del orden de los nodos. El
artefacto de cadena y `static-nodes.json` deben usar esas mismas direcciones y
puertos.

### 2.9 `infrastructure`

Centraliza puertos y redes de Besu, IPFS Kubo y IPFS Cluster. El renderer usa
esta sección para:

- publicar P2P solamente cuando cruza hosts;
- construir multiaddresses de Kubo y Cluster;
- generar reglas de firewall;
- derivar bootstrap y announce addresses.

El despliegue local no publica P2P al host porque todos los servicios comparten
una red bridge. El despliegue remoto publica Besu P2P, Kubo swarm y Cluster
P2P en la red privada declarada.

### 2.10 `secrets`

Cada secreto declara una ruta relativa y consumidores. La definición sirve para
tres tareas distintas:

1. `secrets.py` sabe qué valores puede generar;
2. `services.py` monta el archivo correcto en el contenedor;
3. `runner.py` verifica que cada consumidor tenga acceso antes de arrancar.

Los secretos generables son password/runtime del Minter, runtime del dashboard,
IPFS swarm key e IPFS Cluster secret. El master wallet y el contract signer son
precondiciones explícitas del operador: no se regeneran automáticamente.

## 3. Validación y construcción del plan

### 3.1 `validate`

`deploy.py validate` llama a `load_inventory()`. El cargador:

1. lee JSON;
2. valida la existencia y forma básica del documento;
3. valida redes, defaults, máquinas y overrides;
4. construye objetos inmutables `Machine`, `ServiceInstance` y `Network`;
5. valida referencias y tipos de servicios;
6. valida contratos de conexiones por tipo;
7. valida colocación, puertos, exposiciones y reglas de worker;
8. retorna el documento raw junto con los objetos tipados.

Las reglas de dominio actuales incluyen:

- existencia de tipos requeridos;
- Store conectado exactamente a los Cluster declarados;
- Cluster conectado a Kubo;
- Minter API, PostgreSQL y workers juntos en la misma máquina;
- cada worker conectado a ese PostgreSQL;
- conexiones remotas con red/protocolo/exposición coherentes;
- ausencia de colisiones de puertos publicados;
- conjunto exacto de workers metadata, replication y chain.

### 3.2 Grupos

`groups_for_inventory()` se ejecuta después de cargar máquinas y servicios.
Comprueba que no haya grupos duplicados, servicios asignados dos veces ni
servicios sin asignación inequívoca. El resultado es una tupla de `Group` que
entra en el plan.

### 3.3 Endpoints y subredes

`derive_endpoints()` recorre todas las conexiones y llama a `endpoint_for()`.
El resultado distingue transporte Docker de transporte privado y contiene host,
puerto y red.

`allocate_docker_subnets()` toma `defaults.docker.subnet_pool`, divide el pool
según `subnet_prefix`, ordena las máquinas por ID y asigna una subred por
daemon. Rechaza solapamiento con redes físicas o con otra subred Docker.

### 3.4 Dependencias y fases

`build_plan()` crea pasos `PlanStep` en este orden conceptual:

```text
preflight
validators
rpc
contracts
storage
data
applications
verify
```

Dentro de las fases, `_layers()` calcula un orden topológico usando las
conexiones. Cada servicio recibe `apply:<service_id>` y las dependencias de sus
proveedores. Después de una fase se inserta un paso de readiness.

La ordenación no es sólo cosmética: impide arrancar un consumidor antes de su
proveedor y hace explícito dónde se bloquea una instalación.

## 4. Renderizado de artefactos públicos

`render_plan(plan, output)` exige que el directorio de salida sea inexistente o
vacío. Genera:

```text
<output>/
  shared/
    deployment-topology.json
    plan.json
    firewall-suggestion.json
  machines/<machine>/
    machine.json
    compose.yaml
    env/<service>.env
    config/storage-endpoints.json
    config/nginx.conf              # sólo edge-proxy
    groups/<group>/
      machine.json
      compose.yaml
      env/<service>.env
      config/storage-endpoints.json
  manifest.json
```

El renderer copia el inventario a `shared/deployment-topology.json` y escribe
un plan serializado. Cada archivo público relevante entra en el manifest con
SHA-256. Los secretos no se escriben en esos `.env` públicos.

### 4.1 Variables derivadas

`_env()` no copia arbitrariamente todo el JSON: traduce el tipo de servicio a
las variables que su imagen necesita. Algunos ejemplos:

- Minter: `DATABASE_URL`, Store API, RPC, chain ID y parámetros de workers;
- Admin/Resolver: host, puerto, RPC, chain ID y Store cuando corresponde;
- Dashboard: URLs de Admin, Minter, Resolver, Store, RPC, Kubo, Cluster y
  `WORKER_STATUS_URL`;
- Store: host/puerto y archivo de endpoints de storage;
- Kubo: swarm key, peer name, bootstrap multiaddresses y announce address;
- Cluster: secreto, peer name, alias Kubo, bootstrap multiaddresses y seed;
- Explorer: URL RPC;
- contracts-deploy: RPC, chain ID, rutas de handoff y signer.

Las URLs se calculan desde el grafo. Un consumidor local recibe un nombre DNS
Docker; uno remoto recibe IP privada y puerto publicado.

### 4.2 Compose

`compose_document()` crea un proyecto por máquina y, adicionalmente, un
proyecto por grupo lógico. Todos los proyectos de una misma máquina comparten
el bridge externo `<deployment>-<machine>`.

Los servicios de aplicación se construyen desde `workspace_root/components`.
Besu usa la configuración de `workspace_root/blockchain/config`. Los datos se
montan desde `data_root/<deployment_id>`. Los secretos se montan desde
`secrets_root` en rutas de sólo lectura.

Los servicios one-shot (`contracts-deploy`, migraciones y `rpc-probe`) usan
Compose profile `setup`; el runner los ejecuta explícitamente con `run --rm`.
Los servicios persistentes usan `up -d --build`.

### 4.3 Storage endpoints

El renderer examina las conexiones del Store, resuelve cada Cluster y su Kubo
y genera `storage-endpoints.json`. Ese archivo contiene, por nodo:

- nombre del peer;
- URL de API Kubo;
- URL de API Cluster.

No se inventan endpoints independientes: todos salen de las conexiones del
Store y de la función de endpoints. Esta es la unión entre la topología de
inventario y el backend de almacenamiento de `dark-store-api`.

### 4.4 Firewall

`firewall-suggestion.json` enumera por máquina las exposiciones declaradas y
los puertos P2P necesarios. Incluye:

- puertos de servicios expuestos;
- Besu P2P TCP/UDP;
- Kubo swarm TCP/UDP;
- Cluster P2P TCP.

Es una sugerencia de configuración, no una modificación automática del
firewall del sistema.

## 5. Generación y distribución de secretos

### 5.1 Secretos auxiliares

```bash
venv/bin/python deploy.py secrets-init \
  --inventory examples/deployment-v3/lima-five-host.json \
  --output /Volumes/Externo/dark-lab/secrets/dark-production-five-host
```

La salida debe estar vacía, salvo que se use `--overwrite`. Los archivos se
crean con permisos 0600 y el directorio debe protegerse con 0700.

Se generan sólo credenciales que pueden reemplazarse sin cambiar la identidad
de la cadena. `master-wallet` y `contract-signer` deben ser suministrados por
el operador. En el flujo local `install` puede usar el mismo archivo de wallet
como ambas credenciales cuando el operador lo indica.

### 5.2 Master wallet y signer

El archivo puede ser un `master-wallet.txt` con una línea `Private Key:` o una
clave hexadecimal raw. El instalador extrae la clave, valida su forma y se
niega a reemplazar un secreto existente con contenido distinto.

Reutilizar el mismo master wallet en otra topología es posible, pero no hace
reutilizables las claves de nodo Besu. Si Lima representa una red nueva, el
wallet puede ser el mismo mientras se crea un nuevo genesis y nuevas claves de
validadores.

### 5.3 Distribución

`distribute_secrets()` usa las definiciones del inventario y transfiere sólo
los secretos que consume cada máquina. Antes de arrancar cada servicio,
`_apply_service()` comprueba que cada archivo requerido sea legible.

El bundle y los secretos siguen caminos separados. En remoto, los secretos
quedan bajo `machine.secrets_root`; no se incluyen en la copia pública del
bundle ni en la transferencia de fuentes.

## 6. Artefacto de blockchain

### 6.1 Inicialización

Para una red nueva, el flujo de bajo nivel es:

```bash
venv/bin/python deploy.py chain-init \
  --inventory examples/deployment-v3/lima-five-host.json \
  --output /Volumes/Externo/dark-lab/secrets/dark-production-five-host \
  --master-wallet-address 0x...
```

La inicialización construye el contexto a partir del plan:

- deployment ID y chain ID;
- imagen y parámetros QBFT;
- direcciones privadas anunciadas;
- puertos P2P derivados;
- master wallet address.

El artefacto completo contiene el genesis, claves de nodo, static nodes y un
manifest de integridad. El manifest registra SHA-256 y `chain-verify` rechaza
archivos faltantes, alterados o no registrados.

`chain-bootstrap` y `chain-static-nodes` siguen disponibles para operaciones
por etapas. `chain-export` puede separar roles `rpc`, `validators-a` y
`validators-b` para revisión o distribución.

### 6.2 Red nueva versus red existente

No se debe reutilizar un artefacto de una topología local en Lima si contiene
direcciones Docker o claves de nodos distintas. Hay dos casos:

1. Red Lima nueva: mismo master wallet opcional, pero nuevo artefacto para el
   inventario Lima.
2. Red existente que debe preservarse: usar su artefacto original validado y
   no ejecutar una inicialización que cambie genesis o identidades.

El runner remoto exige `blockchain.artifact.source`. Esa ruta es del
controlador y debe apuntar a un directorio de artefacto completo. Para cada
nodo remoto sólo se copian `genesis.json`, su propio `nodekey` y su propio
`static-nodes.json`; no se distribuyen indiscriminadamente todas las claves a
todas las máquinas.

### 6.3 Relación con Compose

Besu monta:

```text
<secrets_root>/<artifact.path>/genesis.json
<secrets_root>/<artifact.path>/nodes/<node>/nodekey
<secrets_root>/<artifact.path>/nodes/<node>/static-nodes.json
```

El nodo RPC habilita HTTP RPC; los validadores no. Todos escuchan internamente
en 30303. En remoto, el host publica el puerto determinista correspondiente y
el genesis/static-nodes debe anunciar la misma combinación IP/puerto.

## 7. Ejecución local, SSH y Lima

### 7.1 Local Docker

El executor local no usa directamente los paths del inventario como paths
persistentes del host. `_effective_plan()` los reemplaza por:

```text
.generated/deployment-v3/<deployment>/local/<machine>/sources
.generated/deployment-v3/<deployment>/local/<machine>/data
.generated/deployment-v3/<deployment>/local/<machine>/secrets
```

Esto permite simular varias máquinas en un daemon Docker sin contaminar los
paths de producción. Cada máquina lógica tiene su propia subred bridge y los
grupos comparten la red de esa máquina.

### 7.2 SSH remoto

El runner transfiere `components`, `blockchain` y `deployment_v3`, excluyendo
Git, entornos, `.generated` y virtualenvs. También transfiere el directorio de
deployment generado a:

```text
<workspace_root>/.generated/deployment-v3/<deployment>/machines/<machine>
```

Los comandos Compose se ejecutan mediante SSH en la máquina correspondiente.

### 7.3 Lima

`local-infra/generate-lima-inventory.py` parte del template de production,
descubre cada VM mediante `limactl` y escribe overrides locales:

- `management_address=127.0.0.1`;
- puerto SSH forward específico de cada VM;
- usuario guest real;
- clave Lima y `known_hosts` dedicado;
- IP guest en `addresses.lab`;
- paths bajo el HOME guest;
- `execution=ssh`.

Esta dualidad es necesaria: macOS administra las VMs por localhost/puerto
forwarded, pero los contenedores se interconectan usando las IP guest de
`lima0`. El inventario generado debe revisarse y validarse antes de instalar.

Comandos de comprobación:

```bash
venv/bin/python local-infra/generate-lima-inventory.py \
  --store /Volumes/Externo/dark-lab

venv/bin/python deploy.py validate \
  --inventory examples/deployment-v3/lima-five-host.json

venv/bin/python deploy.py preflight \
  --inventory examples/deployment-v3/lima-five-host.json
```

## 8. Instalación y máquina de estados

`install` coordina el flujo completo. Antes de mutar servicios:

1. solicita confirmación, salvo `--yes`/`--non-interactive`;
2. ejecuta preflight en todas las máquinas;
3. adquiere o conserva componentes según `--skip-acquire` y la respuesta del
   operador;
4. prepara secretos locales si aplica;
5. obtiene o genera wallet y artefacto local cuando corresponde;
6. elimina el bundle público previo salvo `--resume`;
7. llama a `apply()`.

`apply()` toma un lock por deployment y registra estados en `status.json` y
`journal.jsonl`. Los estados principales son:

```text
secrets_distributed
chain_artifacts_distributed
preflight passed
service steps succeeded/reused
readiness succeeded
applied
verified / verification_failed
```

El modo `resume` reutiliza servicios marcados como aplicados y el bundle
existente. Una instalación normal regenera el bundle para reflejar inventario,
fuentes y env actuales; los datos persistentes no se borran automáticamente.

## 9. Readiness y verificación

Las gates de readiness se ejecutan después de las fases:

- validators: todos los validadores están running;
- rpc: `net_peerCount` debe ser al menos 4;
- storage: cada Kubo y Cluster responde a su probe local;
- data: Store responde en `/health`;
- applications: los contenedores arrancan; los probes finales quedan para
  `verify`.

`verify()` comprueba además:

- estado Compose por grupo;
- chain ID RPC;
- peers Besu;
- swarm peers Kubo;
- peers IPFS Cluster;
- health del Store con `refresh=true`;
- heartbeats de workers metadata, replication y chain;
- HTTP del edge proxy y `/explorer/` cuando existe.

Un HTTP 200 aislado no demuestra que la replicación funcione. La evidencia
debe incluir peers de Kubo/Cluster, health del Store y heartbeats de workers.

## 10. Flujo operativo recomendado

Para analizar o ejecutar una topología nueva:

```bash
# 1. Generar o revisar inventario
venv/bin/python deploy.py validate --inventory INVENTORY.json

# 2. Ver plan y grupos
venv/bin/python deploy.py plan --inventory INVENTORY.json --json

# 3. Renderizar en un directorio vacío
venv/bin/python deploy.py render \
  --inventory INVENTORY.json \
  --output /tmp/dark-rendered

# 4. Probar máquinas
venv/bin/python deploy.py preflight --inventory INVENTORY.json

# 5. Provisionar secretos auxiliares en una ruta protegida
venv/bin/python deploy.py secrets-init \
  --inventory INVENTORY.json \
  --output /secure/deployment-secrets

# 6. Para red nueva, generar artefacto de cadena según ese inventario
venv/bin/python deploy.py chain-init \
  --inventory INVENTORY.json \
  --output /secure/deployment-secrets \
  --master-wallet-address 0x...

# 7. Instalar con wallet/signer y artifact source correctamente configurados
venv/bin/python deploy.py install \
  --inventory INVENTORY.json \
  --master-wallet-file blockchain/master-wallet.txt

# 8. Si falla o se necesita evidencia
venv/bin/python deploy.py status --inventory INVENTORY.json
venv/bin/python deploy.py verify --inventory INVENTORY.json
```

Para una instalación SSH, el paso 6 y las referencias de `secrets`/`artifact`
deben apuntar a material accesible desde el controlador. El runner distribuirá
el subconjunto necesario a cada host; no hay que copiar manualmente todo el
artefacto a todos los nodos.

## 11. Puntos de diagnóstico

Cuando algo falla, seguir este orden:

1. `validate`: ¿el JSON expresa una topología válida?
2. `plan --json`: ¿las dependencias, grupos y endpoints son los esperados?
3. `render`: ¿las URLs, env, Compose y firewall coinciden?
4. `preflight`: ¿el transporte, Docker, rutas y permisos funcionan?
5. `source_evidence`: ¿los componentes están en branch/origin/commit correctos?
6. secretos: ¿cada consumidor tiene el archivo en su `secrets_root`?
7. artefacto: ¿manifest, genesis, nodekey y static-nodes coinciden?
8. readiness: ¿la fase mínima que falla tiene evidencia suficiente?
9. `status.json` y `journal.jsonl`: ¿qué paso fue el primero en fallar?
10. `verify`: ¿fallan contenedores, red, chain, storage, Store o workers?

Errores típicos:

- `must expose a private port`: falta exposure privada para una conexión remota;
- `network/protocol are required`: conexión entre hosts incompleta;
- `artifact.source is required for SSH deployment`: falta ruta del artefacto en
  el controlador;
- `required secret ... is readable`: secreto ausente o consumidor mal declarado;
- `artifact manifest does not match`: artefacto alterado o incompleto;
- `did not answer its local peer probe`: el contenedor arranca pero Kubo o
  Cluster aún no responde localmente;
- RPC con menos de cuatro peers: revisar IP/puertos P2P, static nodes, firewall
  y que el artefacto haya sido generado para el inventario actual;
- Store healthy pero sin replicación: revisar `/health?refresh=true`, peers de
  Cluster y workers, no sólo el código HTTP.

## 12. Límites actuales y decisiones importantes

- La validación canónica está en `load_inventory()` y en las validaciones de
  dominio; el editor Textual debe tratarse como una interfaz, no como una
  segunda fuente de reglas.
- Los grupos son explícitos, pero no crean conectividad entre Docker daemons.
  La comunicación física entre hosts depende de las redes y exposiciones del
  inventario.
- El renderer genera sugerencias de firewall, no aplica reglas.
- El bundle público contiene rutas, URLs y configuración, pero los secretos y
  claves privadas se mantienen fuera de él.
- La distribución remota de fuentes es controlada por el checkout del
  controlador y su evidencia de commits.
- Un master wallet puede mantenerse entre topologías si esa decisión es
  intencional; las claves de nodo, genesis y static nodes deben corresponder a
  la red concreta que se está arrancando.
- Los paths Lima generados son específicos de las VMs y del store seleccionado;
  si las VMs se recrean, hay que regenerar el inventario y volver a escanear
  `known_hosts`.

## Archivos clave

| Área | Archivo |
|---|---|
| CLI y coordinación | `deploy.py`, `deployment_v3/cli.py` |
| Carga y reglas | `deployment_v3/inventory.py`, `deployment_v3/model.py` |
| Plan | `deployment_v3/planner.py` |
| Endpoints/subredes | `deployment_v3/network.py` |
| Compose/env/firewall | `deployment_v3/render.py`, `deployment_v3/services.py` |
| Aplicación y staging | `deployment_v3/runner.py` |
| SSH/preflight | `deployment_v3/executor.py` |
| Secretos | `deployment_v3/secrets.py` |
| Chain artifacts | `deployment_v3/artifacts.py` |
| Readiness | `deployment_v3/readiness.py` |
| Verificación | `deployment_v3/verify.py` |
| Component evidence | `deployment_v3/sources.py`, `deployment_v3/acquire.py` |
| Lima inventory | `local-infra/generate-lima-inventory.py` |
| Topología y grupos | `docs/deployment-v3-service-placement.md` |
| Prueba Lima | `docs/lima-five-host-test.md` |

