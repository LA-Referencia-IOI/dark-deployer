# Propuesta de redes multisede: LAN, VPN y P2P

Estado: contrato, resolución, renderizado y pruebas offline implementados. La
aceptación sobre dos sedes reales sigue pendiente; este documento conserva las
fases operativas y los criterios para esa validación.

## 1. Objetivo

Modelar instalaciones dARK compuestas por varias sedes sin obligar al operador
a declarar manualmente todos los pares de nodos ni a elegir una única red para
toda una familia de tráfico.

Una sede es un grupo de máquinas que comparte una LAN de baja latencia. Varias
sedes pueden comunicarse mediante una VPN u otra red enrutada. Para una misma
familia de servicios se espera que:

- dos contenedores del mismo host usen DNS de Docker;
- dos máquinas de la misma sede usen la LAN de esa sede;
- dos máquinas de sedes distintas usen una dirección VPN compartida o la LAN
  remota cuando exista enrutamiento entre sedes;
- Besu, Kubo e IPFS Cluster publiquen y consuman endpoints coherentes con esas
  decisiones;
- el resultado expandido permita auditar qué dirección usará cada origen para
  alcanzar cada destino.

La prioridad de diseño es mantener simple el operator inventory. Las relaciones
por pares se derivarán en el resolver y aparecerán sólo en el inventario v3
resuelto, donde son útiles como contrato de ejecución y evidencia.

## 2. Alcance y límites

Esta propuesta cubre:

- la noción explícita de sede y de LAN de sede;
- una VPN común o redes enrutadas entre sedes;
- selección de red por par origen-destino;
- conectividad P2P de Besu, Kubo e IPFS Cluster;
- APIs privadas que deban cruzar sedes;
- bind, anuncio, bootstrap, firewall, preflight y readiness;
- compatibilidad de inventarios existentes y de artefactos blockchain;
- cambios en ejemplos, documentación y herramientas de edición.

No pretende crear ni administrar VPC, subredes, WireGuard, túneles, gateways,
DNS, reglas de cloud o firewalls. El deployer declara, valida y comprueba una
topología que debe existir previamente.

Tampoco convierte automáticamente dos sedes en una arquitectura tolerante a la
pérdida de una sede. La distribución de validadores, el quorum QBFT, la cantidad
de réplicas y la política de asignación de Cluster siguen siendo decisiones de
disponibilidad que deben validarse por separado.

## 3. Situación actual

### 3.1 Lo que ya funciona

El modelo actual tiene buenas piezas reutilizables:

- `Machine.addresses` permite que una máquina tenga una dirección por red
  lógica;
- una red puede tener `cidr` o varios `cidrs`;
- `routes` declara alcance direccional entre dominios de red;
- el tráfico local al mismo host usa DNS de Docker;
- los servicios remotos usan exposiciones privadas declaradas;
- Besu genera listas estáticas por nodo;
- Kubo y Cluster reciben endpoints de bootstrap explícitos;
- los endpoints anunciados pueden separar dirección de bind y dirección
  alcanzable cuando existe NAT.

Estas capacidades resuelven una instalación de una sola sede y también una
instalación donde todos los participantes P2P usan una única VPN.

### 3.2 Limitación principal

El operator inventory selecciona actualmente una sola red global por clase de
tráfico:

```json
"routing": {
  "blockchain_p2p": "vpn",
  "storage_p2p": "vpn",
  "storage_api": "vpn",
  "application_api": "lan"
}
```

`deployment_v3/inventory_resolver.py` copia esas selecciones a
`infrastructure.besu.network`, `infrastructure.ipfs.network` y
`infrastructure.cluster.network`. Luego `deployment_v3/network.py`, el renderer
y las comprobaciones derivan una sola dirección por nodo dentro de esa red.

Por tanto, si `blockchain_p2p` es `vpn`, dos validadores de la misma sede también
se conectan mediante VPN. Si es la LAN de una sede, los validadores de otra sede
no pueden usar esa misma selección. Un objeto `Network` con varios CIDR amplía
un dominio enrutado, pero no expresa que el camino preferido depende de la sede
del origen y del destino.

La unidad de decisión correcta no es solamente:

```text
familia de tráfico -> red
```

sino:

```text
(familia, origen, destino) -> endpoint alcanzable del destino
```

### 3.3 Consecuencias actuales por componente

| Componente | Comportamiento actual | Limitación multisede |
| --- | --- | --- |
| Besu | Cada nodo recibe `static-nodes.json`, pero las direcciones se calculan desde una red global. | No puede usar LAN dentro de la sede y VPN fuera de ella simultáneamente. |
| Kubo | El entrypoint configura un solo `Addresses.AppendAnnounce` y los bootstraps remotos usan una red global. | No representa el conjunto LAN+VPN ni el endpoint preferido por par. |
| IPFS Cluster | El entrypoint configura un solo `announce_multiaddress`; los bootstraps usan una red global. | Pierde los múltiples caminos que Cluster admite y no selecciona por sede. |
| APIs privadas | Cada conexión y exposición tiene una sola red. | Un mismo backend no puede servir consumidores locales por LAN y remotos por VPN sin duplicación o NAT manual. |
| Preflight/readiness | Comprueba rutas y peers contra la red global. | Puede demostrar conectividad general, pero no que se usó el camino esperado. |
| Artefacto Besu | El contexto de compatibilidad incluye direcciones y puertos P2P. | Un cambio de ruteo puede parecer un cambio de identidad de la cadena. |

## 4. Modelo propuesto

### 4.1 Operator inventory v3

Se propone una nueva versión del formato compacto:

```json
{
  "format": "dark-operator-inventory",
  "format_version": 3
}
```

El loader seguirá aceptando `format_version: 2`. No se reinterpretarán los
inventarios v2: una cadena como `"blockchain_p2p": "vpn"` conservará exactamente
su significado global actual.

### 4.2 Sedes

El nuevo bloque `sites` relaciona una sede con su LAN. La VPN no se hace
implícita porque puede haber más de una:

```json
"sites": {
  "aws-eu": {
    "lan": "aws-eu-lan"
  },
  "gcp-us": {
    "lan": "gcp-us-lan"
  }
},
"networks": {
  "aws-eu-lan": {
    "kind": "lan",
    "cidr": "10.10.0.0/24"
  },
  "gcp-us-lan": {
    "kind": "lan",
    "cidr": "10.20.0.0/24"
  },
  "mesh-vpn": {
    "kind": "vpn",
    "cidr": "10.200.0.0/24"
  }
}
```

Cada máquina declara una sede y sólo las direcciones que realmente posee:

```json
"blockchain-a-eu": {
  "site": "aws-eu",
  "management_address": "REPLACE",
  "addresses": {
    "aws-eu-lan": "10.10.0.21",
    "mesh-vpn": "10.200.0.21"
  }
}
```

Las máquinas de aplicaciones que no intercambien tráfico entre sedes no
necesitan dirección VPN. En el modo de overlay directo sí la necesitan todas
las máquinas que ejecuten nodos P2P multisede.

### 4.3 Política de selección por clase de tráfico

El valor histórico de cada clase puede ser una cadena o, en v3, una política:

```json
"routing": {
  "blockchain_p2p": {
    "same_site": "site_lan",
    "cross_site": "mesh-vpn"
  },
  "storage_p2p": {
    "same_site": "site_lan",
    "cross_site": "mesh-vpn"
  },
  "storage_api": {
    "same_site": "site_lan",
    "cross_site": "mesh-vpn"
  },
  "application_api": "site_lan"
}
```

`site_lan` es un selector, no el ID de una red. Para alcanzar un destino se
resuelve a `sites.<destino.site>.lan`. Cualquier otro valor es un ID de red
declarado. Esto evita repetir nombres de LAN en cada grupo y mantiene visibles
las decisiones intersede.

Para reducir todavía más la repetición se admite un default opcional:

```json
"routing": {
  "defaults": {
    "same_site": "site_lan",
    "cross_site": "mesh-vpn"
  },
  "application_api": "site_lan"
}
```

Las clases no declaradas toman `routing.defaults`. El resultado resuelto
siempre materializa la política efectiva; no deja defaults ocultos en el
contrato v3.

### 4.4 Dos modos intersede soportables

#### Overlay directo recomendado

El destino publica su dirección de `mesh-vpn`. El origen pertenece a esa red o
tiene una ruta declarada hacia ella. Es el modo inicial recomendado porque no
depende de anunciar subredes privadas remotas y evita ambigüedad con CIDR
superpuestos.

#### LAN remota enrutada

Si las VPN de sitio enlazan VPC/LAN completas, `cross_site` puede ser
`site_lan`. El endpoint será la LAN del destino y `routes` deberá demostrar que
alguna red del origen alcanza esa LAN:

```json
"routes": [
  {"from": "aws-eu-lan", "to": "gcp-us-lan", "via": "site-vpn"},
  {"from": "gcp-us-lan", "to": "aws-eu-lan", "via": "site-vpn"}
]
```

Este modo permite que sólo los gateways tengan una interfaz VPN. Debe rechazar
CIDR LAN superpuestos y exige que el routing real y los firewalls ya estén
configurados. La primera implementación puede entregar ambos modos porque usan
el mismo selector; el overlay directo seguirá siendo el ejemplo principal.

### 4.5 Algoritmo de resolución

Para cada conexión o relación P2P entre `source` y `target`:

1. Si ambos servicios están en la misma máquina, usar el nombre DNS del servicio
   en Docker y el puerto interno.
2. Si las máquinas tienen el mismo `site`, resolver `same_site`.
3. Si tienen sitios distintos, resolver `cross_site`.
4. Si el selector es `site_lan`, elegir la LAN del sitio del destino.
5. Si el selector nombra una red, usar la dirección del destino en esa red.
6. Verificar que el destino tenga esa dirección y que pertenezca al CIDR.
7. Verificar que el origen esté en el mismo dominio o que exista una ruta
   direccional declarada.
8. Aplicar `advertise_address` o `advertise_port` sólo si existe una
   traducción explícita.
9. Materializar el endpoint final en el inventario resuelto y en la evidencia
   de resolución.

No se debe elegir silenciosamente otra red si la preferida falta. Ese fallback
haría que una máquina mal configurada funcionara por casualidad y ocultaría el
incumplimiento de la política.

## 5. Contrato v3 resuelto

El operator inventory no declarará un grafo completo. El resolver generará
relaciones dirigidas, porque el endpoint correcto depende de dónde llama el
consumidor:

```json
"peerings": {
  "blockchain": [
    {
      "from": "validator-eu-01",
      "to": "validator-eu-02",
      "network": "aws-eu-lan",
      "address": "10.10.0.22",
      "port": 30303
    },
    {
      "from": "validator-eu-01",
      "to": "validator-us-01",
      "network": "mesh-vpn",
      "address": "10.200.0.31",
      "port": 30303
    }
  ]
}
```

Se proponen tres familias: `blockchain`, `ipfs` y `cluster`. Cada arista lleva
el endpoint efectivo, no sólo la intención. El inventario v3 completo podrá
declararlas directamente; cuando proceda de operator quedarán marcadas con
provenance hacia la política que las originó.

Para APIs privadas se conserva `services.*.connections`, pero una exposición
podrá tener varios listeners:

```json
"listeners": [
  {"network": "aws-eu-lan", "port": 9094},
  {"network": "mesh-vpn", "port": 9094}
]
```

Durante la transición se seguirá aceptando `exposure` singular. Mezclar
`exposure` y `listeners` en un servicio será un error. Una conexión seguirá
resolviendo a un solo endpoint efectivo, aunque el proveedor escuche en varios.

Para P2P no conviene reutilizar la semántica HTTP de `exposure`. El renderer
derivará los bindings necesarios desde las aristas entrantes y producirá un
conjunto deduplicado de `(dirección, puerto, protocolo)`. Así un mismo puerto de
contenedor puede publicarse en la IP LAN y en la IP VPN sin duplicar el nodo.

Los campos globales `infrastructure.besu.network`,
`infrastructure.ipfs.network` e `infrastructure.cluster.network` permanecerán
como defaults heredados para inventarios existentes. Un inventario site-aware
usará `peerings`; declarar ambos con resultados incompatibles será un error.

### 5.1 Evolución concreta de los schemas

`deployment_v3/operator_schema.json` cambiará de `format_version: {"const": 2}`
a una unión discriminada:

- rama v2: schema actual sin cambios semánticos;
- rama v3: permite `sites`, exige `machines.*.site` cuando se use una política
  site-aware y amplía cada clase de `routing` con `oneOf`;
- el `oneOf` de routing acepta un ID de red heredado, el selector `site_lan` o
  `{same_site, cross_site}`;
- `routing.defaults` usa el mismo objeto de política;
- `additionalProperties: false` se mantendrá en los objetos cerrados para
  detectar errores tipográficos.

Las referencias cruzadas que JSON Schema no expresa bien —que una sede exista,
que su LAN sea de tipo `lan`, que una máquina tenga la IP necesaria o que una
ruta sea alcanzable— seguirán en la validación semántica Python de
`deployment_v3/inventory.py`.

El formato canónico no necesita convertirse en deployment v4. Se mantiene
`version: 3` y se agregan campos opcionales:

- `sites` en la raíz;
- `site` en cada máquina;
- `peerings` en la raíz;
- `listeners` como alternativa a `exposure` en servicios.

`deployment_v3/schema.json` debe describir estructuralmente esos objetos, y el
parser debe normalizar las formas heredadas antes de construir el modelo. Un
v3 sin los campos nuevos seguirá siendo válido. Si en el futuro fuera necesario
cambiar el significado de un campo existente, entonces sí correspondería una
nueva versión del contrato; esta propuesta evita hacerlo.

El resultado de `inventory-resolve` incluirá en metadata las versiones del
formato de origen, catálogo, resolver y revisión del schema canónico. Esto
permite distinguir dos resultados v3 generados por resolvers con reglas de red
diferentes sin alterar el número mayor del contrato.

## 6. Blockchain/Besu

### 6.1 Topología

Besu ya recibe un `static-nodes.json` por nodo y se ejecuta con discovery
desactivado. La modificación principal es calcular cada lista desde las aristas
`blockchain` salientes del nodo:

```text
validator-eu-01 -> validator-eu-02 = LAN de aws-eu
validator-eu-01 -> validator-us-01 = mesh-vpn
validator-us-01 -> validator-eu-01 = mesh-vpn
```

El grafo derivado por defecto será completo entre los nodos declarados. Esto
mantiene el comportamiento determinista actual y evita introducir roles de
gateway P2P en la primera entrega. Más adelante podría admitirse una topología
reducida explícita, pero no debe inferirse a partir de qué máquinas tienen VPN:
un puente accidental es difícil de verificar y puede particionar la red.

### 6.2 Identidad de cadena frente a ruteo

La identidad persistente de una cadena está formada por genesis, chain ID,
configuración QBFT, identidades y claves de los nodos. La dirección usada para
llegar a un nodo es configuración operativa mutable.

El contexto actual del artefacto incluye direcciones P2P, por lo que cambiar de
LAN a VPN puede invalidar innecesariamente el artefacto. Se propone separar:

- `chain-identity.json`: identidad inmutable y fingerprints de claves;
- `chain-peer-map.json`: endpoints derivados del inventario vigente;
- `static-nodes/<node>/static-nodes.json`: salida regenerable por nodo.

Una actualización de red regenerará solamente el mapa público y las listas
estáticas. Nunca regenerará genesis ni claves privadas. Los artefactos v2
existentes seguirán verificándose por la ruta de compatibilidad y podrán
migrarse explícitamente al nuevo manifiesto.

### 6.3 Consideraciones de disponibilidad

Conectividad no equivale a quorum. El validator set debe evaluarse por sede. Por
ejemplo, cuatro validadores repartidos 2+2 no conservan quorum si una sede queda
aislada. El planner debe mostrar, como mínimo:

- cantidad de validadores por sede;
- quorum requerido;
- quorum restante al perder cada sede;
- advertencia o error según el perfil y los acknowledgements de producción.

También debe advertir si la latencia intersede configurada o medida es
incompatible con los timeouts de QBFT, sin modificar esos timeouts
automáticamente.

## 7. Kubo/IPFS

Kubo separa direcciones de escucha, direcciones anunciadas y bootstraps. Esa
distinción debe conservarse:

- `Addresses.Swarm`: escucha en los transportes y puertos internos;
- `Addresses.Announce` o `AppendAnnounce`: conjunto de direcciones alcanzables;
- bootstrap/peering: dirección preferida para establecer una conexión concreta.

El script actual recibe un único `IPFS_ANNOUNCE_MULTIADDRESS`. Debe aceptar un
JSON array, por ejemplo:

```text
IPFS_ANNOUNCE_MULTIADDRESSES=["/ip4/10.10.0.41/tcp/4001","/ip4/10.200.0.41/tcp/4001"]
```

El renderer formará la unión deduplicada de endpoints por los que el nodo puede
ser alcanzado. Los bootstraps se renderizarán por consumidor usando la arista
resuelta: LAN para el peer de la misma sede y VPN para el remoto.

Hay una limitación importante: el anuncio libp2p es global, no diferente para
cada peer. Anunciar LAN y VPN permite ambos caminos, pero no garantiza que una
conexión ya establecida elija siempre la LAN. La política será estricta para
bootstrap, probes y configuración inicial; la preferencia sostenida debe
observarse con `ipfs swarm peers` y métricas. No se debe prometer control de ruta
que Kubo no ofrece.

`Addresses.NoAnnounce` no resuelve esta selección: es un filtro global del lado
que publica y ocultaría una dirección también a los peers que sí deben usarla.

## 8. IPFS Cluster

IPFS Cluster admite arrays para `listen_multiaddress`,
`announce_multiaddress` y direcciones de peers. El entrypoint actual reduce
`announce_multiaddress` a un solo elemento; debe aceptar el array completo sin
construir JSON mediante sustituciones frágiles.

Cambios propuestos:

- pasar `CLUSTER_ANNOUNCE_MULTIADDRESSES` como JSON validado;
- configurar listeners/bindings de LAN y VPN;
- generar `peer_addresses` o bootstraps desde las aristas `cluster`;
- mantener una identidad y un secret de Cluster únicos por peer/cluster;
- comprobar membresía y conectividad por endpoint esperado, no sólo el número
  final de peers.

El modo CRDT puede propagar estado entre sedes, pero la replicación física debe
considerarse por separado. Si `target_replicas` es menor que el número de peers,
el allocator puede no garantizar diversidad geográfica. Como primera garantía,
los ejemplos multisede usarán una cantidad de réplicas que obligue a incluir
las sedes esperadas. Una evolución posterior puede añadir una política
`min_sites` respaldada por tags/allocator de Cluster; no debe aceptarse el campo
hasta que el runtime pueda cumplirlo y verificarlo.

## 9. ¿Conviene usar DNS dividido?

No como mecanismo principal.

Un mismo nombre con respuestas LAN dentro de una sede y VPN fuera de ella puede
ser atractivo, pero introduce:

- vistas DNS diferentes por sede;
- dependencia de TTL y caches durante cambios;
- resultados distintos entre controller, host y contenedor;
- dificultad para explicar un plan antes de desplegar;
- selección poco determinista si se devuelven varias direcciones;
- diagnósticos más complejos cuando una aplicación conserva una resolución
  anterior.

DNS sí puede ser una capa opcional útil:

- nombres estables para gateways o endpoints operados fuera del deployer;
- multiaddresses `/dns4` o `/dnsaddr` de IPFS cuando el operador las elija;
- nombres de bootnodes Besu gestionados con dirección estable;
- una LAN remota enrutada donde el mismo nombre resuelve siempre a la IP LAN y
  es el routing de red, no DNS, el que decide el camino.

Si se declara `dns_name`, preflight debe resolverlo desde cada sede y comparar
el resultado con los endpoints esperados. El inventario resuelto guardará tanto
el nombre declarado como la dirección efectiva observada. La primera entrega
no dependerá de split-horizon DNS.

## 10. Validaciones nuevas

### Sedes y redes

- cada `site.lan` existe y es de tipo `lan`;
- cada máquina site-aware referencia una sede existente;
- su dirección LAN corresponde a la LAN de esa sede;
- una máquina no puede pertenecer a dos sedes;
- las LAN que deban enrutarse entre sí no tienen CIDR superpuestos;
- una red VPN nombrada en una política existe y es de tipo `vpn`;
- no se exige VPN a máquinas que no participan en tráfico intersede.

### Relaciones

- cada arista referencia servicios compatibles y máquinas existentes;
- no hay self-edge ni duplicados;
- para un par remoto existe exactamente un endpoint efectivo;
- el destino tiene dirección o `advertise_address` válida;
- el origen pertenece a la red o hay una ruta direccional;
- todas las relaciones obligatorias del grafo están presentes;
- no hay mezcla ambigua entre `peerings` y la red global heredada.

### Bind y puertos

- se permiten varios bindings del mismo host port sólo en IP host distintas;
- se rechazan colisiones en la misma `(máquina, dirección, protocolo, puerto)`;
- los endpoints anunciados tienen un binding o una traducción NAT declarada;
- se generan reglas de firewall por CIDR origen, protocolo y endpoint destino.

### Disponibilidad

- validators y storage peers se resumen por sede;
- producción alerta si todos los validators o réplicas están en una sede;
- se evalúa quorum ante pérdida de sede;
- la documentación distingue conectividad P2P de garantía de réplica multisede.

## 11. Cambios por área y componente

| Área | Archivos/componentes principales | Cambio |
| --- | --- | --- |
| Modelo canónico | `deployment_v3/model.py` | Añadir `Site`, listeners múltiples y aristas P2P dirigidas. |
| Validación v3 | `deployment_v3/schema.json`, `deployment_v3/inventory.py` | Validar sedes, políticas resueltas, bindings y compatibilidad heredada. |
| Operator | `deployment_v3/operator_schema.json`, `deployment_v3/inventory_resolver.py` | Aceptar operator v3, resolver `site_lan` y generar matrices por par. |
| Endpoints | `deployment_v3/network.py` | Sustituir la selección global por `endpoint_for(source, target, traffic_class)`. |
| Plan y provenance | planner, explain, diff y fingerprints | Mostrar sede, selector, red efectiva y endpoint por relación. |
| Renderer | `deployment_v3/render.py` y generación Compose | Publicar múltiples IP/bindings y renderizar bootstraps por consumidor. |
| Besu | `deployment_v3/artifacts.py`, comandos `chain-*` | Separar identidad de ruteo y generar static nodes por relación. |
| Kubo | `components/dark-ipfs/scripts/ipfs-entrypoint.sh` | Consumir arrays JSON de anuncios y bootstraps site-aware. |
| Cluster | `components/dark-ipfs/scripts/cluster-entrypoint.sh` | Consumir arrays de listen/announce y peers sin sustitución singular. |
| Preflight | checks de red remotos | Probar cada arista única antes del arranque, con mensajes origen-destino. |
| Readiness/verify | `deployment_v3/readiness.py`, `deployment_v3/verify.py` | Verificar peers esperados, endpoint observado y conectividad entre sedes. |
| Seguridad | sugerencias de firewall y secretos | Generar matrices mínimas; no abrir APIs administrativas de Kubo públicamente. |
| Herramientas | editor Textual y `web-wizard/` | Selector de sede por máquina y política LAN/VPN sin exponer el grafo derivado como edición normal. |
| Ejemplos/docs | ambas carpetas de ejemplos y manuales | Añadir escenario multisede equivalente en operator y v3; conservar ejemplos actuales. |

Dashboard, Explorer, Minter y Resolver no requieren cambios de código por esta
funcionalidad. Sólo cambian sus endpoints privados si alguna de sus conexiones
cruza sedes. `dark-store-api` tampoco debería decidir rutas: recibirá URLs ya
resueltas como hoy.

## 12. Compatibilidad y migración

### Inventarios existentes

- operator v2 continúa aceptado y conserva selección global;
- v3 existente sin `sites` ni `peerings` conserva el renderer actual;
- `inventory-resolve` de v2 debe producir un resultado semánticamente idéntico
  antes y después del cambio;
- no se migrarán silenciosamente los ejemplos vigentes a multisede;
- los nuevos ejemplos site-aware usarán operator v3.

Los ejemplos actuales deben permanecer en v2 durante las fases 0 a 2 para que
actúen como fixtures de regresión. Sólo después de demostrar equivalencia se
podrán publicar copias v3 de una sede o migrarlos de manera coordinada.

### Inventarios migrados

La migración recomendada será explícita:

1. asignar ID de sede a cada máquina;
2. dividir la antigua `lan` global en una LAN por sede;
3. conservar la VPN existente y sus IP;
4. sustituir sólo las clases multisede por políticas `same_site/cross_site`;
5. resolver y revisar el grafo efectivo;
6. comparar servicios, claves, chain ID, puertos y volúmenes;
7. ejecutar preflight de todas las aristas;
8. aplicar primero a un laboratorio, luego por sede de forma coordinada.

Se añadirá un comando de inspección, no destructivo:

```bash
venv/bin/python deploy.py inventory-network-matrix \
  --inventory inventory.json \
  --traffic blockchain_p2p
```

La salida mostrará `from`, `to`, `same/cross-site`, selector, red, dirección,
puerto, route y origen de la decisión.

### Artefactos y datos persistentes

- no regenerar genesis, claves Besu, identidades Kubo/Cluster ni swarm key;
- regenerar sólo configuración pública de peers y bundles;
- preservar volúmenes durante apply/recreate;
- hacer rollback restaurando bundles y peer maps anteriores;
- bloquear apply si la nueva matriz deja un nodo obligatorio sin ruta.

## 13. Orden de implementación

### Fase 0: caracterización

1. Congelar tests de resolución y rendering de todos los ejemplos actuales.
2. Guardar snapshots semánticos de operator v2 -> v3.
3. Añadir fixtures negativos de rutas, CIDR y puertos.
4. Confirmar en runtime qué direcciones anuncian actualmente Besu, Kubo y
   Cluster.

Esta fase evita que la refactorización cambie instalaciones de una sede.

### Fase 1: modelo y resolución, sin cambiar runtime

1. Introducir `Site` y operator format v3.
2. Aceptar políticas `same_site/cross_site` y mantener cadenas legacy.
3. Implementar el selector puro de endpoint por par.
4. Materializar `peerings` y provenance en v3.
5. Añadir matrix/explain/diff y todas las validaciones estáticas.

Al final de esta fase los inventarios multisede se pueden validar, planificar y
renderizar como evidencia, pero todavía no se aplican.

### Fase 2: runtime P2P

1. Renderizar bindings múltiples.
2. Cambiar static nodes de Besu a la matriz dirigida.
3. Cambiar Kubo y Cluster a arrays de anuncios.
4. Renderizar bootstraps y peer addresses por origen.
5. Separar chain identity de peer map.
6. Añadir preflight y readiness por arista.

Se integra primero Besu, luego Kubo y finalmente Cluster, manteniendo tests de
regresión después de cada componente.

### Fase 3: APIs privadas site-aware

1. Añadir `listeners` múltiples preservando `exposure` singular.
2. Resolver conexiones HTTP por sede.
3. Actualizar proxies sólo si un backend necesita cruzar sedes.
4. Extender firewall y preflight HTTP.

Esta fase puede omitirse si cada sede mantiene APIs locales y solamente P2P
cruza la VPN.

### Fase 4: ejemplos, herramientas y documentación

1. Mantener todos los ejemplos actuales como una sola sede.
2. Añadir `production-two-site-twelve-host.json` en operator v3 y v3 resuelto.
3. Representar seis máquinas por sede: apps, dos dominios blockchain, dos
   storage y resolver.
4. Añadir site/network policy al editor y wizard.
5. Actualizar README, manual, referencia de inventario, networking, AWS/Lima y
   runbooks.

### Fase 5: validación operacional

1. Test unitario del selector y de las matrices.
2. Test de integración con dos LAN aisladas y una red VPN.
3. Prueba de fallo: cortar VPN preservando tráfico dentro de cada sede.
4. Restaurar VPN y comprobar reconexión/convergencia.
5. Verificar Besu peers y progreso de bloques.
6. Verificar Kubo peers, transferencia de bloques y rutas observadas.
7. Verificar Cluster peers, estado CRDT y réplicas en ambas sedes.
8. Ejecutar el flujo ARK completo y comprobar lectura desde ambas sedes.

La simulación inicial puede usar namespaces/redes Linux o un laboratorio Lima
reducido. La aceptación final debe usar hosts reales en dos dominios de red; un
solo daemon Docker no demuestra routing multisede.

## 14. Criterios de aceptación

- todos los operator v2 e inventarios v3 actuales validan y conservan su
  topología efectiva;
- un operator v3 de dos sedes genera LAN para pares locales y VPN para pares
  remotos sin declarar manualmente cada par;
- `inventory-network-matrix` explica cada decisión;
- cada Besu recibe static nodes adecuados a su ubicación;
- cada Kubo y Cluster anuncia todos sus endpoints alcanzables;
- bootstraps y probes usan el endpoint elegido para su origen;
- una IP o route ausente falla antes de modificar hosts;
- cambiar sólo LAN/VPN no regenera identidad de cadena ni claves;
- firewall suggestions contienen únicamente orígenes y puertos requeridos;
- la pérdida y recuperación de VPN produce errores y recuperación observables;
- la verificación distingue peer count, camino esperado y replicación real;
- la documentación no presenta DNS como requisito ni como garantía de ruta.

## 15. Riesgos y decisiones explícitas

| Riesgo | Tratamiento |
| --- | --- |
| Complejidad visible en inventario | `sites` y dos selectores; el grafo queda derivado. |
| Selección libp2p no estricta después del bootstrap | Verificar y medir; documentar que el anuncio es global. |
| CIDR LAN repetidos entre proveedores | Rechazarlos en modo LAN remota; usar overlay VPN directo. |
| NAT de puertos P2P | Exigir advertise address/port explícitos y probes desde cada sede. |
| Partición de una topología puente | Usar full mesh lógico por defecto; gateways sólo como futura opción explícita. |
| Regeneración accidental de cadena | Separar identidad y peer map antes de habilitar apply multisede. |
| Réplicas concentradas en una sede | RF que cubra sedes en la primera entrega; `min_sites` sólo con soporte real del allocator. |
| Cambios simultáneos en tres runtimes | Integración secuencial Besu -> Kubo -> Cluster con fixtures legacy permanentes. |

## 16. Recomendación final

La solución más simple que describe fielmente la realidad es:

1. declarar sedes y una LAN por sede;
2. asignar cada máquina a una sola sede;
3. declarar la VPN como una red normal, sólo en las máquinas que la poseen;
4. expresar por clase de tráfico `same_site: site_lan` y
   `cross_site: mesh-vpn`;
5. derivar en el resolver una matriz explícita de endpoints por par;
6. renderizar múltiples bindings/anuncios donde el protocolo lo permita;
7. mantener DNS como opción y evidencia, no como lógica de selección;
8. separar identidad persistente de blockchain de su ruteo mutable.

Este diseño añade sólo dos conceptos operativos (`site` y política por
localidad), conserva todos los inventarios actuales y concentra la complejidad
inevitable en el resolver, que es precisamente donde puede validarse,
explicarse y probarse antes de tocar una máquina.

## 17. Referencias técnicas

- [Besu: configuración de nodos estáticos](https://docs.besu-eth.org/public-networks/how-to/connect/static-nodes)
- [Besu: bootnodes y diferencia con static nodes](https://docs.besu-eth.org/private-networks/how-to/configure/bootnodes)
- [Kubo: referencia de configuración](https://github.com/ipfs/kubo/blob/master/docs/config.md?plain=1)
- [IPFS: configuración de bootstrap peers](https://docs.ipfs.tech/how-to/modify-bootstrap-list/)
- [IPFS Cluster: bootstrap de un cluster](https://ipfscluster.io/documentation/deployment/bootstrap/)
- [IPFS Cluster: referencia de configuración](https://ipfscluster.io/documentation/reference/configuration/)
