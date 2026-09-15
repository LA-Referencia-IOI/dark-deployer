# Revisión crítica de los ejemplos de inventario

Alcance: los 11 ficheros JSON y los 2 README de `examples/` (`deployment-v3/` y
`operator-inventory/`). Fecha: 2026-09-14, sobre `main` en `55bdb7a`.

Aviso de estado del árbol: durante la revisión el árbol de trabajo cambió (se añadían
los comandos de ciclo de vida `stop`/`start`/`restart`/`remove` sin commitear). Las
referencias de línea corresponden al árbol de trabajo tal como estaba al cerrar esta
revisión, y se revisaron contra él. Si el árbol sigue cambiando, la referencia estable
es el nombre del test o de la función, no el número de línea.

Método: lectura cruzada de cada fichero contra `deployment_v3/operator_schema.json`,
`inventory.py`, `inventory_resolver.py`, `planner.py`, `render.py`, `catalogs.py`,
`inventory_editor/templates.py` y los tests; y ejecución real de `validate`, `plan`,
`render` e `inventory-diff` con el código del repo. Los comandos ejecutados son los
de inspección offline (`validate`, `plan`, `render`, `inventory-resolve`,
`inventory-diff`); no se ejecutó nada operativo.

Cada afirmación va marcada como **CONFIRMADO** (con evidencia reproducible:
fichero y línea, salida de comando o resultado de test) o **INFERIDO** (lectura mía
sin prueba directa). Donde la evidencia solo sostiene parte de una afirmación, lo
digo.

La interfaz operativa actual incluye `deployments` para listar IDs de despliegues
gestionados y `services --deployment ID` para listar servicios y acciones válidas
por estado. Las operaciones mutables usan el selector exacto `--target service:ID`
(`stop`, `start`, `restart`, `recreate` y `remove`); `recreate --build`
reconstruye desde el contexto Compose desplegado.

Nota de entorno: el `venv/` del repo es de macOS
(`venv/bin/python3.12 → /opt/homebrew/opt/python@3.12/bin/python3.12`) y no arranca
fuera de tu equipo. Las comprobaciones se hicieron con un intérprete propio más
`jsonschema` y `pyyaml`, ejecutando `deploy.py` desde la raíz del repo. No se modificó
ningún fichero del repositorio.

---

## 1. Inventario de lo que hay

| Fichero | Formato | Perfil | Máquinas | Servicios resueltos | Proxy(ies) |
| --- | --- | --- | ---: | ---: | --- |
| `deployment-v3/local-simple.json` | v3 | — | 1 | 23 | `edge-proxy` loopback:80 |
| `deployment-v3/local-ha.json` | v3 | — | 1 | 25 | `edge-proxy` loopback:80 |
| `deployment-v3/lima-five-host.json` | v3 | — | 5 | 25 | `edge-proxy` public:80 |
| `deployment-v3/production-five-host.json` | v3 | — | 5 | 25 | `edge-proxy` public:80 |
| `deployment-v3/production-six-host.json` | v3 | — | 6 | 27 | `resolver-public` public:80 + `apps-private` private lan:80 |
| `operator-inventory/local-simple.json` | compacto | local | 1 | 23 | `gateway` loopback:80 |
| `operator-inventory/local-observer.json` | compacto | local | 1 | 24 | `gateway` loopback:80 |
| `operator-inventory/local-ha.json` | compacto | lab | 1 | 25 | `gateway` loopback:80 |
| `operator-inventory/lima-five-host.json` | compacto | production | 5 | 25 | `gateway` public:80 |
| `operator-inventory/production-five-host.json` | compacto | production | 5 | 25 | `gateway` public:80 |
| `operator-inventory/production-six-host.json` | compacto | production | 6 | 28 | `resolver-public` + `apps-private` |

Recuento de servicios y exposición obtenidos con `load_inventory`/`build_plan` del
propio repo, no a mano.

Las dos familias se corresponden 1:1 por nombre salvo `local-observer`, que solo
existe en el formato compacto. **INFERIDO**: los nombres paralelos y el hecho de que
`lima-five-host` y `production-six-host` compartan `deployment.id` entre familias
indican que se concibieron como dos vistas del mismo despliegue. Ningún documento lo
declara explícitamente, y las secciones 3 y 4 muestran que esa correspondencia no se
mantiene.

---

## 2. Estado de verificación

**CONFIRMADO**: los 11 ficheros pasan `deploy.py validate`. Avisos (esperados, no
defectos): en los ejemplos de una sola máquina aparece pérdida de quórum al perder el
host y pérdida del consenso; en los de cinco y seis máquinas aparece «Losing machine
blockchain-a removes QBFT quorum» y su simétrico, coherente con que los objetivos
declarados solo exigen `tolerate_validator_individual` (4 validadores, quórum 3, y dos
validadores por máquina).

**CONFIRMADO**: los 6 compactos resuelven, planifican y renderizan. El render del
`production-six-host` compacto produce
`machines/resolver/groups/resolver/env/resolver-api.env` con
`METADATA_STORE_API_URL=http://store-api-reader:8003` y
`DARK_RPC_URL=http://observer01:8545`, es decir, la réplica local del Store API
introducida en `55bdb7a` funciona de extremo a extremo.

**CONFIRMADO**: la suite completa pasa (59 tests) contra estos ejemplos.

**CONFIRMADO**: los cinco ficheros de `deployment-v3/` omiten la clave `images`, que
`schema.json` declara obligatoria. No son documentos v3 válidos por sí mismos; solo lo
son a través de `load_inventory`, que inyecta el bloque desde el catálogo
(`deployment_v3/inventory.py:157-162`) antes de validar. Es intencional y está
documentado en `OPERATIONS-MANUAL.md:168-176`, pero conviene saberlo: cualquier
consumidor que aplique `schema.json` directamente a estos ficheros los rechazará.

---

## 3. Las dos familias no describen el mismo despliegue

**CONFIRMADO**: `inventory-diff` entre cada par (v3 → compacto) da diferencias en
todos los casos. Resumen:

| Par | Secciones distintas | Diferencia de servicios |
| --- | --- | --- |
| local-simple | `deployment`, `blockchain` | `+gateway`, `−edge-proxy`, `minter-api` cambia |
| local-ha | `deployment`, `blockchain` | `+gateway`, `−edge-proxy`, `minter-api` cambia |
| lima-five-host | `blockchain`, `secrets` | `+gateway`, `−edge-proxy`, 4 servicios de storage y `rpc01` cambian |
| production-five-host | `deployment`, `blockchain`, `secrets` | `+gateway`, `−edge-proxy`, 4 de storage y `rpc01` cambian |
| production-six-host | `secrets` | `+store-api-reader`, `resolver-api` y `store-api` cambian |

Diferencias sistemáticas, todas **CONFIRMADO**:

1. **Identidad del proxy**: los v3 usan el servicio fijo del catálogo `edge-proxy`
   (`catalog_data/dark-platform-baseline-v1.0.json:263`); los compactos generan un
   proxy por grupo con identificador propio (`gateway`, `resolver-public`,
   `apps-private`). El resolver elimina el `edge-proxy` catalogado en cuanto el
   documento declara `proxies` (`inventory_resolver.py:383-440`).
2. **`deployment.id` y `label` distintos** en 4 de 5 pares. Solo lima y prod6
   comparten id.
3. **`blockchain.nodes`**: el v3 declara solo `role`; el resuelto añade
   `p2p_port: 30303` para todos los nodos (`inventory_resolver.py:214-218`).
4. **Máquina local**: el v3 fija `addresses.lab = 10.99.0.10`; el compacto, sin
   `addresses`, toma el primer host útil del CIDR, `10.99.0.1`
   (`inventory_resolver.py:254-256`). Ambos son válidos, pero no son el mismo
   despliegue.
5. **`minter-api`**: el v3 `local-simple`/`local-ha` le publica
   `loopback:8001`; el resuelto no le da exposición. Es una pérdida de comodidad
   para depurar en local (no se puede `curl` directo al minter), no un fallo: el
   gateway lo alcanza por DNS de Docker. No hay ninguna forma de pedir esa
   exposición desde el formato compacto.
6. **`secrets`**: en el v3 los `source` son rutas relativas literales; en el
   resuelto son rutas absolutas calculadas respecto al directorio del inventario
   (`inventory_resolver.py:558-563`).

Nada compara estas dos familias. **CONFIRMADO**: no existe ningún test que cargue un
par y afirme equivalencia. La deriva entre ambas es, por tanto, silenciosa.

---

## 4. Hallazgos

### 4.1 `examples/deployment-v3/production-six-host.json` es un volcado resuelto con rutas absolutas de tu equipo

**CONFIRMADO** en el hecho: el bloque `secrets` de ese fichero tiene siete `source`
absolutos que apuntan a
`/Users/lmatas/source/dark-deployer/examples/operator-inventory/REPLACE/secrets/...`
(líneas 291, 301, 310, 319, 325, 331 y 345). Coinciden exactamente con lo que produce
`inventory_resolver.py:563` al resolver
`examples/operator-inventory/production-six-host.json` desde su propio directorio
(`source_root: "REPLACE/secrets"` + `path` del secreto, resuelto contra
`source_path.parent`). Además el fichero lleva
`deployment.id = "dark-operator-production-six"` (línea 97), idéntico al compacto, y
nombres de proxy `apps-private` / `resolver-public`, que son los que genera el
resolver y no los del catálogo.

**INFERIDO**: el fichero se generó con `inventory-resolve --output` sobre el compacto
(y luego se le quitó `images` en `fdf0a09`). No he encontrado un comando documentado
que escriba un resuelto directamente en `examples/deployment-v3/`, así que la vía
concreta es una lectura mía; lo que es seguro es el contenido.

Consecuencias, todas verificables en el fichero: contradice
`docs/deployer.md:6-7` («written directly by the maintained templates in
`examples/deployment-v3/`»), expone rutas locales de tu equipo en un directorio que
`README.md:418` presenta como inventarios mantenidos, y los `source` apuntan a un
directorio `REPLACE/secrets/` que no existe, de modo que un `apply` fallaría al
transferir secretos con un error que no menciona el origen del problema.

### 4.2 Ese fichero y `deployment-v3/lima-five-host.json` son huérfanos

**CONFIRMADO**, por tres vías independientes:

- Son los dos únicos de los 11 sin ninguna referencia desde `tests/` (0 referencias
  cada uno; los otros nueve tienen entre 1 y 10).
- `test_all_examples_validate_and_render` recorre solo
  `("local-simple", "local-ha", "production-five-host")` de `deployment-v3`
  (`tests/test_deployment_v3.py:313-315`). Los otros dos nunca se cargan en tests.
- No están en `_TEMPLATES` (`deployment_v3/inventory_editor/templates.py:12-21`), así
  que `inventory-create --template` no los ofrece: las plantillas v3 son solo tres.

El resultado es que los dos ejemplos v3 que más han cambiado son exactamente los dos
que nada verifica.

### 4.3 Deriva real entre familias: el Store API del resolver

**CONFIRMADO**: `55bdb7a` («Add local Store API reader for resolver», nombre
actualizado posteriormente) modificó
`examples/operator-inventory/production-six-host.json` (y el resolver, render y
tests) pero no `examples/deployment-v3/production-six-host.json`. Por eso el compacto
resuelve a 28 servicios y el v3 a 27. El v3 «equivalente» no tiene el lector local que
el README describe en su propia tabla: `README.md:115` dice «Dedicated public Resolver
with local observer, RPC and Store API», y el fichero v3 carece de `store-api-reader`
(0 apariciones frente a 1 en el compacto).

**INFERIDO**: que el v3 deba actualizarse o retirarse es una decisión de diseño, no un
hecho; lo que sostengo es que hoy describe un despliegue distinto del que promete su
nombre.

### 4.4 `deployment-v3/lima-five-host.json` apunta a otro laboratorio Lima

**CONFIRMADO**: los dos `lima-five-host.json` describen laboratorios diferentes bajo el
mismo `deployment.id` (`dark-lima-five-host`) y la misma etiqueta:

| | `deployment-v3/` | `operator-inventory/` |
| --- | --- | --- |
| Clave SSH | `/Volumes/Externo/dark-lab/.lima/_config/user` | `/Volumes/Externo/dark-local/.lima/_config/user` |
| Direcciones | 192.168.105.3–.7 | 192.168.105.9–.13 |
| Puertos SSH | 56090–56127 | 50688–50734 |
| Grupo `explorer` | no existe | sí (coloca `explorer` en `blockchain-a`) |

Y el puntero de `examples/deployment-v3/README.md:24`, que dice que se genere con
`local-infra/generate-lima-inventory.py`, no es aplicable a ese fichero: el script
escribe en `examples/operator-inventory/lima-five-host.json`
(`local-infra/generate-lima-inventory.py:14`), y `docs/lima-five-host-test.md:25` lo
dice explícitamente («writes `examples/operator-inventory/lima-five-host.json`. It
deliberately does not generate the final v3 execution contract»). Ejecutar el
generador, por tanto, no refresca el fichero que el README enlaza: deja el v3 aún más
obsoleto.

Riesgo concreto: `install --inventory examples/deployment-v3/lima-five-host.json`
apunta a las VMs y la clave de `dark-lab`; `--inventory
examples/operator-inventory/lima-five-host.json` apunta a las de `dark-local`. Como el
`deployment.id` es el mismo, `.generated/deployment-v3/dark-lima-five-host/` (incluido
`private-inputs.json`) se reutiliza entre ambos, con la comprobación de huella de
`_load_private_input_state` vigente sobre material que puede ser de otro laboratorio.

### 4.5 Las direcciones IP del laboratorio están fijadas en un test

**CONFIRMADO**: `tests/test_deployment_v3.py:106-109` afirma literalmente

```
IPFS_BOOTSTRAP_ENDPOINTS=/ip4/192.168.105.12/tcp/5001@4001
IPFS_ANNOUNCE_MULTIADDRESS=/ip4/192.168.105.13/tcp/4001
CLUSTER_BOOTSTRAP_ENDPOINTS=/ip4/192.168.105.12/tcp/9094@9096
CLUSTER_ANNOUNCE_MULTIADDRESS=/ip4/192.168.105.13/tcp/9096
```

Estas aserciones son válidas hoy porque
`examples/operator-inventory/lima-five-host.json` asigna `storage-1 = .12` y
`storage-2 = .13`. Ejecuté la misma renderización sobre
`examples/deployment-v3/lima-five-host.json` (que tiene `.6` y `.7`) y las cuatro
aserciones fallan, con `IPFS_ANNOUNCE_MULTIADDRESS=/ip4/192.168.105.7/tcp/4001`. Es
decir: regenerar el lima (acción que la propia documentación marca como rutinaria,
«If the VMs were recreated or their addresses changed, run the generator again before
`validate`/`preflight`») rompe la suite salvo coincidencia exacta de direcciones.

### 4.6 El invariante «un grupo de validadores solo contiene validadores» se sortea por la vía implícita

**CONFIRMADO** en el código: en `groups_for_inventory` la comprobación de tipo por
`kind` recorre solo la lista explícita `services`
(`deployment_v3/inventory.py:429-433`). La asignación implícita posterior, que añade a
un grupo los servicios de su máquina que nadie declaró, no revalida el `kind`
(`inventory.py:449-458`).

**CONFIRMADO** en los datos: cargando `examples/deployment-v3/lima-five-host.json`,
el grupo `blockchain-a` sale con `kind=validators` y
`services=['validator01', 'validator02', 'explorer']`. El mismo invariante está
protegido por `tests/test_deployment_v3.py:303-311`, que sí lo rechaza cuando la lista
es explícita.

`Group.kind` no es solo informativo: la salida de texto de `plan` lo imprime
(`deployment_v3/cli.py:529`), el web wizard lo serializa
(`web-wizard/webwizard/graph.py:413`) y el frontal lo usa como regla de colocación
—«A proxy may only live in an application group»—
(`web-wizard/webwizard/static/canvas.js:1008-1011`). Un grupo marcado `validators`
que contiene `explorer` excluye esa máquina como candidata a proxy, que es lo
correcto para un grupo de validadores y lo incorrecto para lo que este ejemplo
realmente describe. La invariante que el test defiende no se cumple en un ejemplo
mantenido.

### 4.7 Dos datos incorrectos en la tabla de escenarios del README

**CONFIRMADO**:

- `README.md:113` lista `lima-five-host.json` con perfil `lab`. El fichero declara
  `"profile": "production"` (`examples/operator-inventory/lima-five-host.json:5`), y por
  eso trae `availability.objectives`. El perfil no es un detalle: activa la política de
  objetivos de disponibilidad (`inventory_resolver.py:98-104`).
- `README.md:110` describe `local-simple.json` como «Minimal functional path with one
  validator, RPC, and storage peer». El fichero declara cuatro validadores
  (`validator_count: 4` en un único grupo `validators`), igual que su gemelo v3
  (`validator01`–`validator04`). Si «one validator» quería decir «un grupo de
  validadores», la redacción no lo dice.

### 4.8 Features del formato compacto sin ningún ejemplo que las use

**CONFIRMADO** por barrido de claves sobre los 11 ficheros:

| Clave | Dónde aparece |
| --- | --- |
| `networking` (advertise_address / advertise_port / p2p_advertise_port) | en ninguno |
| `access` (modos `gateway`, `local-direct`) | en ninguno |
| `availability.acknowledgements` | en ninguno (solo lo usan tests) |
| `routes` de nivel superior | solo vacío en `deployment-v3/production-six-host.json` |
| `overrides.resolver`, `storage.readers` | solo en el compacto `production-six-host` |
| `overrides.explorer`, `overrides.settings` | lima, prod5, prod6 compactos |

El README de `operator-inventory` documenta `routes` y `networking.services` con un
ejemplo de código, pero no hay ningún inventario mantenido que los ejercite: la
resolución de NAT/advertise y el fallo por ruta ausente solo están cubiertos por tests
unitarios, no por un ejemplo instalable. Para la función recién añadida
(`readers`), la única cobertura de ejemplo es el compacto `production-six-host`,
que es también el único ejemplo compacto sin gemelo v3 fiel.

---

## 5. Afirmaciones de los README que sí resisten la revisión

Las verifiqué contra código y ficheros, y se sostienen:

- **Una sola fuente de verdad para imágenes**: el catálogo define las ocho imágenes y
  `inventory.py:172-178` exige exactamente ese conjunto; los ejemplos no las repiten.
  (CONFIRMADO)
- **Una máquina no puede correr dos proxies**: `inventory.py:659-664`. (CONFIRMADO)
- **minter-api, minter-postgres y los tres workers comparten máquina**:
  `inventory.py:680-683`. (CONFIRMADO)
- **Una conexión entre hosts exige red y protocolo, y el proveedor debe exponer un
  endpoint privado en esa misma red**: `inventory.py:709-726` y
  `network.py:43-57`. (CONFIRMADO)
- **`routes` solo valida rutas existentes; no configura router, VPN ni cortafuegos**:
  no hay ninguna escritura de reglas de red en el código; el render solo emite
  `firewall-suggestion.json` como sugerencia. (CONFIRMADO)
- **El proxy escucha en 80 para HTTP/TLS externo y en 443 para TLS directo**:
  `network.py:37-40`. (CONFIRMADO)
- **La clave de componente sigue siendo `dark-explorador` y su origen canónico es
  `dark-explorer.git`**: `sources.py:15-25` y el catálogo. (CONFIRMADO)
- **Los ejemplos compactos se instalan directamente, sin copia ni render previo**: el
  flujo de `install` genera los secretos gestionados cuando faltan
  (`cli.py:309-317`) y rellena los `source` de master-wallet y contract-signer
  (`cli.py:146-170`), de modo que un compacto local sin `secrets` funciona. Es cierto
  para los locales; los de producción siguen siendo «documentation-only» hasta
  reemplazar los `REPLACE`, como dice el manual. (CONFIRMADO)

Detalle menor, no un error funcional: el README compacto dice «Each proxy owns a
machine»; en el código un proxy declara un **grupo**
(`inventory_resolver.py:397-399`), y es ese grupo el que determina la máquina
(`inventory_resolver.py:439`). La restricción real es «una máquina, como máximo un
proxy».

---

## 6. Recomendaciones

1. **Sacar `production-six-host.json` de `examples/deployment-v3/`** o regenerarlo a
   mano sin rutas absolutas. Mientras siga ahí, es un ejemplo que no se puede
   compartir y que arrastra rutas de tu equipo. Si se decide que el v3 de prod6 es
   útil, debería escribirse como plantilla autorada con `source` relativos y `REPLACE`,
   y añadirse a `_TEMPLATES`.
2. **Arreglar el puntero del README**: la fila de `lima-five-host.json` en
   `examples/deployment-v3/README.md:24` debe decir que el fichero v3 es una foto
   manual y que el generador escribe en `operator-inventory/`, o retirar el fichero v3.
3. **Añadir un test que compare las familias** por nombre cuando exista par, con las
   diferencias toleradas explícitas (id de proxy, `machine` local, `p2p_port`,
   `secrets.source`). Es la única forma de que la deriva deje de ser silenciosa; hoy
   ninguna prueba cargaría el fallo del punto 4.3.
4. **Ampliar `test_all_examples_validate_and_render`** para que recorra
   `glob("examples/deployment-v3/*.json")` y `glob("examples/operator-inventory/*.json")`
   en vez de listas literales. Eso habría cubierto los dos huérfanos.
5. **Desacoplar el test de las IPs del laboratorio**: sustituir las cuatro constantes de
   `tests/test_deployment_v3.py:106-109` por un cálculo a partir de las direcciones del
   propio inventario cargado, o por un inventario de juguete en el propio test. Con el
   valor actual, regenerar el lima rompe la suite.
6. **Decidir qué hacer con el `kind` de grupo y los servicios implícitos**: o la
   asignación implícita revalida el `kind` (y entonces
   `deployment-v3/lima-five-host.json` debe declarar un grupo `explorer`, como hace el
   compacto), o el `kind` deja de prometer exclusividad. Hoy el código dice una cosa y
   un ejemplo mantenido hace la contraria.
7. **Corregir `README.md:110` y `README.md:113`** (perfil de lima y número de
   validadores de local-simple).
8. **Elegir una política para `examples/deployment-v3/`**: o pasa a ser la vista v3 de
   los mismos escenarios compactos, regenerada por una herramienta y comparada en CI
   (punto 3), o se reduce a los tres ficheros que sí están soportados y verificados. Las
   dos cosas a la vez es lo que ha producido los hallazgos 4.1 a 4.4.
