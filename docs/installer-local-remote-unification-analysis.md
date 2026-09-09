# Análisis del instalador local y remoto

## Propósito

Este documento reúne evidencia del comportamiento actual de `install.py`,
`dark_deployer/deployment.py`, `deployment-topology.json`, los Compose del
repositorio padre y los scripts de blockchain/storage. Sirve como base para
unificar en el futuro la instalación local y la instalación en servidores
Linux remotos. No cambia código ni propone todavía una migración ejecutable.

**Convención de nombres:** aquí, “inventario de despliegue” e
`deployment-topology.json` significan exactamente el mismo artefacto. Ese es
el único nombre de archivo válido; no se mantienen inventarios auxiliares.

## Conclusión ejecutiva

El instalador actual ya tiene dos mecanismos parcialmente convergentes:

1. **Instalación local por perfil**: el wizard lee `.env`, clona o actualiza
   componentes y ejecuta Docker Compose en el equipo donde corre Python.
2. **Entrega remota por topología**: `topology render` crea bundles por host y
   `deployment push/apply` los copia y ejecuta mediante SSH.

Pero no son el mismo flujo. La instalación local usa variables prefijadas
(`DEVELOPER_*`, `SANDBOX_*`, `PRODUCTION_*`), mientras que la entrega remota
usa `deployment-topology.json` y vuelve a materializar un `.env` por host.
Además, `developer` tiene presets especiales de almacenamiento y blockchain,
pero no representa realmente cinco hosts Linux; `production` sí valida cinco
hosts, aunque la aplicación remota sigue necesitando un checkout del deployer
en cada destino.

La simplificación recomendada es modelar una instalación como una colección de
**hosts y roles**, con un destino por host:

```text
topología declarativa
        ↓
plan normalizado por host
        ↓
destino local o SSH/Linux
        ↓
materialización de .env + Compose del rol
        ↓
docker compose en ese host
        ↓
verificación de red y salud
```

La diferencia local/remota debería quedar limitada al transporte de ejecución,
no a una segunda definición de servicios, redes o variables.

## Evidencia del comportamiento actual

### Entradas y perfiles

`install.py` reconoce `TYPE=developer|sandbox|production` y traduce el valor a
un prefijo (`DEVELOPER`, `SANDBOX` o `PRODUCTION`). El wizard escribe las
decisiones en `.env`. Las variables de repositorio, ramas, comandos y opciones
de cada componente están repetidas para los tres prefijos en `.env.example`.

El modo developer ofrece:

- blockchain local por defecto;
- storage `simple` o `ha` generado bajo `.generated/storage/`;
- todos los componentes en una máquina, salvo selección explícita de tiers.

Sandbox y production permiten indicar un blockchain o IPFS remoto mediante
variables como `*_BLOCKCHAIN_HOST` e `*_IPFS_HOST`; el instalador omite la
instalación local de ese tier y consume valores de handoff (`.env.integration`)
cuando corresponde.

### Selección de tiers

`_selected_tiers()` convierte `*_INSTALL_COMPONENTS` en tres booleanos:

- blockchain: `blockchain`, `blockchain-a` o `blockchain-b`;
- storage: `storage-node`;
- apps: `apps`.

El valor `all` activa los tres. La función no recibe un host de destino ni un
transporte; solo decide qué instalar en el checkout actual.

### Orden de instalación local

`install_profile()` ejecuta, según los tiers elegidos:

1. runtime blockchain local o importación de handoff RPC;
2. `dark-core-lib`;
3. Admin API;
4. runtime IPFS/storage;
5. Store API;
6. Resolver API;
7. Minter API y sus workers;
8. dashboard.

La función genera y mezcla `.env.integration` después de blockchain y Store API.
El orden es global al checkout: no existe un plan explícito de host que pueda
intercalar varios servidores ni un grafo de dependencias transportable.

### Preparación de developer

`_prepare_developer_storage_assets()` genera:

- `.generated/storage/runtime.json`;
- `.generated/storage/store-endpoints.json`;
- secretos persistentes bajo `.dark-secrets/developer/`.

`simple` genera un peer; `ha` genera dos peers con aliases, redes e identidades
distintas. Estos archivos son presets locales, no una representación de hosts
remotos. El mismo `deployment-topology.json` no gobierna actualmente el
preset developer de storage.

### Blockchain

El runtime está integrado en `blockchain/` del deployer. `BLOCKCHAIN_RUNTIME_ROLE`
selecciona `rpc`, `validators-a`, `validators-b` o `all`. Los Compose activos
son:

- `blockchain/compose.yml`: definición común de servicios Besu;
- `compose/blockchain-a.yml`: proyecto `dark-blockchain-a`;
- `compose/blockchain-b.yml`: proyecto `dark-blockchain-b`;
- `compose/apps.yml`: incluye el perfil `rpc` para el host apps;
- `blockchain/scripts/start-role.sh` y `status.sh`: operación por rol.

Developer HA simula varios hosts en una máquina mediante proyectos Compose y
`dark-backbone`. Producción debe usar redes Docker locales por servidor y
direcciones VPN para P2P. El inventario genera `BLOCKCHAIN_BACKBONE_IPS`, pero
la ejecución remota no es un agente Docker central: se ejecuta `install.py
host apply` en cada host.

### Storage/IPFS

`dark_deployer/storage.py` ofrece dos caminos:

- `developer_runtime(simple|ha)`, generado localmente;
- `production_runtime(topology.storage)`, derivado del JSON canónico.

`compose/storage.yml` y `compose/storage-production.yml` incluyen el Compose
operativo de `dark-ipfs`. Los endpoints se derivan para Store API, y cada nodo
de producción usa `storage_node_id` y `storage_data_root`. El instalador valida
secretos y crea los directorios de bind mount cuando corresponde.

### Topología de producción

`deployment-topology.example.json` es schema 1 y contiene:

- identidad, entorno, chain ID y versión Besu;
- defaults SSH y directorio remoto;
- hosts con `management_address`, `vpn_address` y rol;
- asignación de storage, política de réplicas y secretos por ruta;
- repositorios y ramas de los componentes.

La validación exige exactamente:

- un host `apps`;
- un host `blockchain-a`;
- un host `blockchain-b`;
- dos hosts `storage-node`.

También valida IPs VPN, grupos de acceso, raíces de datos, rutas absolutas de
secretos, ramas y componentes requeridos.

### Render y bundles

`topology render` produce:

```text
bundle/
├── shared/deployment-topology.json
├── shared/deployment-endpoints.json
├── shared/firewall-policy.json
├── hosts/<host-id>/.env.public
├── hosts/<host-id>/host.json
├── CHECKLIST.md
└── manifest.json
```

El bundle contiene configuración pública y rutas de secretos, nunca el
contenido de las claves. `_host_env()` deriva por host el rol de Compose,
endpoints publicados, ramas, bind addresses, RPC, chain ID y parámetros de
storage.

### Entrega remota

`deployment push` solo imprime o ejecuta un `rsync` por host. `deployment apply`
solo imprime o ejecuta un SSH que, en el servidor remoto:

```bash
cd <remote_directory>
python3 install.py host apply --config bundles/hosts/<host-id>/host.json
```

Por tanto, el modo remoto actual presupone:

- SSH operativo;
- Docker/Compose instalados en el host;
- un checkout del deployer en el directorio remoto;
- secretos privados provisionados fuera del bundle;
- el bundle disponible en el host.

El deployer no ejecuta actualmente Docker remoto mediante una API de Docker ni
mantiene un daemon central. La unidad remota es un checkout local del deployer
materializado desde un bundle.

### Aplicación del host

`host apply` valida hashes, rama, topología y secretos; copia el `.env.public`
al `.env` local y materializa `.generated/deployment-topology.json`. Después
entra en el flujo normal de `install.py`, que vuelve a resolver el perfil y
ejecuta los Compose del host.

Esto garantiza que local y remoto terminan usando el mismo instalador, pero
mantiene dos entradas distintas: wizard interactivo para local y bundle/SSH
para remoto.

## Matriz actual de ejecución

| Aspecto | Developer local | Sandbox/production local | Production remoto |
| --- | --- | --- | --- |
| Fuente principal | `.env` + presets | `.env` + handoffs | `deployment-topology.json` + bundle |
| Selección | wizard/perfil | wizard/perfil | rol del host renderizado |
| Transporte | proceso local | proceso local | SSH + rsync |
| Docker | máquina del operador | máquina del operador | Docker del host Linux |
| Compose | `apps`, `blockchain-*`, `storage` | seleccionados por tier | mismos Compose después de `host apply` |
| Redes | bridges locales + backbone HA | variables/hosts remotos | bridges locales; VPN entre hosts |
| Secretos | `.dark-secrets` o rutas `.env` | handoff/rutas externas | provisionados en cada host |
| Estado público | `.env.integration` | `.env.integration` | bundle y `.env` materializado |
| Verificación | health local | health/handoff | `host validate`, SSH y probes |

## Duplicaciones y contradicciones

### Variables repetidas por perfil

Cada componente aparece tres veces en `.env.example` con URL, rama, setup y
comandos. La mayoría de valores son iguales (`main`, mismo repositorio), pero
se mantienen tres copias que pueden divergir.

### Complejidad observable en `.env.example`

`.env.example` documenta correctamente que el instalador necesita parámetros
de ejecución, pero hoy también funciona como catálogo de repositorios,
ramas, comandos de setup, selección de tiers, endpoints remotos, secretos,
roles blockchain y ajustes de workers. Hay bloques casi paralelos para
`DEVELOPER`, `SANDBOX` y `PRODUCTION`, además de un bloque general. Esto hace
difícil saber qué debe editar un operador y qué será generado por el inventario.

Para la siguiente implementación conviene clasificar cada variable del archivo
en cuatro grupos, y eliminar progresivamente los grupos que sean derivados:

1. **Identidad del host:** perfil, host-id y rol local.
2. **Ejecución:** modo local/SSH, proyecto Compose, puertos y límites de
   desarrollo.
3. **Secretos:** solo rutas o referencias a almacenes seguros.
4. **Derivados:** ramas, URLs entre servicios, nodos, peers, redes, rutas de
   datos y comandos de componentes; deben proceder de
   `deployment-topology.json` o de un bundle generado.

Preguntas que el agente debe resolver antes de simplificar `.env.example`:

- ¿Puede un plan normalizado por host reemplazar los tres bloques prefijados?
- ¿Developer simple/HA debe ser una plantilla de inventario en vez de una
  variable especial?
- ¿Qué overrides necesita realmente un operador después de renderizar el
  inventario?
- ¿Los comandos de setup de cada repositorio pueden desaparecer al quedar
  Compose bajo control del deployer?
- ¿El mismo archivo generado puede alimentar ejecución local y SSH sin
  reescribir `.env.integration` en hosts distintos?
- ¿Qué variables deben ser obligatorias, cuáles opcionales y cuáles deben
  rechazarse si contradicen el inventario?

La meta no es eliminar `.env`, sino reducirlo a un selector de ejecución y
secretos referenciados. El inventario debe seguir siendo la única descripción
de la infraestructura.

### Infraestructura en dos modelos

El inventario define hosts, pero el wizard todavía pide o conserva variables de
hosts remotos (`*_BLOCKCHAIN_HOST`, `*_IPFS_HOST`, endpoints y enodes). Esto
permite que `.env` contradiga al bundle.

### Developer no es una topología de hosts

`DEVELOPER_STORAGE_MODE=ha` simula dos peers, pero no genera una estructura
equivalente a `apps`, `blockchain-a`, `blockchain-b`, `storage-1` y
`storage-2`. El valor `all` sigue siendo una conveniencia local, no un layout
físico.

### Compose incluidos versus Compose de componentes

El deployer posee Compose agregadores bajo `compose/`, mientras que algunos
componentes conservan Dockerfiles, scripts o Compose históricos. El instalador
usa los agregadores del padre, pero la documentación y comandos personalizados
todavía pueden sugerir ejecutar Compose desde el componente.

### Transporte mezclado con aplicación

La entrega remota crea comandos SSH como strings y luego invoca el mismo
instalador remoto. No existe una abstracción explícita de `LocalExecutor` y
`SshExecutor`; por eso la lógica de destino está repartida entre render, push,
host apply y el wizard.

### Nombres heredados

`dark_env_role` aparece como nombre interno en `_host_env()` aunque el runtime
ya está integrado en el deployer. No es un repositorio externo activo, pero el
nombre puede inducir a error en nuevos cambios.

## Modelo unificado propuesto para la siguiente fase

### Fuente única

Usar `deployment-topology.json` también para developer, con un campo de
destino por host:

```json
{
  "hosts": [
    {
      "id": "dev-apps",
      "role": "apps",
      "execution": {"mode": "local"},
      "management_address": "127.0.0.1"
    },
    {
      "id": "prod-apps",
      "role": "apps",
      "execution": {"mode": "ssh", "user": "dark", "port": 22},
      "management_address": "apps.internal.example"
    }
  ]
}
```

La forma exacta debe decidirse en la implementación; la propiedad importante
es que `local` y `ssh` sean transportes de la misma unidad de despliegue.

### Plan normalizado por host

El renderer debería producir un plan explícito:

```text
Host apps
  role: apps
  compose: apps.yml
  services: rpc, APIs, workers, dashboard
  networks: dark-apps (+ backbone solo developer)
  depends_on: blockchain RPC, storage endpoints
  executor: local | ssh
```

El mismo plan debe alimentar `plan`, `push`, `apply`, `verify` y el monitor.

### Derivación de variables

Las ramas, endpoints, nombres de proyecto, redes, puertos y rutas de datos se
derivan del inventario. `.env` debe conservar únicamente:

- identificador de despliegue/host;
- modo de ejecución local/remoto;
- opciones temporales de desarrollo;
- referencias a secretos locales.

No debería repetir listas de nodos, endpoints ni ramas por perfil.

### Ejecutores

Separar tres responsabilidades:

1. `render`: valida y genera artefactos sin ejecutar.
2. `executor-local`: ejecuta Compose en el checkout actual.
3. `executor-ssh`: copia bundle, ejecuta `host apply` y recoge resultados.

Ambos ejecutores deben recibir el mismo plan y producir el mismo contrato de
salud. La diferencia es dónde se ejecuta el proceso, no qué servicios arranca.

### Perfiles como topologías predefinidas

En lugar de tres implementaciones distintas:

- developer simple: todos los roles en un host local, un peer storage;
- developer HA: roles lógicos separados, todos con executor local;
- producción: cinco hosts, executor SSH;
- sandbox: la misma topología con direcciones y secretos de prueba.

Los perfiles serían plantillas de inventario, no ramas paralelas del instalador.

## Criterios de aceptación para una futura implementación

- Un mismo inventario puede renderizar modo local o SSH sin duplicar servicios.
- `docker compose config` funciona para cada rol generado.
- Developer HA levanta los mismos roles que producción, aunque compartan
  máquina física.
- Producción no depende de aliases Docker fuera del host apps/storage local.
- Un host remoto recibe solo su bundle, sus secretos y los artefactos de sus
  nodos.
- Cambiar una IP o ruta se hace una vez en el inventario.
- El plan muestra claramente host, rol, transporte, redes, Compose y
  dependencias antes de ejecutar.
- `verify` comprueba salud y conectividad en local y remoto con el mismo modelo.
- No se mantienen listas duplicadas de endpoints, ramas o nodos en `.env`.
- La instalación es reanudable por host y por etapa, sin repetir despliegues ya
  completados.

## Riesgos que el siguiente agente debe resolver

- Definir cómo se transporta el código de componentes al host remoto: clone en
  destino, bundle de fuentes o checkout preprovisionado.
- Evitar que `.env.integration` de un host sobrescriba datos producidos por
  otro rol.
- Hacer explícita la disponibilidad de Docker, Python y Git en hosts remotos.
- Resolver la diferencia entre redes Docker locales y VPN sin publicar aliases
  internos fuera de su host.
- Decidir si `all` permanece como alias exclusivamente developer o se elimina.
- Eliminar nombres heredados (`dark_env_role`) y comandos personalizados que
  ejecuten Compose desde componentes.
- Mantener secretos fuera de Git, bundles y logs, pero validar su presencia
  antes de iniciar cualquier contenedor.

## Archivos revisados

- `install.py`
- `dark_deployer/deployment.py`
- `dark_deployer/storage.py`
- `deployment-topology.example.json`
- `.env.example`
- `compose/apps.yml`
- `compose/blockchain-a.yml`
- `compose/blockchain-b.yml`
- `compose/storage.yml`
- `blockchain/compose.yml`
- `blockchain/scripts/start-role.sh`
- `blockchain/scripts/status.sh`
