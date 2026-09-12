# Instalación de dARK en AWS: cinco hosts en una zona

Esta guía describe una sede dARK estándar sobre cinco instancias EC2 privadas
en la misma zona de disponibilidad. Usa el inventario operator soportado
`dark-standard-1` y conserva la misma distribución probada por Lima:

| Host | Servicios principales |
| --- | --- |
| `apps` | RPC no validador, contratos, APIs, workers, Store, dashboard y edge proxy |
| `blockchain-a` | Validadores 01 y 02, y explorer |
| `blockchain-b` | Validadores 03 y 04 |
| `storage-1` | Kubo y Cluster peer A |
| `storage-2` | Kubo y Cluster peer B |

Todas las instancias y sus volúmenes EBS deben pertenecer a la misma zona. Un
volumen EBS solo se adjunta directamente a una instancia de su misma zona y
persiste independientemente de la vida de esa instancia. Esta arquitectura
tolera la pérdida de un host de storage, pero **no** la pérdida completa de la
zona. Para continuidad ante caída de zona se necesita otro diseño y una prueba
específica; AWS recomienda varias zonas para protegerse de ese fallo.

Referencias AWS: [zonas de disponibilidad](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-regions-availability-zones.html),
[volúmenes EBS](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-volumes.html) y
[reglas de Security Groups](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html).

## 1. Decisiones previas

Definir antes de crear recursos:

- región y zona de disponibilidad;
- CIDR del VPC y subnet privada, por ejemplo `10.40.10.0/24`;
- dominio DNS y terminación TLS, si habrá acceso público;
- mecanismo para que el controlador llegue por SSH a las IP privadas;
- tamaños EC2 y EBS obtenidos de una prueba representativa;
- política de snapshots, retención, monitorización y actualización;
- si se crea una cadena nueva o se incorpora un artefacto existente.

No usar las direcciones de documentación del ejemplo de producción. El
inventario final debe contener las IP privadas reales asignadas por AWS.

## 2. Red AWS

Crear un VPC y una subnet privada en una zona concreta. Lanzar las cinco EC2 en
esa subnet, sin IP pública. Asignar IP privadas estables mediante interfaces de
red o direcciones privadas secundarias cuando la política operativa lo exija.

El deployer distingue:

- `management_address`: dirección alcanzable por SSH desde el controlador;
- `addresses.lan`: dirección privada usada por APIs entre hosts;
- `addresses.vpn`: dirección usada por Besu, Kubo y Cluster P2P.

En una sede AWS simple, la misma IP privada puede figurar como `lan` y `vpn`.
Son dominios lógicos para separar políticas, aunque compartan interfaz y CIDR.
No hacen falta `routes` ni NAT si todos los hosts comparten VPC/subnet.

El controlador puede ser una EC2 administrativa en el VPC, un runner de CI
privado o una estación conectada por VPN. Session Manager es útil para acceso
operativo, pero el ejecutor actual de dARK necesita conectividad SSH real hacia
`management_address`.

## 3. Security Groups

Preferir reglas entre Security Groups del mismo VPC, no listas de IPs de
instancias. AWS aplica la referencia al tráfico por IP privada. Una separación
práctica es:

| Grupo | Entrada mínima |
| --- | --- |
| `dark-controller` | Sin entrada requerida por dARK |
| `dark-apps` | SSH 22 desde controller; RPC 8545 desde blockchain; HTTP 8080 desde ALB o red administradora |
| `dark-blockchain` | SSH 22 desde controller; Besu P2P 30303-30307 TCP/UDP desde apps y blockchain; explorer 25000 desde apps |
| `dark-storage` | SSH 22 desde controller; Kubo API 5001 desde apps/storage; Kubo P2P 4001 TCP/UDP desde storage; Cluster API 9094 desde apps/storage; Cluster P2P 9096 TCP desde storage |

La tabla es el punto de partida. Después de renderizar, comparar estas reglas
con `shared/firewall-suggestion.json`, que es la evidencia exacta derivada del
inventario. El deployer no modifica Security Groups ni firewalls.

Si el edge proxy es público, colocar un Application Load Balancer con TLS
delante de `apps:8080` y aceptar ese puerto únicamente desde el Security Group
del balanceador. RPC, Kubo, Cluster y las APIs internas no deben exponerse a
Internet.

## 4. Instancias y almacenamiento

Usar una distribución Linux soportada por Docker Engine y Docker Compose v2.
Cada host necesita:

- usuario de despliegue con acceso SSH y permiso para ejecutar Docker;
- Docker Engine y `docker compose`;
- `git`, `curl`, espacio temporal de build y hora sincronizada;
- `/srv/dark` para workspace;
- `/srv/dark/data` para datos persistentes;
- `/srv/dark/secrets` con permisos restringidos.

Adjuntar un volumen EBS cifrado independiente a cada host para
`/srv/dark/data`. No compartir el mismo filesystem entre peers: Besu, Kubo,
Cluster, PostgreSQL y MySQL conservan estado propio. Dimensionar IOPS y
throughput después de medir. Crear snapshots con una política coherente con la
consistencia de cada servicio; un snapshot de disco aislado no reemplaza una
prueba de restauración del sistema completo.

## 5. Preparar el controlador

En el checkout de `dark-deployer`:

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

Instalar la clave SSH fuera del repositorio, crear un `known_hosts` verificado
y preparar los archivos privados. Para una cadena existente, disponer del
artefacto de roles original y del signer autorizado. Para una cadena nueva, el
instalador puede crear los inputs gestionados mediante preguntas explícitas.

## 6. Crear el inventario operator

Copiar el ejemplo:

```bash
cp examples/operator-inventory/production-five-host.json \
  deployment-aws.json
```

Reemplazar:

- `deployment.id` y label;
- CIDR de `lan` y `vpn`;
- las cinco IP privadas en `management_address` y `addresses`;
- usuario, clave y `known_hosts` SSH;
- rutas `/srv/dark`, si se usan otras;
- fuentes de secretos y artefacto de cadena;
- política de acceso público o privado.

Para una subnet `10.40.10.0/24`, una forma simple es declarar dos redes lógicas
con el mismo CIDR y colocar la misma IP privada de cada EC2 en ambas:

```json
"networks": {
  "lan": {"kind": "lan", "cidr": "10.40.10.0/24"},
  "vpn": {"kind": "vpn", "cidr": "10.40.10.0/24"}
},
"routing": {
  "application_api": "lan",
  "blockchain_p2p": "vpn",
  "storage_api": "vpn",
  "storage_p2p": "vpn"
}
```

No declarar `networking.services` en el caso normal: solo se necesita para NAT
o traducción de puertos. No declarar `routes` cuando los cinco hosts comparten
el mismo dominio enrutable.

## 7. Inspección sin cambios remotos

```bash
venv/bin/python deploy.py validate --inventory deployment-aws.json
venv/bin/python deploy.py inventory-resolve \
  --inventory deployment-aws.json --output /tmp/dark-aws-resolved.json
venv/bin/python deploy.py plan --inventory deployment-aws.json
venv/bin/python deploy.py render \
  --inventory deployment-aws.json --output /tmp/dark-aws-render
```

Revisar el inventario resuelto, placement, endpoints, puertos publicados,
`firewall-suggestion.json` y manifiesto. Estos pasos no instalan servicios.

## 8. Preflight

```bash
venv/bin/python deploy.py preflight --inventory deployment-aws.json
```

Debe comprobar SSH, Docker, Compose, arquitectura, disco, direcciones privadas
y rutas API/P2P desde los consumidores. Un preflight correcto demuestra
alcanzabilidad IP, no que un Security Group permita un servicio que todavía no
ha arrancado.

## 9. Instalación

Para una ejecución guiada:

```bash
venv/bin/python deploy.py install \
  --inventory deployment-aws.json \
  --verbose
```

Revisar cada pregunta sobre actualización de checkouts, master wallet, signer,
secretos y creación de cadena. No inicializar una cadena nueva si la sede debe
incorporarse a una cadena existente.

El orden esperado es: validadores, readiness de consenso, RPC, contratos,
storage, bases de datos, APIs/workers, dashboard y verificación final.

## 10. Verificación y aceptación

```bash
venv/bin/python deploy.py status --inventory deployment-aws.json
venv/bin/python deploy.py verify --inventory deployment-aws.json
```

Aceptar la sede solo cuando `verify` demuestre:

- identidad y avance de la cadena Besu;
- cuatro validadores y RPC funcional;
- membresía Kubo y Cluster de ambos storage;
- Store API y réplica objetivo 2;
- contratos disponibles;
- workers saludables;
- dashboard, explorer y edge proxy accesibles por la ruta prevista.

Guardar el inventario revisado, plan, manifiesto, resolución, commits de
componentes y reporte de verificación como evidencia de la instalación.

## 11. Recuperación y operación

Antes de producción, probar:

1. detener `storage-2` y confirmar una falla específica de peer;
2. recuperarlo y ejecutar `install --resume` seguido de `verify`;
3. reiniciar cada host por separado;
4. restaurar un volumen EBS desde snapshot en una instancia de reemplazo de la
   misma zona;
5. rotar una clave SSH y un secreto no blockchain;
6. actualizar un componente con plan, backup y rollback documentados.

Para cambios de código o configuración después de un fallo:

```bash
venv/bin/python deploy.py install \
  --inventory deployment-aws.json \
  --resume \
  --refresh-bundle \
  --verbose
```

`--resume` no sustituye backups ni un plan de recuperación de zona. Una sede
en una sola AZ es una unidad de operación local; la continuidad regional debe
diseñarse separadamente.
