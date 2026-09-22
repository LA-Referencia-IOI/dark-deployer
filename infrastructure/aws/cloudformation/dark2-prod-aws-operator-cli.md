# Perfil AWS CLI del operador `dark2-prod-aws`

Este procedimiento configura el usuario IAM `dark2-prod-operator`, limitado a
las instancias etiquetadas con `dARKDeployment=dark2-prod-aws`.


Introduce los valores del archivo `dark2-prod-operator-access-key.txt`:

```text
AWS Access Key ID:       <AccessKeyId>
AWS Secret Access Key:   <SecretAccessKey>
Default region name:     us-east-1
Default output format:   json
```

No incluyas las comillas JSON al copiar los valores.

## Verificar la identidad

```bash
AWS_PROFILE=dark-operator aws sts get-caller-identity
```

La respuesta debe identificar al usuario:

```text
arn:aws:iam::524284048780:user/dark2-prod-operator
```

Comprobar la instancia `apps`:

```bash
AWS_PROFILE=dark-operator aws ec2 describe-instances \
  --region us-east-1 \
  --filters \
    'Name=tag:dARKDeployment,Values=dark2-prod-aws' \
    'Name=tag:dARKRole,Values=apps' \
    'Name=instance-state-name,Values=running' \
  --query 'Reservations[].Instances[].[InstanceId,PrivateIpAddress,State.Name]' \
  --output table
```

## Conectar por Session Manager

Instala el plugin de Session Manager si aún no está disponible en macOS:

```bash
brew install --cask session-manager-plugin
```

Conectar a `apps`:

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/connect_ssm_apps.sh
```

Abrir el túnel del Dashboard:

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/forward_admin_ssm_apps.sh
```

Después abre `http://localhost:8081/login` y mantén la terminal del túnel
abierta mientras uses el Dashboard.

## Subir nuevamente la PEM

La PEM no forma parte de la access key IAM. Si es necesario copiarla a `apps`:

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/upload_pem_ssm_apps.sh \
  --source /ruta/local/lareferencia-dark.pem
```

El destino remoto por defecto es `/home/ubuntu/lareferencia-dark.pem`, con
propietario `ubuntu` y permisos `600`.

## Comprobar el bootstrap

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/check-bootstrap.sh \
  --region us-east-1
```

## Operar el deployer desde `apps`

La política IAM controla el acceso AWS del operador local. El deployer que se
ejecuta dentro de `apps` sigue usando SSH privado entre las máquinas y la PEM
instalada en `/home/ubuntu/lareferencia-dark.pem`:

```bash
cd /home/ubuntu/dark-deployer
chmod 600 /home/ubuntu/lareferencia-dark.pem
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/dark2-prod-aws.json \
  --verbose
```

## Rotación y revocación

Una access key debe revocarse si se expone. Para crear una nueva, primero
identifica las claves existentes sin imprimir secretos:

```bash
aws iam list-access-keys --user-name dark2-prod-operator
```

Después de configurar y probar la nueva clave, desactiva y elimina la antigua:

```bash
aws iam update-access-key \
  --user-name dark2-prod-operator \
  --access-key-id AKIA_OLD_KEY_ID \
  --status Inactive

aws iam delete-access-key \
  --user-name dark2-prod-operator \
  --access-key-id AKIA_OLD_KEY_ID
```

No ejecutes esos comandos sobre la clave actualmente usada hasta haber probado
la nueva.
