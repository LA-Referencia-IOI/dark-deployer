# Infra local de test de dARK

Esta lab simula cuatro servidores Linux independientes mediante cuatro VMs de
Lima. Cada VM tiene su propio kernel Linux y ejecuta Docker Engine dentro de
ella:

| Servidor | IP fija | Rol |
|---|---:|---|
| `dark-blockchain` | pendiente de DHCP | blockchain/RPC |
| `dark-apps` | pendiente de DHCP | APIs, workers y dashboard |
| `dark-storage-a` | pendiente de DHCP | Kubo + IPFS Cluster |
| `dark-storage-b` | pendiente de DHCP | Kubo + IPFS Cluster |

La red `lima:shared` permite comunicación entre las VMs. Las direcciones las
asigna la red y deben descubrirse antes de generar la topología; no se deben
inventar IPs fijas. Los servicios dARK reales se instalan después en cada VM
mediante los bundles del deployer.

## Uso

Requiere macOS, Lima y Homebrew:

```bash
brew install lima
local-infra/lima-up.sh --store /Volumes/Test/dark-lab
local-infra/lima-status.sh --store /Volumes/Test/dark-lab
```

Para entrar en un servidor: `LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl shell dark-apps`.
Para simular una caída: `LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl stop dark-storage-b`;
para recuperarlo: `LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl start dark-storage-b`.

Los discos de las VMs no se borran al parar. `lima-down.sh` solo detiene las
VMs; no hay ningún comando de destrucción incluido.

El fichero `storage-topology.json` sigue siendo una plantilla conceptual; debe
actualizarse con las direcciones reales de `dark-storage-a` y `dark-storage-b`
antes de usarlo como entrada para validación/renderizado.
