# Sede AWS de seis hosts: decisiones acordadas

Este documento recoge solamente las decisiones vigentes para la primera sede
AWS de `dark2-prod-aws`.

## Registro de decisiones

Este documento es la referencia de trabajo. Cada decisión que se apruebe se
añade aquí y pasa a ser parte del diseño vigente. Las opciones descartadas no
se consideran pendientes ni deben reintroducirse en Rain, CloudFormation o el
inventario salvo nueva decisión explícita.

| Estado | Decisión |
|---|---|
| Acordada | Una única VPC y una única AZ para las seis máquinas dARK. |
| Acordada | Segunda subnet pública vacía únicamente para cumplir el requisito del ALB. |
| Acordada | Sin NAT Gateway; las máquinas usan Internet Gateway y IPv4 públicas dinámicas. |
| Acordada | El ALB publica únicamente Minter y Resolver. |
| Acordada | `minter-01.dark-pid.net` y `resolver-01.dark-pid.net` serán registros DNS gestionados por Rain en Route 53 y apuntarán al ALB. |
| Acordada | El ALB ofrecerá HTTP y HTTPS para Minter y Resolver. HTTPS usará un certificado ACM. |
| Acordada | Dashboard queda fuera del gateway y conserva `apps:8080`. |
| Acordada | `apps:8080` queda cerrado inicialmente en el Security Group. |
| Diferida | El mecanismo de acceso futuro al Dashboard se decidirá más adelante; mientras tanto `apps:8080` permanece cerrado. |
| Acordada | TLS específico para Admin queda fuera de esta primera iteración. |
| Acordada | Cada EC2 tendrá un root gp3 de 30 GiB y un volumen gp3 de datos separado. |
| Acordada | Los datos persistentes de dARK y el almacenamiento interno de Docker vivirán en el volumen de datos. |
| Acordada | El puerto SSH `22` permanecerá cerrado desde Internet, pero estará permitido entre las seis EC2 por sus IP privadas. La administración externa inicial se hará mediante AWS Systems Manager Session Manager. |
| Acordada | Las EC2 usarán Ubuntu Server `26.04 LTS` ARM64 en `us-east-1`. |
| Acordada | El Launch Template usará el key pair EC2 `lareferencia-dark`; esto no abre SSH desde Internet. |
| Acordada | Las seis EC2 de la sede podrán comunicarse entre sí en todos los puertos TCP/UDP privados mediante el Security Group del mesh dARK. |
| Acordada | El bootstrap común instalará y activará el daemon de Tailscale en las seis EC2, pero el enrolamiento en la tailnet se hará posteriormente. |
| Acordada | `RetainDataVolumes` controla los EBS de datos; por defecto es `false` y se pueden conservar con `true`. |
| Acordada | El ALB tendrá dependencia de dos AZ: una subnet contiene las EC2 y la otra permanece vacía para el ALB. |
| Acordada | Rain/CloudFormation no gestionará wallets, contratos ni secretos; esa responsabilidad pertenece al deployer. |
| Acordada | Las seis EC2 compartirán un Launch Template común para el bootstrap del sistema, Docker, SSM y almacenamiento. |
| Acordada | Habrá un stack mínimo independiente para probar VPC, `apps`, EBS, bootstrap y ALB antes de crear la sede completa. |
| Acordada | Las IPs privadas serán `10.20.10.10` a `10.20.10.15`, según el rol de cada máquina. |

Cuando se tome una nueva decisión, se actualizarán simultáneamente esta tabla,
la sección afectada y los criterios de aceptación. Hasta entonces, las
decisiones marcadas como acordadas son la base para revisar cualquier cambio.

## Topología

- Una VPC.
- Las seis máquinas dARK viven en una única Availability Zone.
- Una subnet pública de carga contiene las seis máquinas.
- Una segunda subnet pública, en otra AZ, existe únicamente porque el ALB la
  requiere. No contiene máquinas dARK.
- Todas las EC2 reciben IPv4 pública dinámica para gestión y salida a Internet.
- Todas las comunicaciones dARK entre hosts usan las IP privadas de la LAN.
- No se usa NAT Gateway.
- El ALB se conserva como entrada de Minter y Resolver, y permitirá sumar
  targets de futuras sedes sin cambiar las URLs públicas.

```text
Internet
  │
  ├── ALB
  │     ├── minter-01.dark-pid.net -> apps:80 (`/api/v1/` -> Minter; `/health` -> health)
  │     └── resolver-01.dark-pid.net -> resolver:80

Subnet de carga, una AZ
  apps  resolver  blockchain-a  blockchain-b  storage-1  storage-2
```

## Máquinas y servicios

| Host | Servicios |
|---|---|
| `apps` | `rpc01`, `admin-api`, `dashboard`, `minter-api`, `minter-worker`, migraciones y proxy de Minter |
| `resolver` | `resolver-api`, `observer01` y proxy de Resolver |
| `blockchain-a` | `validator01`, `validator02`, `validator03` |
| `blockchain-b` | `validator04`, `validator05` |
| `storage-1` | `ipfs-storage-a`, `cluster-storage-a` |
| `storage-2` | `ipfs-storage-b`, `cluster-storage-b` |

Explorer, Redis, documentación de Minter y OpenAPI no forman parte de este
perfil.

## Tipos de instancia acordados

Se usarán exclusivamente estas familias y tamaños de instancia para esta
primera sede:

| Host | Instancia | vCPU | RAM |
|---|---|---:|---:|
| `apps` | `c7g.2xlarge` | 8 | 16 GiB |
| `resolver` | `c7g.large` | 2 | 4 GiB |
| `blockchain-a` | `c7g.xlarge` | 4 | 8 GiB |
| `blockchain-b` | `c7g.xlarge` | 4 | 8 GiB |
| `storage-1` | `c7g.large` | 2 | 4 GiB |
| `storage-2` | `c7g.large` | 2 | 4 GiB |

Estas instancias son Graviton/ARM64. Las imágenes, contenedores y binarios de
los seis hosts deberán ser compatibles con `arm64`.

## Direccionamiento e inventario

La LAN de la sede usará direcciones privadas fijas; la primera propuesta es:

```text
VPC:       10.20.0.0/16
Carga:     10.20.10.0/24

apps:           10.20.10.10
resolver:       10.20.10.11
blockchain-a:   10.20.10.12
blockchain-b:   10.20.10.13
storage-1:      10.20.10.14
storage-2:      10.20.10.15
```

El inventario usará:

```text
management_address = IPv4 pública dinámica del host
addresses.aws-lan  = IPv4 privada fija del host
```

El inventario usa las IP privadas fijas como `management_address`. La
administración inicial se realiza mediante SSM; SSH solo funciona dentro de
la red privada o mediante Tailscale posteriormente. Blockchain, IPFS, Cluster,
Store, RPC y las aplicaciones se comunican por `aws-lan`.

## Acceso público

### Minter

```text
http://minter-01.dark-pid.net/api/v1/ o https://minter-01.dark-pid.net/api/v1/
  -> ALB
  -> proxy de apps:80
  -> minter-api:/api/v1/
GET /health -> minter-api:/health
```

### Resolver

```text
http://resolver-01.dark-pid.net/ o https://resolver-01.dark-pid.net/
  -> ALB
  -> proxy de resolver:80
  -> resolver-api:/api/v1/arks/
GET /health/live -> resolver-api:/health/live
```

### Dashboard administrativo

Dashboard sale del gateway de aplicaciones y se publica directamente desde su
puerto original, pero sin DNS público:

```text
apps:8080 (sin DNS público)
  -> IPv4 pública de apps:8080, si el Security Group se habilita posteriormente
  -> dashboard:8080
```

El servicio queda desplegado, pero `apps:8080/tcp` permanece cerrado en el
Security Group. Nadie puede acceder desde Internet en la primera iteración.
El acceso administrativo se decidirá posteriormente mediante una regla
controlada, VPN o túnel SSH. TLS de Admin tampoco forma parte de esta primera
implementación.

## Red y Security Groups

La tabla de rutas de la subnet de carga tiene:

```text
10.20.0.0/16   local
0.0.0.0/0      Internet Gateway
```

Reglas de entrada:

| Destino | Entrada permitida |
|---|---|
| `apps:80` | Security Group del ALB, para Minter |
| `apps:8080` | Cerrado inicialmente |
| `resolver:80` | Security Group del ALB, para Resolver |
| `*:22` público | Cerrado inicialmente; no se permite SSH directo desde Internet |
| `*:22` privado | Permitido entre las seis EC2 mediante el Security Group del mesh dARK |
| Red privada entre las seis EC2 | Todos los puertos TCP/UDP mediante el Security Group del mesh dARK |

No se exponen públicamente Besu JSON-RPC, Kubo API, IPFS Cluster API, Docker
daemon ni bases de datos. La regla completa del mesh solo aplica a las seis
EC2 de esta sede y no incluye Internet ni el ALB; sí incluye SSH entre hosts
por sus IP privadas.

Para futuras sedes, la conectividad entre redes privadas no se ampliará de
forma automática. Se añadirá una regla explícita para los CIDR o Security
Groups de las sedes que deban participar en el tráfico multisede, según los
servicios que se quieran interconectar.

## Discos iniciales

Cada host tendrá dos volúmenes EBS separados:

- root gp3 de 30 GiB para el sistema operativo y herramientas básicas;
- volumen gp3 independiente para los datos persistentes de dARK.

El volumen de datos se montará en `/srv/dark/data`. Además de los datos de
los servicios, Docker usa `/srv/dark/data/docker` como su `data-root` y
`containerd` usa `/srv/dark/data/containerd` para snapshots y capas. El
objetivo es que el crecimiento normal de imágenes, capas, cache de BuildKit,
capas escribibles y logs no consuma el root de 30 GiB.

La distribución prevista es:

```text
root gp3 (30 GiB)
└── sistema operativo y herramientas básicas

datos gp3
└── /srv/dark/data
    ├── <deployment>/...       datos persistentes de dARK
    ├── docker/                data-root de Docker
    └── containerd/            snapshots y capas del runtime
```

Los tamaños de datos acordados son:

| Host | Root gp3 | Datos gp3 |
|---|---:|---:|
| `apps` | 30 GiB | 120 GiB |
| `resolver` | 30 GiB | 60 GiB |
| `blockchain-a` | 30 GiB | 120 GiB |
| `blockchain-b` | 30 GiB | 120 GiB |
| `storage-1` | 30 GiB | 250 GiB |
| `storage-2` | 30 GiB | 250 GiB |

Los volúmenes gp3 de datos tendrán política de conservación al eliminar el
stack (`Retain`). La eliminación de la infraestructura no debe borrar la
cadena, los datos de IPFS, las bases de datos ni los datos de Docker.

Los volúmenes gp3 se amplían cuando sea necesario. La política operativa de
alertas y ampliación se definirá posteriormente; como punto de partida se
recomienda alertar al 70 %, escalar la revisión al 80 % y tratar el 90 % como
umbral crítico.

La plantilla Rain/CloudFormation configura tanto el `data-root` de Docker
como el almacenamiento de snapshots de `containerd` en el volumen de datos.

## CloudFormation, Rain y deployer

CloudFormation/Rain crea VPC, subnets, Internet Gateway, seis EC2, EBS,
Security Groups, ALB, registros Route 53 para Minter y Resolver, y el perfil
IAM necesario para Session Manager. No crea wallets, contratos ni secretos
dARK: esos artefactos y su distribución pertenecen al deployer.

## Bootstrap común de las seis EC2

Las seis máquinas usarán un único `AWS::EC2::LaunchTemplate`. El template
común contendrá:

- Ubuntu Server `26.04 LTS` ARM64;
- root gp3 de 30 GiB;
- IMDSv2 obligatorio;
- instance profile de SSM;
- `UserData` idempotente para preparar el host;
- instalación y activación de SSM Agent;
- instalación y activación del daemon de Tailscale, sin incluir claves de
  autenticación en la plantilla;
- instalación de Docker Engine y Docker Compose v2;
- configuración de Docker para usar `/srv/dark/data/docker` como `data-root`;
- rotación de logs de Docker;
- dependencia systemd para que Docker requiera el montaje de
  `/srv/dark/data`;
- comprobaciones de montaje, Docker, Compose y SSM antes de informar que el
  bootstrap terminó correctamente.

Las instancias individuales solo declararán las diferencias propias de cada
rol: tipo `c7g`, Security Groups adicionales, tags, volumen de datos y
servicios de gateway cuando correspondan.

La topología será:

```text
DarkHostLaunchTemplate
  ├── Ubuntu ARM64 + root 30 GiB
  ├── SSM + Docker + Compose
  ├── montaje /srv/dark/data
  └── Docker data-root: /srv/dark/data/docker

apps / resolver / blockchain-a / blockchain-b / storage-1 / storage-2
  ├── referencia al Launch Template
  ├── tipo de instancia propio
  ├── Security Groups propios
  └── EBS de datos propio, conservado con Retain
```

El bootstrap esperará a que aparezca el único volumen EBS adicional al root,
lo inicializará solo si está vacío, lo montará en `/srv/dark/data` y corregirá
su ownership después del montaje. No deberá crear accidentalmente un
`/srv/dark/data` alternativo en el root si el EBS no está disponible.

Docker se configurará después del montaje y antes de iniciar el servicio. La
configuración incluirá límites de tamaño y rotación para los logs de los
contenedores. El deployer recibirá el host únicamente cuando estas
comprobaciones hayan pasado.

El procedimiento de generación de wallets, contratos, secretos y artefactos
de blockchain queda fuera del bootstrap y seguirá siendo responsabilidad de
`deploy.py`.

La instalación del daemon no implica todavía `tailscale up`: el enrolamiento,
la tailnet, las ACL y los nombres DNS de Tailscale se configurarán después
mediante SSM o el deployer. Rain no almacenará ni distribuirá auth keys.

## Stack mínimo de validación

Antes de crear la sede completa se podrá desplegar
`dark2-prod-aws-minimal.yaml` mediante
`rain-dark2-prod-aws-minimal.sh`. Reutiliza el mismo archivo de parámetros
del perfil completo, pero crea solamente la VPC, las dos subnets públicas que
requiere el ALB, `apps` con su EBS de datos, el bootstrap común, el ALB y el
registro Route 53 de `minter-01.dark-pid.net`.

No crea Resolver, validadores, IPFS ni Cluster, por lo que no admite una
instalación dARK completa. Su propósito es validar el stack base, la AMI, SSM,
Docker, Tailscale, el montaje del volumen, el ALB y la ruta de Minter antes
del despliegue de seis hosts.

## DNS gestionado por Rain

Rain gestionará en Route 53 los registros públicos:

```text
minter-01.dark-pid.net A/AAAA Alias -> ALB
resolver-01.dark-pid.net A/AAAA Alias -> ALB
```

El despliegue deberá crear el registro si no existe y actualizarlo si ya
existe, siempre apuntando al ALB del stack. El Dashboard no tendrá nombre DNS
público en esta iteración.

La resolución de Minter y Resolver no dependerá de las IPv4 públicas de las
EC2. Si las instancias se reemplazan o cambian de IP, los registros seguirán
apuntando al ALB.

## TLS del ALB

El ALB tendrá dos listeners públicos:

- `80/tcp` para HTTP;
- `443/tcp` para HTTPS, terminado en el ALB mediante un certificado ACM que
  cubra `minter-01.dark-pid.net` y `resolver-01.dark-pid.net`.

HTTP y HTTPS estarán disponibles inicialmente. No se forzará un redirect de
HTTP a HTTPS en esta primera iteración; esa política podrá endurecerse después
sin cambiar los backends dARK.

## Administración inicial mediante AWS Systems Manager

La administración inicial no dependerá de SSH. Cada EC2 deberá tener:

- SSM Agent instalado y activo en la AMI ARM64;
- un instance profile con `AmazonSSMManagedInstanceCore`;
- conectividad HTTPS saliente hacia los endpoints de Systems Manager de
  `us-east-1`.

El key pair EC2 configurado será `lareferencia-dark`. Se conservará como
mecanismo de SSH privado o de contingencia entre hosts, pero el Security Group
no permitirá SSH desde Internet.

El usuario Linux de Ubuntu y del operator inventory será `ubuntu`. El nombre
`lareferencia-dark` identifica el key pair de AWS, no un usuario Linux.

El operador necesitará localmente:

- AWS CLI configurado con credenciales autorizadas para listar instancias y
  abrir sesiones SSM;
- el plugin de Session Manager para AWS CLI;
- opcionalmente `jq` para seleccionar instancias por tags.

El acceso se realizará con:

```bash
aws ssm start-session --target <instance-id> --region us-east-1
```

La conexión se identificará preferentemente por los tags del stack, por
ejemplo `dARKRole=apps`, y no por la IP pública. Tailscale queda como una
decisión posterior para administración privada y comunicación entre sedes.

## Imagen base de las EC2

La imagen será Ubuntu Server `26.04 LTS` ARM64 (`Resolute Raccoon`) en
`us-east-1`. No se fija un AMI ID estático porque los IDs son regionales y
cambian cuando Canonical publica nuevas revisiones. El parámetro oficial de
Systems Manager para resolver la AMI vigente es:

```text
/aws/service/canonical/ubuntu/server/26.04/stable/current/arm64/hvm/ebs-gp3/ami-id
```

La AMI o su bootstrap deberá dejar disponible:

- SSM Agent activo;
- Docker Engine;
- Docker Compose v2;
- soporte para ejecutar imágenes y binarios `arm64`;
- herramientas básicas de administración y diagnóstico.

El flujo es:

```text
Rain change set y despliegue
  -> outputs de IPs públicas y privadas
  -> instanciación del operator inventory
  -> deploy.py validate / plan / render / install
```

La plantilla ya elimina NAT Gateway y hace pública la subnet de carga. Quedan
por alinear el inventario y el renderer para publicar Dashboard directamente
en `8080`, manteniendo cerrado ese puerto, y los parámetros/outputs para
aplicar las IP privadas fijas y los dominios finales. La gestión de Route 53
queda definida para la siguiente etapa de Rain.

## Criterios de aceptación

- Rain crea el stack con ALB y sin NAT Gateway.
- Las seis EC2 tienen IPv4 pública dinámica y direcciones privadas de LAN.
- Minter responde en `minter-01.dark-pid.net/api/v1/` mediante ALB y conserva internamente `/api/v1/`.
- Resolver responde en `resolver-01.dark-pid.net/` mediante ALB.
- Dashboard está desplegado en `apps:8080`, pero el puerto permanece cerrado
  para acceso externo.
- Blockchain e IPFS usan únicamente IPs privadas entre hosts.
- El inventario instanciado pasa `validate`, `plan`, `render` e `install`.
