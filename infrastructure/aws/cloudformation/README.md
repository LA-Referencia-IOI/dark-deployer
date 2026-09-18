# Perfil AWS CloudFormation: sede activa de seis hosts

`dark2-prod-aws.yaml` crea la infraestructura de la referencia
`examples/operator-inventory/dark2-prod-aws.json`. No instala dARK, no
genera wallets ni contratos, y no escribe secretos: esos pasos siguen siendo
responsabilidad del controlador y de `deploy.py`.

## Qué crea

- una VPC propia con DNS habilitado;
- una subnet pública de carga en la primera AZ para los seis hosts y una
  segunda subnet pública vacía en otra AZ, necesaria para el ALB;
- Internet Gateway y ruta pública. No hay NAT Gateway: cada EC2 recibe una
  IPv4 pública dinámica para descargar imágenes y dependencias;
- seis EC2 con IPv4 privada y pública: `apps`, `resolver`, `blockchain-a`,
  `blockchain-b`, `storage-1` y `storage-2`;
- un Launch Template común para las seis EC2, con Ubuntu 26.04 LTS ARM64,
  root gp3 de 30 GiB, IMDSv2, SSM Agent, Docker Engine, Docker Compose v2,
  Tailscale y el bootstrap idempotente del host;
- seis volúmenes EBS `gp3` cifrados, uno por host. `RetainDataVolumes` decide si
  se conservan al borrar o reemplazar el stack. El bootstrap espera el volumen, lo inicializa solo si
  está vacío, lo monta en `/srv/dark/data`, configura Docker para usar
  `/srv/dark/data/docker` como `data-root` y redirige los snapshots de
  `containerd` a `/srv/dark/data/containerd`;
- un rol de instancia limitado a `AmazonSSMManagedInstanceCore`, IMDSv2
  obligatorio y Security Groups derivados de
  `shared/firewall-suggestion.json` renderizado para el inventario;
- un ALB público, dos target groups y reglas de host para Apps y Resolver.

La administración externa inicial se realiza mediante AWS Systems Manager
Session Manager; el puerto 22 permanece cerrado desde Internet. El puerto 22
sí está permitido entre las seis EC2 mediante sus IP privadas, igual que el
resto del tráfico interno del mesh. El deployer deberá usar SSM o una
conectividad privada posterior, como Tailscale, cuando se ejecute la
instalación.

## Decisiones y límites deliberados

La plantilla es una sede única: los seis hosts y sus EBS están en la misma AZ.
El ALB usa dos AZ porque AWS lo requiere, pero esto **no** convierte los
validadores, storage o sus datos en una arquitectura tolerante a una caída de
zona. La continuidad regional o multisede se modela con el inventario
multisede y una plantilla complementaria de conectividad VPN.

La simplificación evita el coste fijo y por tráfico de un NAT Gateway. La
contrapartida es que todas las EC2 tienen IPv4 pública dinámica, por lo que los
Security Groups deben mantener cerrados Besu RPC, IPFS, Cluster, Docker y las
bases de datos. No se permite SSH desde Internet; el puerto 22 solo está
abierto entre las EC2 del mesh y los puertos públicos se limitan al ALB.

El ALB ofrece HTTP y HTTPS. Se debe proporcionar un ARN regional de ACM que
cubra `AppsHostname` y `ResolverHostname` para habilitar el listener HTTPS;
HTTP y HTTPS quedan disponibles inicialmente, sin redirect obligatorio. Rain
crea o actualiza en Route 53 los registros de ambos nombres como aliases al
ALB. No se crea ningún nombre DNS público para el Dashboard.

## Requisitos previos

1. Una AMI Ubuntu Server 26.04 LTS ARM64 o el parámetro SSM oficial de
   Canonical. El bootstrap instala Docker, Compose y SSM; la AMI debe permitir
   acceso inicial a los repositorios de Ubuntu.
2. El key pair EC2 `lareferencia-dark` disponible en `us-east-1`. La clave
   privada `lareferencia-dark.pem` debe estar en `/home/ubuntu` de la máquina
   `apps`, que será el controlador, con permisos `600`; por ejemplo:
   `chmod 600 /home/ubuntu/lareferencia-dark.pem`. Esto no abre el puerto 22
   desde Internet.
3. El usuario operativo inicial es `ubuntu`, que debe coincidir con el
   `ssh.user` del operator inventory.
4. Dos nombres DNS y un certificado ACM regional validado.

Después de acceder a `apps`, instala allí el intérprete del controlador y crea
el entorno del repositorio. CloudFormation no instala Python en los hosts:

```bash
sudo apt-get update
sudo apt-get install -y python3.14 python3.14-venv
cd /home/ubuntu/dark-deployer
python3.14 -m venv venv
venv/bin/python -m pip install -r requirements.txt -r requirements-tui.txt
```

Las demás máquinas solo necesitan la preparación base de Docker, Compose y
SSM realizada por el bootstrap.

El `UserData` prepara el host completo, pero no instala Python ni dARK, ni modifica
wallets, contratos o secretos. También deja instalado y activo el daemon de
Tailscale, pero no ejecuta `tailscale up` ni incluye auth keys. El enrolamiento
en la tailnet queda para una fase posterior mediante SSM o `deploy.py`. Los
artefactos dARK siguen siendo responsabilidad de `deploy.py`.

## Acceder a `apps` con Session Manager

El puerto 22 no está publicado hacia Internet. Desde el equipo operador instala
el plugin de AWS Session Manager (en macOS con Homebrew):

```bash
brew install --cask session-manager-plugin
session-manager-plugin
```

Obtén el ID de la instancia `apps` y abre una sesión:

También puede hacerse con el wrapper del repositorio:

```bash
infrastructure/aws/cloudformation/connect_ssm_apps.sh
```

Para volver a subir la clave privada al host `apps` mediante SSM:

```bash
infrastructure/aws/cloudformation/upload_pem_ssm_apps.sh \
  --source /path/to/lareferencia-dark.pem
```

`--source` es obligatorio. Si no se indica `--target`, la instala en
`/home/ubuntu/<nombre-de-la-PEM>`, con propietario `ubuntu` y permisos `600`. Se puede usar
`--target`, `--region`, `--profile` o `--deployment` para modificar esos
valores.

Para acceder temporalmente al Dashboard por un túnel SSM local:

```bash
infrastructure/aws/cloudformation/forward_admin_ssm_apps.sh
```

Esto descubre la IP interna del contenedor Dashboard y reenvía su puerto
`8080` a `localhost:8081` del Mac, que coincide con `APP_URL` y `ASSET_URL`
del Dashboard. Mantén la sesión abierta y accede a `http://localhost:8081`.
El puerto remoto se puede cambiar con
`--remote-port` y el local con `--local-port`.

## Operador IAM limitado al deployment

La política [`dark2-prod-aws-operator-policy.json`](dark2-prod-aws-operator-policy.json)
permite descubrir las instancias, abrir sesiones SSM, usar port forwarding y
ejecutar diagnósticos únicamente sobre recursos etiquetados con
`dARKDeployment=dark2-prod-aws`. No concede permisos para crear, eliminar o
etiquetar recursos.

Crear el grupo y asociar la política administrada desde AWS CLI:

```bash
aws iam create-group --group-name dark2-prod-aws-operators
aws iam create-policy \
  --policy-name dark2-prod-aws-operator \
  --policy-document file://infrastructure/aws/cloudformation/dark2-prod-aws-operator-policy.json
aws iam attach-group-policy \
  --group-name dark2-prod-aws-operators \
  --policy-arn arn:aws:iam::524284048780:policy/dark2-prod-aws-operator
```

Después se crea el usuario o rol operativo y se añade al grupo. No se debe
conceder a ese usuario `ec2:CreateTags`, `ec2:DeleteTags` ni permisos IAM,
porque podría modificar las etiquetas y eludir la restricción. La política
usa el identificador de cuenta actualmente configurado (`524284048780`) y la
región `us-east-1`; si se reutiliza en otra cuenta o región, deben cambiarse
los ARN correspondientes.

Acepta `--region`, `--profile` y `--deployment` si se usan valores distintos
de los del stack activo.

La forma equivalente, útil para diagnosticar el descubrimiento, es:

```bash
INSTANCE_ID=$(aws ec2 describe-instances \
  --region us-east-1 \
  --filters \
    "Name=tag:dARKDeployment,Values=dark2-prod-aws" \
    "Name=tag:dARKRole,Values=apps" \
    "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId' \
  --output text)

aws ssm start-session \
  --region us-east-1 \
  --target "$INSTANCE_ID"
```

Ya dentro de `apps`, el repositorio del deployer debe estar en
`/home/ubuntu/dark-deployer` y la clave privada en
`/home/ubuntu/lareferencia-dark.pem`:

```bash
cd /home/ubuntu/dark-deployer
chmod 600 /home/ubuntu/lareferencia-dark.pem
```

Desde esa máquina, `deploy.py` usará SSH hacia las seis máquinas mediante sus
direcciones privadas. El rol de instancia necesita
`AmazonSSMManagedInstanceCore`.

## Validar y crear

### Flujo con Rain

Rain es una capa opcional sobre CloudFormation. Usa la misma plantilla y los
mismos recursos, pero simplifica la carga de parámetros y la creación del
change set. El wrapper crea un change set por defecto y no lo ejecuta hasta
que se indique `--apply`.

Preparar una configuración local:

```bash
cp infrastructure/aws/cloudformation/dark2-prod-aws.parameters.example.yaml \
  infrastructure/aws/cloudformation/dark2-prod-aws.parameters.yaml
# Editar los valores REPLACE_* y no versionar el archivo local.
```

`ResourceNamePrefix` controla los nombres visibles de los recursos. Su valor
por defecto es `dark-prod-`, por lo que las máquinas y volúmenes se etiquetan
como `dark-prod-apps`, `dark-prod-resolver`, `dark-prod-blockchain-a-data`,
etc. El valor debe terminar en guion.

`RetainDataVolumes` controla solo los EBS de datos. Por defecto es `false`,
por lo que se borran con el stack; con `true` se conservan. Los volúmenes root
siempre se eliminan al terminar la instancia.

Crear y revisar el change set:

```bash
infrastructure/aws/cloudformation/rain-dark2-prod-aws.sh \
  --config infrastructure/aws/cloudformation/dark2-prod-aws.parameters.yaml \
  --region us-east-1 --profile dark --plan
```

Ejecutarlo después de revisar la propuesta:

```bash
infrastructure/aws/cloudformation/rain-dark2-prod-aws.sh \
  --config infrastructure/aws/cloudformation/dark2-prod-aws.parameters.yaml \
  --region us-east-1 --profile dark --apply
```

Comprobar después del despliegue que el bootstrap terminó en los seis hosts
antes de instanciar el inventario:

```bash
infrastructure/aws/cloudformation/check-bootstrap.sh \
  --region us-east-1 --profile dark
```

El bootstrap solo escribe el sello `/var/lib/dark/bootstrap-complete` si todas
sus comprobaciones pasaron; el script lo lee vía SSM Run Command en cada EC2
en ejecución con el tag `dARKDeployment` y falla si algún host no lo tiene o
no responde. Requiere en el operador `ec2:DescribeInstances`, `ssm:SendCommand`
y `ssm:GetCommandInvocation`.

Rain no reemplaza `deploy.py`: después del stack hay que recoger sus outputs,
instanciar el inventario y ejecutar `validate`, `plan` e `install`.

### Smoke stack mínimo de Apps

Para validar primero el VPC, el bootstrap Ubuntu/Docker/SSM/Tailscale, el EBS
de datos, el ALB y el alias de Minter sin crear los otros cinco hosts, usar el
mismo archivo de parámetros con el script separado:

```bash
infrastructure/aws/cloudformation/rain-dark2-prod-aws-minimal.sh \
  --config infrastructure/aws/cloudformation/dark2-prod-aws.parameters.yaml \
  --region us-east-1 --profile dark --plan
```

El stack por defecto se llama `dark2-prod-aws-minimal`; `--apply` lo ejecuta.
Después de `--apply`, verificar el sello del host único con
`check-bootstrap.sh --minimal` y las mismas credenciales.
Este perfil no permite instalar la topología dARK completa: sirve para probar
la infraestructura base y el despliegue posterior del proxy de Apps. El EBS de
datos se elimina por defecto; usar `RetainDataVolumes: 'true'` si debe
conservarse al eliminar el stack.

Primero validar localmente y contra CloudFormation:

```bash
aws cloudformation validate-template \
  --template-body file://infrastructure/aws/cloudformation/dark2-prod-aws.yaml
```

Crear un change set antes de cualquier recurso:

```bash
aws cloudformation create-change-set \
  --stack-name dark2-prod-aws \
  --change-set-name initial \
  --change-set-type CREATE \
  --template-body file://infrastructure/aws/cloudformation/dark2-prod-aws.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=ImageId,ParameterValue=/aws/service/canonical/ubuntu/server/26.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
    ParameterKey=AppsHostname,ParameterValue=minter-01.dark-pid.net \
    ParameterKey=ResolverHostname,ParameterValue=resolver-01.dark-pid.net \
    ParameterKey=AcmCertificateArn,ParameterValue=arn:aws:acm:us-east-1:ACCOUNT:certificate/REPLACE \
    ParameterKey=HostedZoneId,ParameterValue=ZREPLACE
```

Revisar y ejecutar explícitamente el change set:

```bash
aws cloudformation describe-change-set \
  --stack-name dark2-prod-aws --change-set-name initial
aws cloudformation execute-change-set \
  --stack-name dark2-prod-aws --change-set-name initial
aws cloudformation wait stack-create-complete --stack-name dark2-prod-aws
aws cloudformation describe-stacks --stack-name dark2-prod-aws \
  --query 'Stacks[0].Outputs' --output table
```

## Conectar la infraestructura con dARK

Usar el inventario de seis hosts con las IP privadas fijas declaradas por el
template para `management_address` y `addresses.aws-lan`. La administración
inicial se realiza mediante SSM; SSH requiere conectividad privada o Tailscale.
dARK usa exclusivamente la LAN privada entre servicios.

Los orígenes de los proxies deben ser exactamente los outputs `AppsUrl` y
`ResolverUrl`. Si se usa TLS en el ALB, conservar `tls.mode: external` y el
listener del proxy en 80, como ya hace el inventario de ejemplo.

En el perfil AWS activo actual no se publica Explorer ni la documentación de
Minter. La API pública del Minter queda bajo
`/api/v1/`, conservando internamente `/api/v1/`. El ALB comprueba la salud
mediante `GET /health`, que también queda publicado por el proxy de Apps.
El ALB comprueba Resolver mediante `GET /health/live`, sin depender de RPC ni
de Store.

Antes de instalar, instanciar y revisar el inventario:

```bash
venv/bin/python local-infra/instantiate-inventory.py \
  --input examples/operator-inventory/dark2-prod-aws.json \
  --output deployment-aws-active-six.json

venv/bin/python deploy.py validate --inventory deployment-aws-active-six.json
venv/bin/python deploy.py plan --inventory deployment-aws-active-six.json
venv/bin/python deploy.py render \
  --inventory deployment-aws-active-six.json --output /tmp/dark-aws-render
```

Comparar el `shared/firewall-suggestion.json` recién renderizado con los
Security Groups de la plantilla antes de ejecutar `install`.

## Destrucción y datos

La eliminación del stack elimina las instancias y la red, pero conserva los
volúmenes EBS de datos por `DeletionPolicy: Retain`. Antes de eliminar una sede
se debe respaldar también el material privado del controlador; conservar EBS no
reconstruye secretos, artefactos de cadena ni configuración del despliegue.
