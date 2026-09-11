# Infra local de test de dARK

Esta lab simula cinco servidores Linux independientes mediante cinco VMs de
Lima. Cada VM tiene su propio kernel Linux y ejecuta Docker Engine dentro de
ella:

| Servidor | IP fija | Rol |
|---|---:|---|
| `dark-blockchain-a` | pendiente de DHCP | validator01/02 |
| `dark-blockchain-b` | pendiente de DHCP | validator03/04 |
| `dark-apps` | pendiente de DHCP | APIs, workers y dashboard |
| `dark-storage-1` | pendiente de DHCP | Kubo + IPFS Cluster |
| `dark-storage-2` | pendiente de DHCP | Kubo + IPFS Cluster |

La red integrada de Lima permite la conectividad básica entre las VMs sin
`socket_vmnet` ni privilegios adicionales. Las direcciones las asigna la red y
deben descubrirse antes de generar la topología; no se deben inventar IPs
fijas. El preflight debe confirmar la conectividad entre VMs antes de instalar
los servicios dARK reales en cada VM

Para evitar instalar Ubuntu y Docker rootless cinco veces, `lima-up.sh` crea
primero una VM base temporal llamada `dark-lab-base`, espera su provisión una
sola vez y la clona para los cinco roles. La base no contiene datos dARK, IPFS,
Besu ni secretos; esos se crean después por el deployer en cada clon.
mediante los bundles del deployer.

## Uso

Requiere macOS, Lima y Homebrew:

```bash
brew install lima
local-infra/lima-up.sh --store /Volumes/Test/dark-lab
local-infra/lima-status.sh --store /Volumes/Test/dark-lab
```

If the store directory does not exist, `lima-up.sh` asks before creating it.
Use `--yes` for unattended setup.

```bash
local-infra/lima-up.sh --store /Volumes/Test/dark-lab --yes
```

Para entrar en un servidor: `LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl shell dark-apps`.
Para simular una caída: `LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl stop dark-storage-2`;
para recuperarlo: `LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl start dark-storage-2`.

Los discos de las VMs no se borran al parar. `lima-down.sh` solo detiene las
VMs; no hay ningún comando de destrucción incluido.

La topología de almacenamiento está integrada en `deployment-topology.json`;
la antigua plantilla conceptual ya no debe
actualizarse con las direcciones reales de `dark-storage-a` y `dark-storage-b`
antes de usarlo como entrada para validación/renderizado.
