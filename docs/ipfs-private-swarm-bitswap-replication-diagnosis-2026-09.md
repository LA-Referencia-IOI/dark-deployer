# Replicación IPFS en el swarm privado `local-ha`: incidente, causa y arreglo

Incidente abierto y cerrado el 2026-09-14, en el escenario `local-ha` de `dark-deployer`.

Estado a 2026-09-14: **causa confirmada y arreglo validado** con una imagen canaria. Quedan
pendientes de aceptación y de promoción los puntos de §8.

Documentos relacionados:

- `docs/ipfs-private-swarm-bitswap-replication-review-2026-09-14.md` — revisión crítica del
  diagnóstico inicial. De ahí salió la causa que este documento cierra.
- `scripts/ipfs-bitswap-probe.sh` — la prueba discriminante, ejecutable, en modo `inplace`
  (sobre el despliegue) e `isolated` (par Kubo desechable).
- `components/dark-ipfs/scripts/ipfs-entrypoint.sh` — configuración efectiva de Kubo.

---

## 1. Resumen

IPFS Cluster asignaba la segunda réplica de un contenido pero no llegaba a materializarla: el
pin quedaba en `pinning` y acababa en `pin_error`. La causa no estaba ni en Cluster, ni en la
Store API, ni en la red: el segundo Kubo **descubría** al proveedor y lo añadía a su sesión
Bitswap, pero nunca le enviaba un WANT, así que no había stream, ni bytes, ni bloques.

El motivo es un defecto de Bitswap: registra los peers a partir de un notificador de libp2p
que se instala al arrancar, y libp2p sólo le informa de las conexiones abiertas **después**
de ese momento. En este despliegue el entrypoint deja el peer en `Bootstrap` antes de
`exec ipfs daemon`, y en la red Docker el handshake se completa en menos de un milisegundo,
así que la conexión gana la carrera prácticamente siempre: el proveedor queda invisible para
Bitswap durante toda la vida de esa conexión y nada lo corrige. Aguas arriba está arreglado
en `ipfs/boxo` PR #1201 (`bitswap/network/bsnet/ipfs_impl.go`, fusionado el 2026-08-14).

Se confirmó el 2026-09-14 con una secuencia controlada —reiniciar los dos Kubo, reproducir el
fallo, curarlo con un ciclo real de `swarm disconnect` + `swarm connect`— y se validó
aplicando como canario una imagen que contiene el arreglo: ambos sentidos transfieren, los
logs muestran los eventos de conexión de Bitswap y la replicación deja de quedarse colgada.

---

## 2. Síntoma

Después de publicar un payload y pedir el objetivo de dos réplicas, la Store API informaba
una secuencia como esta:

```text
total=1 assigned=2 pinning=1 queued=0
...
pin_error: context canceled
```

La lectura de Cluster mostraba un peer `pinned` y el otro en `pinning` o `pin_error`. El
`assigned_replicas=2` demuestra que el allocator de Cluster eligió los dos peers; no
demuestra que el segundo pudiera obtener el contenido.

Detalles que forman parte del síntoma y que costó interpretar:

- `findprovs` desde el segundo nodo devolvía el peer ID del primero: el proveedor era
  descubrible.
- La conexión libp2p estaba activa en `ipfs swarm peers` y `ipfs ping` funcionaba: no era un
  problema de red, ni de PSK, ni de gater.
- `ipfs block stat` del CID en el segundo nodo agotaba el tiempo de espera con
  `Error: context canceled`, y los contadores de Bitswap de **ambos** nodos seguían a cero.
- `partners [0]` en los dos nodos, medido mientras la lectura estaba en vuelo. Este contador
  sólo informa en esa ventana: después de una transferencia que ya terminó vale 0 aunque
  haya funcionado.

---

## 3. Cronología

| Momento (2026-09-14) | Qué se hizo | Resultado |
| --- | --- | --- |
| Mañana | Diagnóstico inicial: configuración, versiones, evidencia y descartes (§4) | Causa inmediata localizada entre la sesión Bitswap y el stream; sin causa de implementación |
| Tarde | Revisión crítica del diagnóstico | Aparece la causa candidata (registro de conexión de Bitswap), dos descartes invertidos y la prueba que faltaba |
| Tarde | Primera pasada del probe `inplace` | Falso positivo: el rebote era inválido (ver §6.2) |
| Tarde | Reinicio de los dos Kubo y rebote válido | Fallo reproducido y curado; causa confirmada (§6.3) |
| Tarde | Canario con una imagen posterior al arreglo | Transferencia en ambos sentidos, logs correctos; validado en el despliegue (§7) |
| Tarde | Centralización de versiones de runtime y regeneración del bundle | Una sola fuente de versiones en el catálogo (§7.3) |

---

## 4. El diagnóstico inicial

### 4.1 Entorno y versiones

| Elemento | Valor probado |
| --- | --- |
| Kubo inicial | `ipfs/kubo:v0.41.0` |
| Kubo probado durante el diagnóstico | `ipfs/kubo:v0.42.0` |
| IPFS Cluster | `ipfs/ipfs-cluster:v1.1.6` |
| Red | swarm privado, misma PSK para ambos Kubo |
| Routing | DHT LAN privado |
| Transporte entre peers | TCP/4001, red Docker local |

El escenario tiene dos pares Kubo/IPFS Cluster en un único host Docker: sirve para probar
replicación lógica, no para tolerar la pérdida de host.

Identificadores del entorno durante el incidente, útiles para cruzar con los logs de los
volúmenes persistentes: Kubo A `172.30.0.2`, peer ID
`12D3KooWM8jaL5sF8e3fwiBgQQePBFxRav4D1JiH9zQ7PUpsJEJR`; Kubo B `172.30.0.3`, peer ID
`12D3KooWJNUTpZoS5u6eoF6s9pofU5PW7vL8JXCKK8pUi2yJqZhN`. El bloque raw de diagnóstico del
entorno generado fue `bafkreibxbhej46mmvotmsw5bljpvobydrdwqmulvt7oq33q34gqvgt6hym`, y el de
la primera pasada del probe `bafkreidxql5jgrwph232oq5fbqxxcovfobclkv3nmsgqwm4v6qew6mto2e`.

Los dos Kubo anunciaban soporte para `/ipfs/bitswap`, `/ipfs/bitswap/1.0.0`, `1.1.0` y
`1.2.0`, y la configuración de Bitswap permanecía por defecto (`Bitswap: {}`).

### 4.2 Evidencia recogida

La conectividad entre peers funcionaba en ambos sentidos, el DHT LAN de cada nodo contenía
exactamente al otro peer, y las direcciones anunciadas eran los nombres DNS internos Docker
con TCP/4001. Para eliminar la ambigüedad de un bloque no anunciado se introdujo un bloque
raw en A y se publicó explícitamente con `ipfs routing provide`; `findprovs` desde B devolvió
el peer ID de A.

Con el proveedor visible desde B, la lectura del bloque fallaba tras el tiempo de espera y
la traza de Bitswap mostraba esta secuencia:

```text
No peers - broadcasting
Found peer for CID
Added peer to session
availability -> true
```

Después de esa secuencia no aparecía ningún stream con transferencia, A no registraba un
WANT y los contadores de los dos nodos seguían a cero. La conexión TCP/DHT permanecía
activa. El fallo quedaba situado, por tanto, después del descubrimiento del proveedor y antes
de una transferencia observable.

### 4.3 Hipótesis descartadas

| Hipótesis | Resultado |
| --- | --- |
| Cluster no asigna la réplica | Descartada: asigna dos peers. |
| Store API no pide la replicación | Descartada: Cluster recibe y conserva el pin. |
| Falta conectividad Docker/TCP | Descartada: conexión P2P y DHT LAN activos, `ipfs ping` correcto. |
| PSK o identidad incompatibles | Descartada: ambos peers forman el swarm privado. |
| CID sin proveedor anunciado | Descartada: B encuentra explícitamente a A mediante `findprovs`. |
| Bitswap deshabilitado | Descartada: ambos nodos anuncian los protocolos y usan los defaults habilitados. |
| Sólo afecta a Kubo 0.41 | Descartada como explicación suficiente: se reproduce con 0.42. |
| Falta de peering persistente | No resuelve el fallo: se probó temporalmente y B siguió sin completar la transferencia. |
| Reconectar el bootstrap tras el arranque | No resuelve el fallo y se retiró del entrypoint. Ver §6.2: ese intento no desconectaba, así que no probaba nada. |

### 4.4 Experimentos que no deben convertirse en arreglo

Durante el diagnóstico se probaron temporalmente: fijar `Bitswap.Libp2pEnabled=true` y
`Bitswap.ServerEnabled=true`; desactivar `Internal.Bitswap.BroadcastControl.Enable`;
declarar el otro nodo en `Peering.Peers`; y reconectar el bootstrap tras el arranque. Los
cambios se revirtieron de los volúmenes. No deben añadirse al inventario, al renderer ni a
los entrypoints: ninguno hizo fiable la transferencia y algunos alteran innecesariamente el
ciclo de vida del daemon.

Dos avisos operativos que salieron de aquí y siguen vigentes:

- El entrypoint no aplica un estado declarado: sólo escribe claves concretas, así que
  cualquier sondeo manual añadido a mano al `config.json` de un volumen **sobrevive a los
  reinicios**. Revertir un experimento es un paso manual que hay que hacer y verificar.
- Lo que Docker ejecuta es la copia generada del entrypoint, no la del fuente
  (`…/.generated/…/sources/components/dark-ipfs/scripts/ipfs-entrypoint.sh`). Después de
  tocar un entrypoint hay que regenerar o republicar el bundle antes de recrear contenedores.

---

## 5. La causa

### 5.1 Mecanismo

Bitswap no pregunta si hay conexión: escucha eventos. Al arrancar registra un notificador de
libp2p, y libp2p sólo reporta las conexiones abiertas **después** de ese registro. Un peer que
ya estaba conectado —porque el entrypoint lo dejó en `Bootstrap` antes de `exec ipfs daemon`—
nunca llega a registrarse. Sin registro no hay cola de mensajes por peer, y
`PeerManager.SendWants` devuelve `false` **sin escribir un solo log** cuando no hay cola: no
hay error, no hay stream, no hay WANT. El deseo queda en la lista del cliente para siempre, y
nada se autocorrige, porque mientras esa conexión viva no habrá otro evento `Connected` que
dispare el reintento.

El propio PR que lo arregla lo describe así:

> Bitswap can wait forever for a block a connected peer already has. `Start` registers a
> libp2p notifier, and libp2p only reports connections opened after that, so a peer
> connected while the node is still coming up is never recorded. Bitswap never sends it a
> want, and nothing corrects that later: the peer stays invisible for the life of the
> connection. Routing usually hides this by finding the content elsewhere; without that
> fallback the fetch just hangs.

El arreglo es mínimo: tras registrar el notificador, recorrer `host.Network().Conns()` y
marcar como conectados los peers de las conexiones ya abiertas (saltando las "limited", igual
que hace el notificador).

### 5.2 Por qué encaja con toda la evidencia

| Evidencia del incidente | Qué predice el mecanismo |
| --- | --- |
| `Found peer for CID` y `Added peer to session` | El descubrimiento DHT y la sesión funcionan: no dependen del registro de conexión de Bitswap. |
| Ningún WANT y `blocks sent/received: 0` en ambos | El deseo nunca se convierte en mensaje: es el `return false` silencioso de `SendWants`. |
| `partners [0]` con la lectura en vuelo | No existe ningún stream Bitswap registrado en ninguno de los dos sentidos. |
| Sin rechazo del resource manager y sin error de stream | No se llegó a intentar abrir el stream: el fallo está aguas arriba de `NewStream`. |
| `swarm connect` sobre una conexión viva no arregla nada | Conectar a un peer ya conectado reutiliza la conexión existente y no genera evento nuevo. |
| Se reproduce sin Cluster, sin PNET y en 0.41 y 0.42 | Es una propiedad del arranque de Bitswap: ni Cluster, ni la PSK, ni la versión dentro de esa ventana lo cambian. |
| `Peering.Peers` no lo arregla | El peering también conecta durante el arranque, antes de que el notificador importe. |
| El wrapper retirado "no lo arreglaba de forma fiable" | "No fiable" es la firma de una carrera de arranque; y ese wrapper no desconectaba, así que no forzaba conexión nueva. |

El detalle que explica la reproducibilidad: en una red Docker con RTT por debajo del
milisegundo la carrera se pierde casi siempre. Aguas arriba esto apareció como un *flake* de
CI (Kubo vía mDNS), es decir, como un fallo intermitente; aquí es determinista.

### 5.3 El arreglo aguas arriba

`ipfs/boxo` PR #1201, "fix(bitswap): see peers connected before startup", fusionado el
2026-08-14 sobre `bitswap/network/bsnet/ipfs_impl.go`. Kubo v0.42.0 fija `boxo` v0.40.0, que
todavía contiene el defecto; el salto 0.41 → 0.42 no aporta nada por sí solo. Antes de
decidir un ancla de versión hay que verificar qué release estable incluye el arreglo, porque
puede no existir todavía.

---

## 6. Cómo se confirmó

### 6.1 La prueba discriminante

Dos Kubo del mismo `boxo`, con exactamente la misma configuración en los dos casos —la del
entrypoint, sin Cluster— y una sola variable: **cuándo se establece la conexión**.

- **Orden tardío (control):** arrancar ambos demonios, esperar a que Bitswap haya arrancado y
  *entonces* conectar. Debe funcionar.
- **Orden temprano:** el mismo par, pero con el peer en `Bootstrap` antes de arrancar el
  demonio, que es lo que hace el entrypoint real. Debe fallar.

No se varía ninguna clave de configuración a propósito: así el resultado no se puede
atribuir a un perfil ni a un filtro. Está implementado en `scripts/ipfs-bitswap-probe.sh`,
que en cada matriz lanza la lectura en ambas direcciones, fuerza un rebote de conexión en
cada una y repite las lecturas, de modo que el contraste "falla → se cura" queda dentro de
una sola ejecución.

### 6.2 El falso positivo de la primera pasada

La primera ejecución sobre el despliegue terminó con código 0 y parecía confirmar la cura.
No valía: el rebote invocaba `ipfs swarm disconnect <peerID>` con el peer ID desnudo, y Kubo
exige un multiaddr (`/p2p/<peerID>`). El comando fallaba, el `connect` siguiente era un no-op
sobre la conexión todavía viva y no había ningún evento nuevo. Es exactamente el riesgo de
una reconexión ciega: parece curar y no cura. El script ahora usa `/p2p/<peerID>` y falla de
forma explícita si no logra desconectar.

La misma pasada arrastraba otros dos defectos de la prueba, ya corregidos: el bloque de
diagnóstico era idéntico en los dos nodos, así que el CID también lo era y la segunda
dirección leía un bloque que ya tenía en local (no medía ninguna transferencia); y `partners`
se muestreaba después de la lectura, fuera de la única ventana en la que informa.

### 6.3 La confirmación

Se reiniciaron únicamente los dos Kubo. Tras el arranque:

- `storage-b` → `storage-a` volvió a colgarse, con un CID nuevo, proveedor visible por
  `findprovs` y `partners [0]` durante la lectura.
- `storage-a` → `storage-b` sí transfirió (81 B).
- El `ipfs ping` falló de forma transitoria y después funcionó en ambos sentidos.

Se aplicó entonces el rebote válido en `storage-b`:

```bash
ipfs swarm disconnect /p2p/<peerID-A>
ipfs swarm connect /dns4/ipfs-storage-a/tcp/4001/p2p/<peerID-A>
```

La misma lectura, del mismo CID, devolvió inmediatamente el contenido. Los contadores
finales mostraron un bloque enviado, uno recibido y 81 B transmitidos y recibidos en cada
nodo. Con los mismos demonios y el mismo bloque, el único cambio fue un evento de conexión
efectivo: eso confirma el mecanismo.

### 6.4 Lo que la confirmación no cubre

El contraste de logs entre la versión afectada y la corregida se apoya en una medición cuyo
instrumento estaba en duda: en la pasada sobre 0.42 los contadores salieron a cero *incluido*
`Found peer for CID`, que el diagnóstico inicial sí había capturado con debug, así que pudo
fallar la captura y no el daemon. Para una incidencia aguas arriba conviene repetirlo con el
script actual, que informa del nivel de log aplicado y del volumen de log, y que puede
hacerlo sin tocar el despliegue: `isolated --image ipfs/kubo:v0.42.0` delante y detrás del
arreglo.

---

## 7. El arreglo y su validación

### 7.1 El canario

Se probó la imagen oficial de desarrollo `ipfs/kubo:master-2026-08-17-a73e8c0`
(Kubo `0.44.0-dev-a73e8c0`, digest
`sha256:c11759f51eca6af1b2e45d93125af9058b63925704821bd37477449e3f3d9411`), posterior a la
fusión de Boxo #1201.

El control aislado con dos Kubo, PSK privada, DHT y AutoConf desactivado transfirió bloques
raw en ambas direcciones cuando la conexión se hizo después del arranque: `findprovs` vio al
proveedor y las dos lecturas devolvieron el contenido en menos de 3 s. Los logs del build
mostraron `bitswap/connevtman: PeerConnected notification` y `bitswap/bsnet:
NewMessageSender`.

Al endurecer ese banco aparecieron defectos del propio arnés, ya corregidos en el script: la
imagen oficial declara `/data/ipfs` como volumen (un `repo.lock` de una pasada interrumpida
contaminaba la siguiente) y, al aislarlo con tmpfs, el script reutilizaba el peer ID y el CID
de la fase anterior.

### 7.2 Validación en el despliegue

Aplicado el canario, ambos contenedores Kubo quedaron `healthy` ejecutando
`ipfs version 0.44.0-dev`, los dos peers de Cluster responden y cada uno ve al otro, y la
configuración efectiva devuelve `AutoConf.Enabled=false` y `DNS.Resolvers=null`.

El probe `inplace`, con CIDs nuevos, terminó bien en los dos sentidos y **sin reproducir el
fallo**: antes de cualquier rebote las cuatro lecturas ya funcionaban.

```text
storage-b -> storage-a: ok; después de reconectar: ok
storage-a -> storage-b: ok; después de reconectar: ok
```

Los logs registraron en ambos nodos `PeerConnected notification` (2), `new queue for` (2),
`Found peer for CID` (1), `Added peer to session` (1) y `WANT_BLOCK` (2), sin ningún
`cannot reserve`. Los contadores finales fueron coherentes con una transferencia real: un
bloque recibido y uno enviado en cada nodo, `wantlist [0 keys]` y 80/81 B según la dirección.
Los bloques raw temporales se eliminaron al terminar.

### 7.3 Cambios en el repositorio

- **Entrypoint de Kubo**: declara explícitamente `AutoConf.Enabled=false` y vacía
  `DNS.Resolvers`, `Routing.DelegatedRouters` e `Ipns.DelegatedPublishers`; conserva el perfil
  `autoconf-off` sólo como compatibilidad con versiones que todavía lo incluyen. La familia
  nueva rechaza el endpoint público de AutoConf en un swarm privado.
- **Renderer**: fija ambos servicios Kubo a la imagen y el digest del canario y añade
  `IPFS_TELEMETRY: off` (`deployment_v3/services.py`); el valor aparece en el compose
  generado de `storage-1` y `storage-2`.
- **Versiones de runtime centralizadas**: la única fuente es `base.images` en
  `deployment_v3/catalog_data/dark-platform-baseline-v1.0.json` (Besu, Kubo, IPFS Cluster,
  PostgreSQL, MySQL, Redis, Dashboard y edge proxy). El renderer, las tareas auxiliares y la
  generación de artefactos Besu consumen ese bloque, y el probe aislado toma de allí su
  imagen Kubo por defecto. Una clave ausente hace fallar la validación en vez de aplicar un
  fallback oculto. Los inventarios compactos heredan las imágenes al resolver el catálogo y
  los full se normalizan contra el catálogo instalado; se eliminaron sus antiguos campos
  `blockchain.besu_image`.

### 7.4 Validación offline

Sintaxis POSIX de los entrypoints, `git diff --check`, la suite completa de `tests/`
(53 pruebas en la última ejecución), todos los ejemplos de inventario compactos y full, el
preflight de Docker/Compose/arm64 y el render completo del inventario `local-ha`, sin pines de
las imágenes gestionadas fuera del catálogo.

### 7.5 Qué no se hizo

Al preparar el canario no se tocó el despliegue activo: los contenedores siguieron sanos
ejecutando `ipfs/kubo:v0.42.0` hasta que el operador decidió aplicarlo. En la validación del
canario sólo se recreó lo necesario para el cambio de imagen; no se recreó ningún otro
servicio.

---

## 8. Pendientes y criterios de cierre

| Criterio de cierre del incidente | Estado | Dónde |
| --- | --- | --- |
| 1. Escribir contenido en A | Cumplido | §7.2 |
| 2. Pedir a Cluster dos réplicas | Cumplido en el canario, pendiente la aceptación prolongada | §7.2, §8.1 |
| 3. Observar dos pins `pinned` | Cumplido en el canario | §7.2 |
| 4. Leer el CID desde B | Cumplido con el probe (manual) | §6.3, §7.2 |
| 5. Verificar bloques y bytes Bitswap en ambos sentidos | Cumplido con el probe (manual) | §7.2 |
| 6. Flujo de Store API sin `context canceled` ni reintentos indefinidos | Pendiente | §8.2 |

**8.1 Aceptación prolongada.** Falta la prueba de aceptación de Cluster/Store sobre un flujo
de aplicación real (el notebook `notebooks/scenarios/local-ha-deposit-lifecycle.ipynb`) con la
imagen canaria, y repetirla varias veces: el fallo es una carrera de arranque y una sola
pasada no basta como criterio. Los criterios 4 y 5 hoy sólo los cubre el probe en modo
manual; llevarlos a la aceptación es el trabajo que queda de esta revisión.

**8.2 El segundo defecto, sin dueño todavía.** El `pin_error: context canceled` que aparece en
`peer_map` no es el `context canceled` del `timeout` de las pruebas: es otro contexto, el de
Cluster, y su procedencia nunca se estableció. El mecanismo plausible está en la Store API
(`app/backends/ipfs_cluster.py`): la promoción se pide con `POST /pins/{cid}?replication-min=2&replication-max=2`
y el cliente `httpx` del backend tiene 30 segundos por defecto, sin configuración propia en
`store-api.env`; la respuesta de ese endpoint de Cluster es un flujo que termina cuando el pin
termina. Si el segundo réplica no puede recuperar los bloques, el pin no termina, el timeout
del cliente vence, httpx cancela la petición y Cluster registra `pin_error: context canceled`.
En `_promote_cid` esa cancelación se clasifica como error de transporte, se reintenta contra
el otro endpoint y el resultado devuelto es `promotion_requested`: la Store API puede informar
éxito mientras Cluster registra un pin fallido, y el endpoint documenta "without waiting for
pins" en un camino que sí espera.

Prueba que lo decide, sin nada de Bitswap de por medio: parar el Kubo de B y promover un CID.
Si aparece `pin_error: context canceled` a los ~30 segundos, el error es del cliente de
promoción; si el pin se queda en `pinning` indefinidamente, pertenece a Cluster.

**8.3 Promoción de la imagen.** Decidir la promoción a un canal estable cuando exista una
release que incluya el arreglo. Antes de aplicarla conviene saber que Kubo v0.43 convierte la
combinación PNET con `Routing.Type=auto` en error de arranque; aquí se usa `dht`, así que no
aplica hoy, pero es relevante si alguien toca ese perfil.

**8.4 Deuda de cobertura.** `components/dark-ipfs/scripts/smoke-test.sh` no puede detectar
esta clase de fallo por construcción: añade el contenido a través del proxy local de Cluster y
lo lee con `ipfs cat` **en el mismo nodo**, así que el bloque siempre está local y el test pasa
aunque la replicación esté rota. El notebook de aceptación sí detecta el síntoma —imprime
`total=… assigned=… pinning=… queued=…` y lanza excepción si hay `error_replicas`— pero sólo
lee la vista de Cluster.

---

## 9. Lecciones y trampas operativas

- **`ipfs swarm disconnect` exige multiaddr.** Con el peer ID desnudo el comando falla, el
  `connect` siguiente es un no-op sobre la conexión viva y el resultado parece una cura. Una
  reconexión que no reconecta es peor que no intentarlo: deja una falsa confirmación.
- **Las imágenes oficiales de Kubo declaran `/data/ipfs` como volumen.** Un `repo.lock` de una
  pasada interrumpida contamina la siguiente; en pruebas desechables conviene `--tmpfs`.
- **El estado de un probe entre fases hay que reiniciarlo.** Peer ID y CID cacheados de la
  fase anterior hacen que la fase siguiente mida otra cosa.
- **`partners` de `ipfs stats bitswap` sólo informa con la lectura en vuelo.** Después de una
  transferencia terminada vale 0 aunque haya funcionado.
- **Un bloque con contenido idéntico produce el mismo CID.** Si los dos nodos publican el
  mismo bloque, la "lectura remota" del otro es una lectura local y no mide nada.
- **El refresh del bundle no poda ficheros borrados en el fuente.** `.generated/…/catalog_data/`
  conservaba `dark-standard-1.json` después de eliminarlo del repo. No es peligroso hoy
  (los catálogos se resuelven por ruta explícita), pero es la misma familia de trampa que la
  copia del entrypoint: el árbol generado conserva artefactos que ya no existen.
- **"No funciona de forma fiable" suele ser la firma de una carrera de arranque**, no una
  prueba de que el mecanismo no sea la causa. En este incidente esa lectura retrasó el
  diagnóstico.
- **Cluster puede intentar hablar con su Kubo local antes de que su API escuche.** Durante el
  diagnóstico se observó que Cluster B accedía a `ipfs-storage-b:5001` antes de que el puerto
  estuviera abierto, con `connection refused` transitorio. Es una carrera de arranque
  independiente del incidente de Bitswap, pero conviene conservarla como observación
  operativa: afecta al orden en que Cluster y Kubo deben arrancar.

---

## Apéndice A. Reproducción manual

Sustituir los nombres por los de los dos contenedores Kubo del bundle activo.

```bash
# En A, crear y anunciar un bloque de prueba. El contenido debe ser distinto por nodo:
# un contenido idéntico produce un CID idéntico y el otro nodo lo tiene ya en local.
cid=$(docker exec <kubo-a> sh -c \
  'printf bitswap-diagnostic-a | ipfs block put --format=raw --mhtype=sha2-256')
docker exec <kubo-a> ipfs routing provide "$cid"

# B debe encontrar A como proveedor.
docker exec <kubo-b> ipfs routing findprovs "$cid" -n 1

# El fallo: no recupera el bloque aunque el proveedor sea visible. Muestrear partners
# DURANTE la lectura, no después.
docker exec <kubo-b> sh -c "timeout 15 ipfs block stat '$cid'"
docker exec <kubo-b> ipfs stats bitswap --human

# La cura: un evento de conexión real (con multiaddr, nunca el peer ID desnudo).
docker exec <kubo-b> ipfs swarm disconnect /p2p/<peerID-A>
docker exec <kubo-b> ipfs swarm connect /dns4/<kubo-a>/tcp/4001/p2p/<peerID-A>
docker exec <kubo-b> sh -c "timeout 15 ipfs block stat '$cid'"
```

Equivalente automatizado: `scripts/ipfs-bitswap-probe.sh inplace` (sobre el despliegue) o
`isolated` (par desechable, con `--pnet` para igualar el swarm privado y `--image` para
probar otra versión de Kubo).

Evidencia de que no hubo transferencia: contadores de bloques y bytes a cero en los dos
nodos durante la ventana de la prueba. La mera presencia de dos peers en `/peers`, una
asignación de Cluster o un estado `pinning` transitorio no son criterios suficientes.

## Apéndice B. Fuentes

- [ipfs/boxo PR #1201 — fix(bitswap): see peers connected before startup](https://github.com/ipfs/boxo/pull/1201)
- [ipfs/boxo issue #83 — Failure to fetch content from connected nodes](https://github.com/ipfs/boxo/issues/83)
- [ipfs/boxo issue #70 — Question about sendWants and send blocks in peermanager](https://github.com/ipfs/boxo/issues/70)
- [ipfs/kubo issue #9175 — bitswap network Notify Event may be registered after connect](https://github.com/ipfs/kubo/issues/9175)
- [discuss.ipfs.tech — Private network with 2 nodes: unable to broadcast new CID](https://discuss.ipfs.tech/t/private-network-with-2-nodes-unable-to-broadcast-new-cid-to-the-other-node/11952)
- [discuss.ipfs.tech — Unable to get blocks when the node is connected to multiple peers](https://discuss.ipfs.tech/t/unable-to-get-blocks-when-the-node-is-connected-to-multiple-peers/18711)
- Referencia de configuración de Kubo: <https://github.com/ipfs/kubo/blob/master/docs/config.md>
