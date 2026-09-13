# Propuesta: instalador modular por servidor y topología canónica (histórica)

> La propuesta fue sustituida por `deployment_v2`. Use `deploy.py` con un
> inventario de `examples/deployment-v2/`; los nombres antiguos se conservan
> solo para explicar la evolución.

> **Estado: propuesta superada.** La topología canónica y los bundles por host
> ya están implementados en el inventario de despliegue y en `dark-deployer`.
> Este archivo se conserva como historial de decisiones; para operar el
> sistema use [`deployer-operations.md`](deployer-operations.md) y
> [`production-single-site-four-server-installation.md`](production-single-site-four-server-installation.md).

## Propósito y alcance

Este documento propone una evolución del instalador de dARK para que una
misma instalación pueda desplegarse, actualizarse y verificarse por partes:

- un servidor de aplicaciones;
- un servidor o conjunto de servidores blockchain;
- un número arbitrario de servidores IPFS;
- en el futuro, más de una sede conectada por VPN.

No es una guía para ejecutar el instalador actual ni una especificación de
compatibilidad. Es una propuesta de rediseño deliberado para reemplazar el
modelo actual de perfiles y archivos dispersos por un modelo mantenible. No
modifica código ni configuración operativa.

El objetivo es que una persona pueda responder estas preguntas leyendo un
único documento de topología y el bundle de su host, sin inferir nada desde
nombres Docker, valores `localhost` o archivos generados:

1. ¿Qué servidores existen y qué rol cumple cada uno?
2. ¿Qué dirección privada/VPN y qué nombre DNS usa cada servidor?
3. ¿Qué servicios deben comunicarse entre servidores y por qué puertos?
4. ¿Qué parte se instala en este host y cuáles son sus dependencias externas?
5. ¿Qué hechos se definen una vez y qué valores se derivan automáticamente?

La propuesta cubre una sede. La interconexión de varias sedes se debe modelar
como una extensión explícita de la misma topología, no como listas paralelas
de variables de entorno.

## Diagnóstico del estado actual

El repositorio ya contiene trabajo valioso hacia la separación por roles:

- `install.py` reconoce `apps`, `blockchain` y `storage-node`.
- `dark_deployer/deployment.py` genera bundles públicos por host a partir de
  un inventario separado.
- El archivo de storage separado usa un esquema mínimo y permite derivar
  endpoints IPFS a partir de direcciones privadas.
- Las redes Docker actuales ya distinguen `dark-apps`, `dark-blockchain`,
  redes locales `dark-storage-<node>` y una red `dark-backbone` para la
  simulación developer.

Sin embargo, esas piezas no forman todavía un único modelo operativo.

| Área | Situación actual | Consecuencia |
| --- | --- | --- |
| Fuente de infraestructura | La información estaba repartida entre `.env`, inventarios separados, `.env.integration` y archivos generados. | No había una fuente inequívoca que describiera toda una sede. |
| Cardinalidad | El validador de inventario exige exactamente 1 host blockchain, 1 apps y 2 storage. | No se puede expresar de forma natural `N` nodos IPFS ni separar el bootstrap de los validadores. |
| IPFS | Existían varios formatos de topología de storage con versiones divergentes. | Un archivo aparentemente válido podía ser ignorado, fallar en preflight o inducir a editar el formato equivocado. |
| Instalación | `install_profile()` sigue siendo una secuencia global fija: blockchain, librería, admin, IPFS, Store, resolver, minter y dashboard. | El orden describe una instalación monolítica aunque se ejecute solo una parte. |
| Handoffs | `.env.integration` mezcla estado público de blockchain y de Store API; el instalador la lee y a veces la reescribe. | Es difícil saber qué host es productor de cada dato y cuándo un archivo quedó obsoleto. |
| Dirección de servicios | Coexisten DNS Docker, `localhost`, IPs VPN, `RPC_URL`, `*_PUBLIC_URL` y aliases de backbone. | Se pueden generar endpoints correctos para developer pero inválidos en producción, o viceversa. |
| Red Docker | Developer simula correctamente una red troncal; producción también declara redes externas con el mismo nombre. | Una red bridge Docker nunca atraviesa hosts: el mismo nombre en dos servidores no crea conectividad entre ellos. |
| Operación | `all` permite instalar roles incompatibles con el modelo físico objetivo. | Oculta errores de dependencia y dificulta reproducir una sede real en developer. |

También hay dos contradicciones concretas que la nueva estructura debe
eliminar:

- `storage.py` acepta exclusivamente topología v3, mientras persisten ejemplos
  v2 que contienen URLs Docker, `sites` y `purge_after_replicas`.
- La documentación operativa describe tanto bundles deterministas como la
  copia manual de `.env.integration`; ambos son mecanismos de distribución de
  estado parcialmente solapados.

## Principios de diseño

1. **Una topología canónica por despliegue.** Toda dirección, host, rol,
   endpoint publicado y pertenencia de un nodo IPFS se declara una sola vez.
2. **Los hosts no editan infraestructura.** Cada host recibe un bundle
   renderizado; su único aporte local son secretos y, si se necesita, una
   selección explícita de host para developer.
3. **El instalador aplica un rol, no “un perfil completo”.** `apps`,
   `blockchain` y `storage-node` son unidades instalables independientes.
4. **La topología es declarativa; los artefactos de ejecución son derivados.**
   Un archivo de runtime, una lista de bootstrap, `.env.integration` y los
   aliases Docker nunca son editados a mano.
5. **La conectividad entre hosts es IP/DNS privada/VPN, no Docker bridge.**
   Docker solo resuelve nombres dentro de un host.
6. **IPFS admite N nodos.** El primer nodo siembra el Cluster; todos los demás
   se unen con la misma topología y secretos. El número de réplicas se valida
   contra el número de nodos.
7. **Los secretos no forman parte del documento de topología ni de un bundle
   público.** La topología expresa rutas esperadas y requisitos de secreto,
   nunca contenido de claves.
8. **Developer reproduce la forma, no las direcciones de producción.** Puede
   simular hosts con redes Docker separadas, pero usa un preset derivado y no
   una copia modificada de una topología productiva.

## Arquitectura objetivo

### Capas de configuración

```text
inventory.json       hechos de infraestructura y política
          |
          | render
          v
dist/<deployment-id>/
  shared/                        artefactos verificables comunes
  hosts/<host-id>/               bundle público, específico del host
          |
          | apply en cada host + secretos locales
          v
Compose y runtime locales        sólo datos derivados para ese rol
```

La raíz del repositorio conserva valores que describen el código del
deployer —URLs de repositorios y ramas por componente—, pero ya no contiene
IPs, endpoints, membresías IPFS ni roles de una instalación concreta.

## Fuente única de verdad de la instalación

La fuente única de verdad debe abarcar todo lo necesario para responder dos
preguntas distintas:

1. **Qué infraestructura debe existir y cómo se interconecta.**
2. **Cómo se entrega y aplica la configuración en cada host.**

Ambas pertenecen al mismo despliegue y deben vivir en un solo documento
canónico, pero en secciones separadas. Así no se confunde una dirección de
administración usada por SSH con una dirección privada usada por IPFS Cluster,
ni una rama de código con una decisión de red.

Se propone que el inventario de despliegue sea ese documento. No debe contener
secretos; en cambio contiene referencias seguras a secretos que deben existir
en cada host. El renderizador es el único programa autorizado a transformar
este documento en `.env.public`, Compose runtime y bundles por host.

```text
inventory.json
  ├─ release: qué código se instala
  ├─ deployment: cómo llegar y aplicar en cada host
  ├─ network: cómo se comunican los roles entre hosts
  ├─ chain: cómo se forma y publica la red blockchain
  ├─ storage: cómo se forma y replica el Cluster IPFS
  └─ hosts: qué roles, direcciones y requisitos tiene cada host
```

La clave privada SSH, la clave de plataforma y los secretos IPFS nunca se
escriben en el JSON ni se copian a un bundle público. El documento sólo puede
referirse a una ruta local de controlador o a una ruta esperada en el host.
Esto permite usar la misma topología con SSH, Ansible, CI o una ejecución
manual controlada sin cambiar las direcciones ni el modelo de roles.

### Separación de configuración

| Fuente | Responsabilidad | Ejemplos | No contiene |
| --- | --- | --- | --- |
| Inventario de despliegue | Fuente canónica de infraestructura, release y método de entrega. | Hosts, roles, IPs privadas, DNS, usuario SSH, puerto, referencia a clave, ramas, secretos requeridos. | Contenido de claves, `.env` por componente, aliases Docker, estado efímero. |
| `secrets/` externo o gestor de secretos | Material sensible por controlador/host. | `ssh_private_key`, `master-wallet.key`, swarm key, Cluster secret. | Topología ni valores de runtime no sensibles. |
| bundle renderizado | Vista verificable por host. | `.env.public`, runtime IPFS, política de firewall, manifiesto. | Secreto y decisiones de hosts no necesarios para ese rol. |
| `.env` de componente | Runtime generado, local al componente. | `RPC_URL`, bind, endpoint Store. | Selección manual de host, credenciales SSH, IPs de otros roles repetidas. |
| `.env.example` del repositorio | Defaults de desarrollo y documentación de parámetros. | flags locales seguros. | Datos de un despliegue real. |

Una política importante: los `.env` dejan de ser un lugar donde se decide la
infraestructura. En producción solo son artefactos generados y reemplazables.
Si alguien cambia una IP a mano en uno de ellos, `host verify` debe detectarlo
por hash y ofrecer regenerar el bundle, no aceptar la divergencia.

### Transporte y estrategia de despliegue

La forma de despliegue también se declara de modo explícito. La primera
implementación puede soportar solo SSH; el esquema permite añadir un runner
local, Ansible o una API de orquestación sin reescribir la topología.

```json
{
  "deployment": {
    "transport": "ssh",
    "defaults": {
      "user": "darkdeploy",
      "port": 22,
      "ssh_private_key_ref": "file:///secure/operator-keys/site-a-ed25519",
      "host_key_policy": "pinned",
      "known_hosts_ref": "file:///secure/operator-keys/site-a-known_hosts",
      "target_directory": "/opt/dark-deployer",
      "apply_strategy": "bundle-and-apply"
    }
  }
}
```

Semántica propuesta:

- `transport`: inicialmente `ssh`; se valida contra una lista cerrada.
- `user`, `port` y `target_directory`: parámetros de conexión/instalación,
  heredables desde `deployment.defaults` y sobrescribibles por host solo si
  existe una necesidad real.
- `ssh_private_key_ref`: una **referencia local del controlador**, nunca el
  PEM ni un path que el host remoto deba leer. Debe tener permisos 0600 y el
  preflight debe verificar que es una clave legible sin imprimirla.
- `host_key_policy`: usar `pinned` por defecto; `known_hosts_ref` identifica
  la base de claves de host esperada. No se acepta automáticamente una nueva
  huella SSH en producción.
- `apply_strategy`: `bundle-and-apply` significa que el controlador renderiza,
  copia el bundle público, valida hashes en remoto y ejecuta el aplicador
  local. Una alternativa posterior `manual` genera instrucciones pero no abre
  SSH; una futura `ansible` reutilizaría los mismos bundles.

El comando de orquestación futuro podría ser:

```text
deployment connect-check --inventory inventory.json
deployment render --inventory inventory.json --output dist/<id>
deployment push --inventory inventory.json --bundle dist/<id>
deployment apply --inventory inventory.json --hosts site-a-storage-01
deployment verify --inventory inventory.json
```

`push` no instala nada: copia solamente bundles públicos y valida la identidad
SSH. `apply` ejecuta el instalador local del host y requiere que los secretos
de ese host ya existan. Esta división hace posible desplegar primero
blockchain, luego storage y finalmente apps sin mantener listas de comandos
distintas en cada `.env`.

### Identidad y secretos por host

El bloque de cada host declara requisitos, no valores:

```json
{
  "id": "site-a-storage-01",
  "roles": ["storage-node"],
  "management_dns": "storage-01.site-a.example.org",
  "private_address": "10.20.30.31",
  "deployment": {
    "ssh_private_key_ref": "file:///secure/operator-keys/storage-admin-ed25519"
  },
  "required_secrets": [
    {"name": "ipfs_swarm_key", "target": "/run/dark-secrets/ipfs-swarm.key"},
    {"name": "ipfs_cluster_secret", "target": "/run/dark-secrets/ipfs-cluster-secret"}
  ]
}
```

La referencia SSH específica es opcional: se hereda de `deployment.defaults`
en la mayoría de las instalaciones. El bundle local contiene los `target` y
verifica presencia/permisos; nunca recibe el origen local de la clave SSH.

Esto cubre tres identidades que no deben mezclarse:

| Identidad | Vive en | Para qué se usa |
| --- | --- | --- |
| clave SSH del controlador | estación de despliegue o CI | acceder al host y copiar/aplicar bundle. |
| clave de plataforma | hosts blockchain/apps, o gestor de secretos que los alimenta | desplegar contratos y firmar operaciones de plataforma. |
| swarm key y Cluster secret | hosts storage | formar el swarm privado y el Cluster IPFS. |

## Reglas de precedencia

La nueva estructura elimina la actual precedencia difícil de seguir entre
`.env`, `.env.integration` y valores derivados. La única precedencia es:

1. Inventario de despliegue firmado/revisado.
2. Bundle renderizado cuyo hash coincide con la topología.
3. Secreto externo presente en la ruta requerida por el bundle.
4. Runtime generado localmente a partir del bundle.

Ningún archivo de una capa inferior puede cambiar semánticamente una capa
superior. En particular, un `.env` local no puede reemplazar `RPC_URL`, pool
IPFS, dirección bind o rol; solo puede aportar parámetros explícitamente
marcados como locales de desarrollo.

| Capa | Contenido permitido | No debe contener |
| --- | --- | --- |
| Inventario de despliegue | hosts, roles, direcciones privadas, DNS público opcional, topología IPFS, política de réplica, exposición de servicios, cadena y referencias de versión. | secretos, URLs Docker, valores `localhost`, CIDs o estado generado. |
| `profiles/developer-*.json` | definición abstracta de hosts simulados y número de nodos. | IPs de producción o secretos reales. |
| `hosts/<id>/host.json` | identidad del host, rol, hashes, archivos y requisitos de secretos. | configuración de otros hosts no necesaria para aplicar el rol. |
| `hosts/<id>/.env.public` | variables derivadas que consumen componentes del host. | claves privadas, swarm key, Cluster secret. |
| archivos generados de componentes | endpoints internos Docker, bootstrap IPFS, bind addresses y configuración runtime derivada. | parámetros editables por operadores. |
| secreto local | clave de plataforma o secretos IPFS, con modo 0600. | direcciones de infraestructura o configuración versionable. |

### Roles y unidades instalables

La instalación debe modelarse por capacidades concretas y no por una lista
global de etapas.

| Rol | Servicios locales | Entradas externas necesarias | Salidas que publica |
| --- | --- | --- | --- |
| `apps` | Dashboard, Admin API, Minter API, workers, Resolver, Store API, PostgreSQL y dependencias web. | RPC privado, direcciones de contratos, pool IPFS privado, clave de plataforma cuando firma. | APIs de aplicación y dashboard según política de exposición. |
| `blockchain-bootstrap` | Un nodo inicial, contratos/dApp y, opcionalmente, explorador. | Cadena/genesis y clave de plataforma. | Chain ID, contratos, RPC privado y material de unión para validadores. |
| `blockchain-validator` | Un validador adicional. | Genesis, peer/bootstrap del bootstrap y credenciales del nodo. | Participación P2P; normalmente no publica RPC general. |
| `blockchain-rpc` | RPC dedicado, que puede coincidir con bootstrap en una primera versión. | Red blockchain ya inicializada. | RPC HTTP/WS privado. |
| `storage-node` | Una pareja Kubo + IPFS Cluster. | Topología completa, swarm key, Cluster secret. | API/Cluster privada y participación en swarm/Cluster. |

El rol `all` debe sobrevivir solo como un atajo explícito para developer. No
debe existir en bundles productivos ni en el inventario de producción.

Separar bootstrap, validador y RPC permite pasar de tres validadores en una
sola máquina a varios hosts sin rediseñar el formato. En una instalación
pequeña un host puede llevar más de una capacidad **solo si el inventario lo
declara explícitamente**; no debe ocurrir como efecto implícito de `all`.

## Direcciones, DNS y puertos

### Taxonomía obligatoria

Cada servicio debe tener nombres distintos para conceptos distintos. Así se
evita que una URL válida en Docker se convierta accidentalmente en la URL de
producción.

| Concepto | Ejemplo | Dónde se define | Consumidores |
| --- | --- | --- | --- |
| ID lógico | `site-a-storage-01` | Topología canónica | renderizador, observabilidad, nombres de artefactos. |
| Nombre de administración | `storage-01.site-a.example.org` | Topología canónica | personas, SSH, automatización; no es endpoint de datos por defecto. |
| Dirección privada/VPN | `10.20.30.31` | Topología canónica | comunicación host a host, anuncios IPFS, firewall. |
| Nombre DNS privado opcional | `storage-01.vpn.site-a.internal` | Topología canónica | alternativa estable a la IP VPN; debe resolver desde todos los consumidores. |
| Dirección pública | `dashboard.example.org` | Topología canónica, opcional | solo entrada de usuarios o integraciones autorizadas. |
| Nombre Docker | `store-api`, `postgres`, `ipfs` | Compose generado/local | únicamente contenedores del mismo host/red Docker. |
| Alias de simulación | `developer-storage-01-cluster` | preset developer generado | únicamente `dark-backbone` developer. |

Regla decisiva: un componente que cruza un límite de host usa
`private_dns` si está definido y es verificable; si no, usa `private_address`.
Nunca usa el nombre Docker del otro host.

### Qué IPs se declaran una única vez

- Todos los hosts con rol `apps`, blockchain o storage tienen una dirección
  privada/VPN única dentro de `network.private_cidr`.
- Una dirección pública es opcional y solamente se declara para entradas
  humanas o clientes autorizados: dashboard, API de minter si se expone, o
  reverse proxy. No se usa para tráfico interno.
- Los puertos estándar se definen en un catálogo de servicios versionado por
  el deployer; la topología declara únicamente excepciones justificadas.
- Los contenedores no reciben IPs de la VPN. El host publica los puertos
  necesarios sobre la interfaz privada; la red Docker local sigue siendo
  privada al host.

### Exposición mínima propuesta

| Destino | Fuentes permitidas | Puertos | Motivo |
| --- | --- | --- | --- |
| Apps | red privada; minter restringido además a clientes confiables | 8000, 8001, 8002, 8003, 8081 según necesidad | APIs y dashboard. |
| Blockchain RPC | solo apps y administración | 8545, opcional 8546 | clientes internos de cadena. |
| Blockchain P2P | validadores autorizados | 30303/tcp+udp o puertos declarados | consenso y descubrimiento. |
| Storage API/Cluster REST | solo Store API y administración | 5001, 9094, 9095 | almacenamiento y diagnóstico. |
| Storage swarm/Cluster | nodos storage autorizados | 4001/tcp+udp, 9096/tcp+udp | IPFS y Cluster entre peers. |
| Bases de datos | loopback o red Docker local | no publicados | dependencia interna. |

El firewall se deriva de la topología y se emite como artefacto revisable. No
debe ser una tabla independiente editada a mano.

## Modelo de topología canónica propuesto

Se propone sustituir los inventarios separados por un único
inventario de despliegue, schema v1.
El renderizador puede generar una vista mínima exclusiva para Store API, pero
esta no es una fuente editable.

Ejemplo reducido para una sede con tres validadores y N nodos IPFS:

```json
{
  "schema_version": 1,
  "deployment_id": "site-a-prod-01",
  "environment": "production",
  "network": {
    "private_cidr": "10.20.30.0/24",
    "transport": "vpn",
    "trusted_minter_clients": ["10.20.30.50/32"]
  },
  "chain": {
    "chain_id": 2025,
    "rpc_host": "site-a-chain-rpc",
    "validators": ["site-a-validator-01", "site-a-validator-02", "site-a-validator-03"]
  },
  "storage": {
    "cluster_name": "dark-global",
    "publication_replicas": 1,
    "durability_replicas": 2
  },
  "hosts": [
    {
      "id": "site-a-apps-01",
      "roles": ["apps"],
      "management_dns": "apps-01.site-a.example.org",
      "private_address": "10.20.30.20",
      "public_endpoints": {"dashboard": "https://dark.site-a.example.org"}
    },
    {
      "id": "site-a-chain-rpc",
      "roles": ["blockchain-bootstrap", "blockchain-rpc"],
      "management_dns": "chain-rpc.site-a.example.org",
      "private_address": "10.20.30.10"
    },
    {
      "id": "site-a-validator-02",
      "roles": ["blockchain-validator"],
      "management_dns": "validator-02.site-a.example.org",
      "private_address": "10.20.30.11"
    },
    {
      "id": "site-a-storage-01",
      "roles": ["storage-node"],
      "storage_node": true,
      "management_dns": "storage-01.site-a.example.org",
      "private_address": "10.20.30.31"
    }
  ]
}
```

El ejemplo omite dos validadores y los nodos storage restantes por brevedad.
La validación debe exigir lo siguiente:

- IDs de host únicos y direcciones privadas únicas dentro del CIDR.
- Uno o más hosts `apps`; exactamente un `blockchain-rpc` por defecto, o un
  grupo de RPC si la versión posterior soporta balanceo.
- Uno o más validadores; la política productiva puede exigir tres o más.
- Uno o más `storage-node`; `publication_replicas` y `durability_replicas`
  deben ser enteros positivos y no superar la cantidad de storage nodes.
- Cada `storage-node` dispone de dirección privada; no hay selector duplicado
  que deba coincidir con otro archivo.
- Ningún endpoint público apunta a una IP de Docker, `localhost` o una
  dirección fuera de la política de red.
- Las dependencias entre roles se derivan de la topología, no se escriben como
  listas repetidas de URLs.

### Vistas derivadas necesarias

Del documento canónico se generan, con hash y sin edición manual:

| Artefacto | Consumidor | Contenido mínimo |
| --- | --- | --- |
| `host.json` | comando `host validate/plan/apply` | identidad, roles, hashes y requisitos de secretos. |
| `.env.public` | instalador del host | bind local, URLs externas derivadas, repositorios/ramas. |
| `apps-runtime.json` | instalador de apps | RPC privado, contratos, pool de Store/IPFS y APIs internas. |
| `storage-runtime.json` por nodo | dark-ipfs | nombre Cluster, bootstrap, anuncios, factores de réplica y bind. |
| `firewall-policy.json` | operador/automatización de firewall | reglas derivadas de roles y direcciones. |
| `deployment-report.json` | operación y auditoría | hashes, ramas y commits efectivamente instalados, sin secretos. |

La información contractual (chain ID, addresses y ABI) es salida del primer
despliegue blockchain. Debe almacenarse como `chain-state.json` firmado o
hasheado, versionado dentro del bundle de despliegue siguiente. No debe
mezclarse con el `.env.integration` mutable de una instalación anterior.

## Redes y Compose por perfil

### Producción

En producción no existe una red Docker global. Cada host tiene únicamente sus
redes bridge locales:

```text
apps host
  apps-internal: dashboard, APIs y workers
  apps-data: PostgreSQL, MySQL, Redis

blockchain host
  blockchain-internal: validadores y RPC

storage host N
  storage-N-internal: Kubo N y Cluster N
```

El tráfico que sale de esos bridges lo publica el host sobre su dirección
privada/VPN. Por ejemplo, Store API en apps alcanza
`http://10.20.30.31:9094`; no se conecta a una red Docker remota llamada
`dark-backbone` ni resuelve `ipfs` o `rpc01` de otro servidor.

### Developer simple y HA

Developer puede conservar una sola máquina, pero debe representar las mismas
fronteras físicas:

```text
developer simple
  apps-internal --\
  blockchain-internal -- dark-backbone (simulación de VPN)
  storage-01-internal --/

developer HA
  apps-internal --\
  blockchain-internal -- dark-backbone
  storage-01-internal --/
  storage-02-internal --/
```

`dark-backbone` solo existe en developer. En ella se conectan las interfaces
que cruzarían hosts reales: RPC, Store API y los endpoints Kubo/Cluster. El
dashboard permanece solo en la red apps. Los nombres de backbone se generan
desde el preset developer y nunca se escriben en una topología productiva.

## Flujo de instalación propuesto

El rediseño sustituye la secuencia global fija por operaciones explícitas.
Los nombres siguientes son propuestas de CLI, no comandos existentes.

```text
1. inventory validate --file inventory.json
2. inventory render --file inventory.json --output dist/<id>
3. host validate --bundle dist/<id>/hosts/<host-id>
4. host apply --bundle dist/<id>/hosts/<host-id>
5. deployment verify --bundle dist/<id>
```

### Fase A: preparar y renderizar

1. El operador crea o modifica solo el inventario de despliegue.
2. `inventory validate` comprueba cardinalidad, direcciones, secreto requerido,
   capacidad de réplica, referencias de repositorio y exposición de puertos.
3. `topology render` produce bundles inmutables por host con hashes.
4. El operador distribuye el bundle público y provisiona los secretos en las
   rutas declaradas, con permisos 0600.

### Fase B: bootstrap de blockchain

1. Se aplica el host bootstrap de blockchain.
2. Se despliegan contratos una única vez.
3. El instalador escribe un `chain-state.json` explícito: chain ID, RPC
   privado, direcciones de contrato, ABI y hash de génesis.
4. Se vuelven a renderizar los bundles que dependen de estado contractual o
   se distribuye un artefacto de estado firmado con versión definida.

### Fase C: validadores y almacenamiento

1. Se aplican los validadores adicionales usando el material de unión generado
   por el bootstrap; ninguno vuelve a desplegar contratos.
2. Se aplica el primer `storage-node`, que crea el Cluster.
3. Se aplica cada nodo storage adicional. Cada uno recibe la topología completa
   pero ejecuta únicamente su configuración local derivada.
4. Se ejecuta una verificación de Cluster antes de instalar apps.

### Fase D: aplicaciones

1. Se aplica el host `apps` con el RPC y el pool IPFS ya derivados.
2. Se crean las bases de datos y se ejecutan las migraciones del minter.
3. Se inician Admin, Store, Resolver, Minter/workers y Dashboard.
4. La verificación comprueba conectividad desde el host apps hacia RPC y al
   menos un endpoint storage, además de salud interna de APIs y workers.

### Fase E: cambio incremental

Agregar un nodo IPFS no requiere reinstalar apps ni blockchain:

1. Añadir el host storage al documento canónico.
2. Ajustar `durability_replicas` solo si corresponde.
3. Renderizar una nueva revisión del despliegue.
4. Aplicar el bundle al nodo nuevo.
5. Aplicar la reconciliación de factores de Cluster a los nodos existentes.
6. Aplicar el bundle actualizado al host apps únicamente si el pool de
   endpoints debe cambiar.

El mismo patrón sirve para añadir un validador o un segundo host de apps una
vez que se defina la política de balanceo.

## Dependencias explícitas

```text
blockchain-bootstrap ──> chain-state ──> apps
          │
          └───────────────────────────> blockchain-validator(s)

storage-node[0] ──> Cluster listo ──> storage-node[1..N]
                                        │
chain-state + pool storage listo ───────┴──> apps
```

El grafo deja claro que el host apps no necesita que el instalador local haya
clonado o ejecutado blockchain/IPFS; necesita artefactos verificados y
endpoints alcanzables. También elimina la necesidad de “reanudar desde una
etapa” que pertenece a una secuencia monolítica.

## Cambios concretos requeridos para implementar la propuesta

### 1. Modelo y renderizador

- Crear un módulo `dark_deployer/topology.py` con esquema, validación y
  derivación. `deployment.py` puede convertirse temporalmente en adaptador.
- Sustituir las restricciones de exactamente cuatro hosts y dos storage nodes
  por cardinalidades declarativas.
- Fusionar el inventario y la topología de storage. Retirar los esquemas
  separados y
  los archivos v2 del repositorio; mantener un único ejemplo válido.
- Introducir `chain-state.json` como artefacto explícito, separado de los
  valores de configuración del host.
- Hacer que todos los bundles lleven el hash de topología, hash de chain state
  cuando exista y las revisiones/commits efectivos de los componentes.

### 2. Instalador

- Reemplazar `install_profile()` como orquestador principal por `apply_role()`
  que recibe un bundle ya materializado.
- Mantener `rebuild <component>` como operación local de desarrollo, pero no
  usarlo para construir la dependencia entre servidores.
- Separar las acciones `validate`, `render`, `apply`, `verify`, `status` y
  `upgrade` con entradas y salidas deterministas.
- Retirar escritura implícita de `.env.integration` global. Los componentes
  reciben sólo su runtime derivado y el bundle retiene el estado público.
- Reemplazar las variables `*_INSTALL_COMPONENTS`, `*_BLOCKCHAIN_HOST`,
  `*_STORAGE_NODE_ID` y `*_STORAGE_ACCESS_GROUP` en instalaciones productivas
  por datos derivados de `host.json`.

### 3. Componentes y redes

- Convertir las redes externas actuales en nombres por función y host;
  producción no debe depender de `dark-backbone`.
- Retirar `dark-net` completamente, incluido cleanup y documentación.
- Hacer que los Compose de apps consuman sólo DNS Docker locales y URLs
  privadas derivadas para dependencias remotas.
- Hacer que el Compose de IPFS reciba bootstrap/anuncios derivados por nodo,
  sin rutas alternativas de topologías manuales.
- Evitar que dashboard, minter o resolver conozcan peers IPFS; solo Store API
  recibe el pool de almacenamiento.

### 4. Observabilidad y operación

- Agregar a `deployment verify` pruebas con origen explícito: apps→RPC,
  apps→Store/API Cluster, peer↔peer IPFS, y API/worker local.
- Hacer que `status` indique revisión de bundle y topología, no solo procesos
  Docker activos.
- Generar un informe de instalación que guarde hashes, ramas y commits reales
  sin guardar secretos.
- Diferenciar health local (contenedor vivo), readiness del rol y verificación
  de dependencia remota. Un `200` de una API no debe probar por sí solo que
  el Cluster o RPC requerido están disponibles.

## Plan de migración recomendado

| Fase | Resultado verificable | Riesgo que reduce |
| --- | --- | --- |
| 0. Inventario de compatibilidad | Matriz de variables actuales, consumidores y secretos. | Eliminar una variable todavía requerida por un componente. |
| 1. Esquema canónico | Validador y ejemplos para 1 apps, 3 validadores y N storage. | Restricciones de cuatro hosts y topologías v2/v3 incompatibles. |
| 2. Render puro | Bundles reproducibles sin aplicar Docker. | Mezcla entre configuración, secretos y efectos de instalación. |
| 3. Roles IPFS | Bootstrap y unión de N storage nodes desde bundles. | Fallos de bootstrap o duplicación de identidad. |
| 4. Roles blockchain | Bootstrap, validadores y RPC como roles independientes. | Re-despliegue accidental de contratos. |
| 5. Apps | Apps consume sólo artefactos renderizados y dependencias privadas. | URLs Docker/localhost que escapan al host correcto. |
| 6. Developer | Presets simple/HA reproducen las fronteras de producción. | Diferencias ocultas entre laboratorio y producción. |
| 7. Retiro | Eliminar `.env.integration` global, v2, `all` productivo y rutas antiguas. | Mantener dos modelos activos indefinidamente. |

No se debe eliminar la vía actual antes de que cada fase tenga una ruta de
verificación. Dado que el usuario indicó que no se necesita compatibilidad
hacia atrás para bases de datos, esta propuesta puede introducir un corte
limpio de configuración cuando el renderizador nuevo esté listo.

## Criterios de aceptación del futuro ejecutor

1. Se puede describir una sede con 1 apps, 3 validadores y cualquier cantidad
   `N >= 1` de nodos IPFS usando un único archivo canónico.
2. Ninguna IP privada, DNS, endpoint IPFS o pertenencia de host se copia entre
   `.env`, topologías separadas y handoffs manuales.
3. El bundle de un host permite instalar únicamente ese rol, después de que se
   provisionen sus secretos, sin pedir datos interactivos de infraestructura.
4. Producción no usa una red Docker que pretenda abarcar varios hosts.
5. Developer simple y HA reproducen las fronteras apps/blockchain/storage y
   cambian solo el número de nodos storage.
6. Un nodo storage nuevo puede añadirse sin reinstalar la cadena ni modificar
   manualmente Compose en los nodos existentes.
7. Dashboard y APIs de apps nunca necesitan conocer una dirección de peer
   IPFS; Store API es el único puente hacia almacenamiento.
8. El informe final identifica bundle, topología, chain state, ramas y commits
   realmente aplicados.
9. Los comandos de validación y render no arrancan Docker ni alteran archivos
   de configuración activos.

## Decisiones pendientes que deben fijarse antes de implementar

- Si se admite más de un host `apps` en la primera versión o se mantiene uno
  hasta introducir balanceador, sesiones dashboard y base de datos HA.
- Si los endpoints privados se expresan siempre como IP VPN o se permite DNS
  privado obligatorio. La recomendación es aceptar ambos, con DNS validado al
  renderizar y dirección IP como respaldo para IPFS bootstrap.
- Si blockchain RPC comparte host con el bootstrap inicialmente o se exige un
  host de RPC dedicado. La estructura propuesta permite ambas variantes.
- Política de exposición del Dashboard y del endpoint de minter para clientes
  externos, incluyendo TLS/reverse proxy y mTLS.
- Cómo firmar o distribuir `chain-state.json` cuando haya más de una sede.

Hasta resolverlas, el formato puede reservar campos opcionales, pero no debe
introducir valores por defecto ocultos que vuelvan a mezclar topología y
comportamiento local.
