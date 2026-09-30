# Linux hosts externos como destinos de `dark2-prod`

Esta guía prepara servidores Linux que no fueron creados por CloudFormation y
que se añaden como destinos SSH de
[`dark2-prod-tailscale-eight-host.json`](../examples/operator-inventory/dark2-prod-tailscale-eight-host.json).
Ese inventario amplía la deployment existente `dark2-prod`; no crea una cadena
nueva ni inscribe automáticamente los hosts en Headscale.

El controlador continúa siendo `apps` en AWS. Las claves privadas PEM viven
en ese controlador, desde donde `deploy.py` abre SSH y transfiere los bundles.
En los servidores externos solo se instala la clave pública en
`authorized_keys`.

Los servidores externos no necesitan clonar `dark-deployer` ni los repos de
componentes, ni ejecutar el `venv`: el controlador prepara y transfiere el
bundle de cada máquina por SSH/`rsync`.

## Sistema operativo, software y versiones

| Elemento | Requisito o referencia |
| --- | --- |
| Sistema operativo | Ubuntu Server 26.04 LTS es la referencia del stack AWS actual. Se recomienda la misma versión para reducir diferencias; el preflight no comprueba el release. |
| CPU/arquitectura | Linux compatible con todas las imágenes de servicios asignadas. El preflight ejecuta `uname -m`, pero no impone arquitecturas. Verifica que las imágenes de Besu, Kubo, Cluster, PostgreSQL y APIs soporten la arquitectura del host. |
| Usuario remoto | `ubuntu` y SSH TCP/22, como en el inventario de ejemplo. Se pueden cambiar en el inventario si el usuario alternativo tiene los mismos permisos. |
| Docker Engine | Instalado, activo y accesible sin `sudo` por el usuario SSH; `docker info` debe finalizar correctamente. |
| Docker Compose | Plugin Compose v2; `docker compose version` debe finalizar correctamente. |
| Tailscale | Instalado y conectado al mismo Headscale. El ejemplo necesita sus direcciones y rutas para comunicación entre sedes. Usa las direcciones reales asignadas, no los placeholders del JSON. |
| Python | Python 3.14/venv se instala en hosts AWS por su bootstrap, pero no es requisito del preflight ni del host externo. Python/deployer se ejecutan en el controlador. |
| Herramientas de sistema | `sh`, `ip`, `df`, `awk`, `grep`, `mktemp`, `mkdir`, `rmdir`, `touch` y `rsync`. `rsync` debe estar disponible en el controlador y en cada destino. |

El bootstrap AWS instala `docker.io` y `docker-compose-v2` desde los
repositorios Ubuntu y Tailscale desde el instalador upstream; las versiones
exactas no están fijadas actualmente. Por tanto, el contrato documentado es
funcional (Docker Engine y Compose v2), no una versión concreta reproducible.
Registra en cada host `docker --version`, `docker compose version` y
`tailscale version` para controlar cambios.

## CPU, memoria y discos de referencia

El inventario no declara tamaños para máquinas externas. Como referencia, el
perfil AWS asigna `c7g.large` (2 vCPU, 4 GiB) a Resolver y Storage. Es un punto
de partida del mismo perfil, no una garantía para toda carga.

| Rol externo | Root | Datos separados de referencia | Servicios principales |
| --- | ---: | ---: | --- |
| `external-observer` | 30 GiB | 60 GiB | Besu observer, Resolver API, Store API reader y proxy |
| `external-storage` | 30 GiB | 250 GiB | Kubo/IPFS e IPFS Cluster |

Los valores de datos reflejan el perfil AWS actual. Dimensiona Storage según
el volumen de objetos y la política de réplicas; deja margen y monitoriza el
espacio antes de llegar al límite.

Usa un disco separado, ext4 y montaje persistente en `/srv/dark/data`. Deja el
root para el SO y paquetes. Estructura/permisos esperados:

```text
/srv/dark                 ubuntu:ubuntu 0755   workspace y bundles
├── data                  ubuntu:ubuntu 0750   filesystem del disco de datos
│   ├── docker            root:root     0710   Docker Engine data-root
│   └── containerd         root:root     0710   snapshots de containerd
└── secrets               ubuntu:ubuntu 0700   secretos del deployer
```

Configura Docker para guardar capas y rotar logs en el disco de datos:

```json
{
  "data-root": "/srv/dark/data/docker",
  "log-driver": "local",
  "log-opts": {"max-size": "50m", "max-file": "5"}
}
```

En Ubuntu, Docker `data-root` no traslada por sí solo los snapshots de
containerd. Configura también `/var/lib/containerd` para usar
`/srv/dark/data/containerd` y garantiza que Docker espere al montaje de
`/srv/dark/data`. No dejes que el daemon arranque antes del montaje.

El preflight no crea las raíces configuradas: deben existir y ser escribibles
por el usuario SSH. Prepara y prueba los permisos después de montar el volumen:

```bash
sudo install -d -m 0755 -o ubuntu -g ubuntu /srv/dark
sudo install -d -m 0750 -o ubuntu -g ubuntu /srv/dark/data
sudo install -d -m 0700 -o ubuntu -g ubuntu /srv/dark/secrets
sudo -u ubuntu mkdir /srv/dark/.write-check && sudo -u ubuntu rmdir /srv/dark/.write-check
sudo -u ubuntu mkdir /srv/dark/data/.write-check && sudo -u ubuntu rmdir /srv/dark/data/.write-check
sudo -u ubuntu mkdir /srv/dark/secrets/.write-check && sudo -u ubuntu rmdir /srv/dark/secrets/.write-check
```

El montaje debe sobrevivir a reinicios. Verifica `/etc/fstab`, UUID y
`findmnt /srv/dark/data`; reinicia y repite las pruebas de escritura.

## SSH, PEM y usuario remoto

1. En `apps`, guarda una clave privada para cada servidor externo, por ejemplo
   `/home/ubuntu/external-observer.pem` y
   `/home/ubuntu/external-storage.pem`.
2. En `apps`, establece propietario `ubuntu:ubuntu` y modo `0600`. Las rutas
   deben coincidir con `machines.<id>.ssh.private_key_file`.
3. Instala la clave pública correspondiente en
   `/home/ubuntu/.ssh/authorized_keys` del destino. Usa modo `0700` para `.ssh`
   y `0600` para `authorized_keys`, ambos propiedad de `ubuntu`.
4. Habilita `sshd` y permite TCP/22 desde `apps` por una ruta privada segura
   (Tailscale o LAN). No abras SSH a Internet.
5. Desde `apps`, confirma el login no interactivo del deployer:

   ```bash
   ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
     -i /home/ubuntu/external-observer.pem ubuntu@<TAILSCALE_IP> true
   ```

   Repite para cada host. Si el inventario configura `known_hosts_file`, usa
   también ese archivo en la prueba.

La PEM privada se necesita únicamente en el controlador. Nunca copies claves
privadas a los hosts objetivo ni las guardes en el inventario o Git.

## Docker y permisos de ejecución

El executor no antepone `sudo` a los comandos Docker. El usuario remoto debe
pertenecer al grupo `docker` y tener el daemon activo:

```bash
sudo usermod -aG docker ubuntu
sudo systemctl enable --now docker
```

Abre una sesión SSH nueva después de añadir el grupo y comprueba:

```bash
docker info --format '{{.ID}}'
docker compose version --short
docker info --format '{{.DockerRootDir}}'
```

Al replicar el layout AWS, el último comando debe mostrar
`/srv/dark/data/docker`. El usuario debe poder escribir en las tres raíces;
la instalación normal no debería depender de `sudo` interactivo.

## Tailscale, direcciones y firewall

Cada servidor debe estar enrolado en el mismo Headscale que AWS y tener una
dirección Tailscale estable reflejada en el inventario. Ejemplo de enrolamiento:

```bash
sudo tailscale up \
  --login-server=https://vpn.dark-pid.net \
  --auth-key='<PREAUTH_KEY>' \
  --hostname='dark2-prod-external-observer' \
  --accept-dns=false
```

La pre-auth key es secreta y temporal: no la guardes en comandos persistentes,
logs ni Git. Comprueba `tailscale status`, `tailscale ip -4` y que la dirección
coincida con `addresses.tailscale` y con `management_address` si SSH usa VPN.

En el inventario de ocho hosts sustituye las IPs ilustrativas y el CIDR de la
LAN externa por valores reales. Si ambos servidores comparten LAN, confirma
que sus IPs estén configuradas localmente y que `external-lan` describa esa
subred. El tráfico AWS dentro de su sede prefiere la LAN privada; el tráfico
entre sedes usa Tailscale.

Firewall/ACL debe permitir:

- SSH TCP/22 desde `apps` a cada destino.
- HTTPS saliente TCP/443 hacia Headscale para enrolamiento/control de Tailscale.
- Rutas y puertos API/P2P seleccionados para Besu, Kubo, IPFS Cluster y APIs.
  Derívalos del inventario vigente:

  ```bash
  venv/bin/python deploy.py inventory-network-matrix \
    --inventory examples/operator-inventory/dark2-prod-tailscale-eight-host.json
  ```

El `network-preflight` comprueba que el kernel encuentre una ruta (`ip route
get`); no comprueba que el firewall permita el puerto ni que haya un proceso
escuchando. Verifica ACLs, firewall y listeners antes de instalar.

## Qué comprueba `deploy.py preflight`

El preflight remoto actual ejecuta estos controles por máquina:

| Check | Condición |
| --- | --- |
| SSH | El controlador puede ejecutar comandos remotos en modo no interactivo. |
| `docker` | `docker info --format '{{.ID}}'` devuelve código 0: daemon accesible y usuario autorizado. |
| `compose` | `docker compose version --short` devuelve código 0. |
| `architecture` | Ejecuta `uname -m`; registra el resultado, sin validar una lista de arquitecturas. |
| `disk_space` | Más de 1 GiB libre en el filesystem que contiene el padre de `data_root` (normalmente `/srv/dark`). **Limitación actual:** con `data_root=/srv/dark/data` esto mide `/srv/dark`, no el filesystem montado en `/srv/dark/data`. |
| raíces | Existen `workspace_root`, `data_root` y `secrets_root`. |
| escritura | El usuario SSH crea y borra un directorio temporal dentro de cada raíz. |
| `address:<network>` | Cada dirección declarada en `machines.<id>.addresses` aparece asignada a una interfaz local (`ip -4 addr show`). |

El preflight no comprueba por sí solo la versión de Ubuntu, Tailscale
conectado, la persistencia del montaje tras reiniciar, el `data-root` de
Docker, el espacio para crecimiento ni que los puertos remotos estén abiertos.
Se deben comprobar aparte con esta guía. En particular, verifica el espacio
del volumen de datos directamente con `df -h /srv/dark/data`; no tomes el check
`disk_space` como comprobación de capacidad de ese volumen.

Ejecuta la validación offline y luego el preflight antes de `install`:

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/dark2-prod-tailscale-eight-host.json
venv/bin/python deploy.py preflight \
  --inventory examples/operator-inventory/dark2-prod-tailscale-eight-host.json
```

Un preflight verde es necesario, pero no suficiente. Continúa con
`inventory-network-matrix`, valida conectividad/firewalls y usa los artefactos
y secretos de la deployment existente; no inicialices una cadena nueva para
añadir estos hosts.
