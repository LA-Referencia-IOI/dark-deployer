# Revisión crítica del diagnóstico inicial (2026-09-14)

Objeto: auditar el razonamiento, la evidencia y el plan de
`docs/ipfs-private-swarm-bitswap-replication-diagnosis-2026-09.md` tal como estaban la
mañana del 2026-09-14, y decir qué faltaba para llegar a la causa.

Estado del incidente: **resuelto el mismo día**. La causa quedó confirmada y el arreglo
validado; el relato completo, la evidencia y los pendientes están en el documento del
incidente. Este documento no se actualiza con cada avance: es el registro de la auditoría.

Cómo leerlo: las secciones 1 a 7 son la revisión tal como se emitió, con una nota de
desenlace allí donde la práctica corrigió algo. La sección 8 recoge los errores que cometió
esta propia revisión, que son parte del expediente.

---

## 1. Veredicto

El diagnóstico era un buen ejercicio diferencial y era honesto: registraba lo que no sabía,
separaba "asignado" de "replicado", rechazaba el `pinning` transitorio como criterio de éxito
y dejaba explícito que `context canceled` era el artefacto de un timeout y no una causa. Sus
criterios de cierre eran mejores que el síntoma que perseguía.

Tenía tres problemas, en orden de importancia.

**Primero: se detenía un paso antes de la causa.** La evidencia que el documento ya contenía
—proveedor descubierto *y conectado*, cero bytes y cero bloques en ambos nodos, ningún
rechazo del resource manager, ningún WANT— era la firma de un defecto documentado de Bitswap:
el nodo que recupera nunca registró esa conexión porque existía antes de que Bitswap
arrancara. Existía un arreglo publicado aguas arriba (PR de `ipfs/boxo` #1201) cuyo
planteamiento del problema describía la traza casi literalmente. El diagnóstico situaba el
fallo "entre el peer que la sesión considera disponible y la cola de mensajes o stream de
Bitswap" —esa localización era correcta— pero lo trataba como un misterio local que había que
buscar con una matriz de configuración, cuando ya tenía nombre, mecanismo y parche.

**Segundo: dos descartes estaban invertidos.** Que `Peering.Peers` no lo arreglara y que
reconectar el bootstrap "de forma no fiable" no lo arreglara no eran pruebas contra la
hipótesis de conexión: eran predicciones de ella. El documento los usaba para cerrar la línea
de investigación que era la correcta. La segunda parte se confirmó de la peor manera posible:
la reconexión que se había probado no desconectaba, así que no podía arreglar nada.

**Tercero: el plan atacaba el eje equivocado y cerraba la única vía real de arreglo.** Los
próximos pasos proponían aislar individualmente `AppendAnnounce`, `autoconf-off`,
`Routing.Type`, el perfil `server` y `AddrFilters`, y descartaban cambiar de versión. Pero el
fallo ya se reproducía en dos configuraciones muy distintas (con y sin PNET), lo que apuntaba
a *cuándo* se establece la conexión y no a *cómo* está configurada. La matriz de configuración
costaba semanas; la prueba que decidía costaba cinco minutos. Y la conclusión "cambiar de
versión no es la siguiente línea de trabajo" —correcta dentro de la familia 0.41/0.42—
descartaba precisamente el único arreglo real disponible.

---

## 2. La causa que el diagnóstico tenía a un paso

Bitswap no pregunta si hay conexión: escucha eventos. Al arrancar registra un notificador de
libp2p, y libp2p sólo reporta las conexiones abiertas después de ese registro. Un peer que ya
estaba conectado —en este despliegue, porque el entrypoint lo deja en `Bootstrap` antes de
`exec ipfs daemon`— nunca llega a registrarse; sin registro no hay cola de mensajes por peer,
y `PeerManager.SendWants` devuelve `false` sin escribir un solo log. No hay error, no hay
stream, no hay WANT, y nada lo corrige mientras esa conexión viva.

El mecanismo completo, la cita del PR que lo arregla, la tabla de encaje con cada pieza de
evidencia y la referencia al fichero afectado están en §5 del documento del incidente. Aquí
basta con la conclusión: la causa se confirmó esa misma tarde, y el diagnóstico estaba a un
paso de nombrarla porque toda la evidencia necesaria ya estaba en su §3.

---

## 3. Las pruebas que faltaban, y qué pasó con cada una

Se propusieron por orden de coste. El desenlace de cada una está en el documento del
incidente; esta tabla es el cierre de la sección.

| Prueba propuesta | Desenlace |
| --- | --- |
| 3.1 Rebote de conexión real (`disconnect` + `connect`) en el nodo que recupera | Ejecutada. Es la que confirmó la causa, después de corregir el script. |
| 3.2 Reiniciar el nodo que sirve dejando el otro en pie | Ejecutada como control positivo: el fallo reapareció al reiniciar y se curó con el rebote. |
| 3.3 Control de streams (`ipfs ping`, `ipfs id`) | Ejecutada. Dos segundos que descartaron MTU, resource manager y gater sin matriz de configuración. |
| 3.4 Log con los subsistemas correctos, capturado desde el arranque | Resuelta a medias: en la imagen corregida aparecen `PeerConnected notification` y `WANT_BLOCK`, pero la pasada sobre 0.42 salió a cero incluso para `Found peer for CID`, así que el instrumento quedó en duda (§6.4 del documento del incidente). |
| 3.5 Volcado de goroutines durante el cuelgue | No fue necesaria: la causa se confirmó antes. Sigue siendo la herramienta indicada si algo parecido vuelve a reproducirse sin curarse. |
| 3.6 Experimento 2×2 (misma configuración, sólo cambia el orden de conexión) | Implementada en `scripts/ipfs-bitswap-probe.sh`, con modos `inplace` e `isolated`. Es la prueba que el diagnóstico pedía sin llegar a escribir. |

---

## 4. Descartes y evidencia, revisados

Los descartes de Cluster, Store API, conectividad, PSK e identidad eran correctos y estaban
bien sostenidos. Los siguientes necesitaban matiz.

**MTU/PMTU.** No estaba evaluado y merecía una línea: un primer WANT de Bitswap son decenas de
bytes y ambos contenedores están en el mismo bridge, así que no podía explicar un silencio
total. Lo decidió el `ipfs ping` de 3.3.

**Resource manager.** El documento decía que no había observado rechazo, pero no decía cómo lo
había verificado, y en los próximos pasos pedía justamente comprobarlo. Kubo no limita streams
salientes por defecto, y cuando limita lo dice con una cadena propia (`Protected from exceeding
resource limits`). Había que declararlo verificado con ese grep y no como ausencia de
observación.

**Negociación de protocolo y `Websocket=false`.** No podían suprimir el WANT: Bitswap abre el
stream y negocia sobre la conexión TCP existente, y un fallo de negociación deja un error
visible. Coherente con lo observado, pero por la razón contraria a la que sugería el
documento: no se veía error **porque no se intentó abrir el stream**.

**`AppendAnnounce`.** No podía suprimir un WANT entre dos peers ya conectados. Sí es un riesgo
real, pero para *otros* peers (un `/dns4/<container>` sólo resuelve dentro del bridge), y
pertenece a otro informe.

**`findprovs` era evidencia más débil de lo que se le atribuía.** Kubo cachea los registros de
proveedor en el datastore, así que una consulta repetida puede resolverse localmente sin
intercambio de stream alguno. Importaba porque el documento usaba ese resultado para declarar
probado que el segundo nodo conocía el CID y su servidor, y sobre esa base descartaba
hipótesis. El `ipfs ping` lo habría demostrado en dos segundos.

**"Ambos nodos anuncian los protocolos Bitswap".** Faltaba decir cómo se verificó: `ipfs id`
sobre uno mismo no prueba lo que ve el peer. Poco relevante para el resultado, pero era una
afirmación sin fuente.

**`partners [0]`, que esta revisión presentó como firma del fallo.** Es un dato del
diagnóstico, pero sólo discrimina medido **con la lectura en vuelo**; después de una
transferencia terminada vale 0 aunque haya funcionado. El error de énfasis fue de esta
revisión, no del diagnóstico (§10).

---

## 5. Correcciones al diagnóstico verificadas contra el repositorio

**5.1 El wrapper retirado nunca desconectaba.** El documento enumeraba entre los experimentos
probados "desconectar y reconectar explícitamente el bootstrap tras arrancar". El historial
del componente decía otra cosa: el wrapper retirado en `e3545cf` hacía `ipfs swarm connect`
tras esperar a que la API estuviera lista, y **nunca** ejecutó `ipfs swarm disconnect`
(`git log -S 'swarm disconnect' -- scripts/ipfs-entrypoint.sh` sobre todo el historial del
fichero: cero resultados). La sección de evidencia del documento lo confirmaba: sólo
documentaba `swarm connect` explícito.

Consecuencia: la única prueba que discriminaba entre "problema de conexión" y "problema de
configuración" no se había ejecutado en forma válida, ni en el entrypoint ni a mano. Y una
segunda lectura reforzaba la hipótesis: el documento decía que el wrapper no lo arreglaba "de
forma no fiable". *No fiable* es lo que se espera de una carrera de arranque; se leyó como "la
conexión no es la causa" cuando significaba lo contrario.

**5.2 El bundle generado.** El documento advertía que un bundle previo "puede conservar una
copia" del entrypoint antiguo. Verificado: la única copia generada —la que `compose.yaml`
monta realmente— era idéntica byte a byte al fuente. El riesgo que describía no estaba
abierto; la regla que extraía ("regenerar antes de recrear contenedores", porque lo que
ejecuta Docker es la copia generada) sigue siendo correcta y conviene conservarla. Esa regla
tiene hoy otro caso: el refresh del bundle no poda ficheros borrados en el fuente (§9 del
documento del incidente).

**5.3 El entrypoint no aplica un estado declarado.** Sólo escribe claves concretas, así que
cualquier sondeo manual añadido al `config.json` de un volumen sobrevive a los reinicios. El
documento decía que los cambios "se revirtieron de los volúmenes" —correcto—, pero era un paso
manual no verificado por nada, en un diagnóstico cuyo tema central era distinguir el estado
real del supuesto. A medio plazo conviene que el renderer genere el `config.json` completo, o
que el entrypoint lo reafirme en cada arranque, de modo que un experimento no pueda contaminar
la siguiente prueba.

**5.4 "Cambiar de versión no es la siguiente línea de trabajo".** Correcto dentro de la familia
0.41/0.42: ninguna de las dos contiene el arreglo. Pero la conclusión general descartaba el
único arreglo real, y el tiempo lo confirmó: la corrección fue precisamente una versión con el
`boxo` arreglado. En el momento de la revisión el pin estaba hardcodeado en
`deployment_v3/services.py`; hoy las versiones de runtime viven en `base.images` del catálogo,
así que decidir una versión es cambiar una entrada y regenerar el bundle.

**5.5 Dos `context canceled` distintos tratados como uno.** Ver §6.

---

## 6. El segundo `context canceled`

El documento identificaba bien que el `context canceled` del test (`timeout 15 ipfs block
stat`) es un artefacto del propio comando. Pero el `pin_error: context canceled` que aparece
en `peer_map` es otro contexto —el de Cluster— y su procedencia nunca se estableció. El
documento los agrupaba y concluía que el texto no es una causa: cierto para el primero, no
demostrado para el segundo.

**Sigue abierto.** El mecanismo plausible, la prueba que lo decide y su lugar en los criterios
de cierre están en §8.2 del documento del incidente. Es un defecto independiente del que se
cerró: arreglar Bitswap no lo arregla.

---

## 7. El plan de próximos pasos, revisado

| Paso propuesto | Revisión y desenlace |
| --- | --- |
| 1. Reproducción mínima fuera de Cluster | Mantener la idea, cambiar la variable: orden de conexión, no claves de configuración. Tal como estaba, reproducía la misma carrera y volvía a fallar sin enseñar nada nuevo. Implementado como `scripts/ipfs-bitswap-probe.sh isolated`. |
| 2. Matriz de configuración (`autoconf-off`, routing, `AppendAnnounce`, bootstrap por API) | Retirado: eje equivocado. El fallo ya ocurría en dos configuraciones muy distintas, incluida una sin PNET. |
| 3. Trazas de libp2p y Bitswap | Mantenido, con dos correcciones: fijar los subsistemas reales y capturar desde el arranque, no a posteriori; y añadir el volcado de goroutines. |
| 4. Abrir incidencia a Kubo | Redirigido. El arreglo ya existía aguas arriba, así que una incidencia nueva se cerraría como duplicada. Lo valioso es aportar a #1201 una reproducción determinista en Docker, que es lo que faltaba allí. |
| 5. Cambio mínimo en el renderer o el entrypoint | Redirigido: la corrección no era de configuración. Resultó ser (a) anclar la imagen a un Kubo con el `boxo` arreglado, aplicado como canario y validado; (b) un paliativo de reconexión sólo si (a) no fuera viable, que no hizo falta. |

---

## 8. Riesgos de aplicar un arreglo sin prueba

Una reconexión ciega reproduce el wrapper que ya se retiró y que "a veces" funcionaba:
exactamente el tipo de parche que deja un fallo intermitente y caro de diagnosticar la próxima
vez. Tocar los knobs de Bitswap que se probaron (`Libp2pEnabled`, `ServerEnabled`,
`BroadcastControl`) no puede arreglar un deseo que nunca se crea, y contamina volúmenes
persistentes. Y subir de versión sin verificar la matriz `boxo`/Kubo puede introducir cambios
de comportamiento ajenos al incidente.

**El primero de esos riesgos se materializó, y en el instrumento de esta revisión**: la primera
pasada del probe reconectaba con un peer ID desnudo, Kubo lo rechazaba, el `connect` era un
no-op y el resultado parecía una cura. Lo detectó el operador al comprobar el comando a mano.
Es la razón de que la reconexión del script use hoy `/p2p/<peerID>` y falle de forma explícita
si no logra desconectar.

---

## 9. Criterios de cierre y cobertura

Los criterios de cierre del diagnóstico eran buenos y se mantuvieron. Esta revisión añadió dos
exigencias, porque el fallo es una carrera de arranque: una **prueba negativa** (con el orden
de conexión invertido a propósito, el flujo debe fallar; si no, la prueba no demuestra nada) y
**N repeticiones**, ya que en una red real la carrera es probabilística aunque en Docker sea
casi determinista. El estado de cada criterio está en §8 del documento del incidente.

Sobre la cobertura del repositorio, el hueco que señaló esta revisión sigue abierto: el smoke
test de `dark-ipfs` lee el contenido en el mismo nodo que lo publicó, así que pasa aunque la
replicación esté rota, y el notebook de aceptación sólo mira la vista de Cluster. Los criterios
4 y 5 hoy los cubre el probe en modo manual. Llevarlos a la aceptación es el trabajo que queda.

---

## 10. Errores de esta revisión

Un registro de auditoría que no anota sus propios fallos vale menos. Los de ésta:

- **Presentó como confirmación una pasada que no probaba nada** (§8). El `disconnect` con peer
  ID desnudo convirtió el rebote en un no-op y el resultado en un falso positivo. Lo corrigió
  el operador.
- **Sobredimensionó `partners [0]`** como firma del fallo. Sólo informa con la lectura en
  vuelo; la pasada real lo mostró a 0 también en los casos que funcionaron.
- **Verificó el script en un entorno distinto del de destino.** Se escribió sobre bash 5.1 (el
  de la sesión de trabajo) y el primer intento del operador falló en su macOS:
  `declare: -A: invalid option`, porque `/bin/bash` allí es 3.2. El script está hoy limitado a
  bash 3.2 —sin arrays asociativos, sin `declare -g`, estado por nodo en ficheros, una sola
  trampa de salida—, pero esa comprobación se hizo por inspección del código, no ejecutándolo
  en 3.2.
- **Confió en un banco de pruebas que modela sus propios supuestos.** El `docker` simulado con
  el que se validó el script reproducía fielmente el fallo… y también sus defectos: devolvía el
  mismo CID para los dos nodos y por eso no podía detectar que una lectura "remota" era local.
  Un simulador que comparte los supuestos de quien lo escribe no puede encontrar errores en
  ellos; lo encontró el operador leyendo la salida real.
- **Dejó afirmaciones que el tiempo invalidó**: que el pin estaba hardcodeado en
  `services.py` (después se centralizaron las versiones) y que la cura estaba verificada
  (lo estaba la hipótesis, no el arreglo). Corregidas en el texto y anotadas aquí.

---

## Apéndice. Cómo se verificó

- Documento auditado: lectura completa, y verificación de sus afirmaciones contra el
  repositorio: `components/dark-ipfs/scripts/{ipfs,cluster}-entrypoint.sh` (fuente y copia
  generada, comparadas byte a byte); historial git del componente `dark-ipfs`, incluido el diff
  de `e3545cf`; `deployment_v3/services.py` y `catalogs.py`; los inventarios `local-ha`; el
  bundle generado (`compose.yaml`, `env/ipfs-storage-{a,b}.env`, `storage-endpoints.json`,
  `catalog_data/`);
  `components/dark-store-api/app/{backends/ipfs_cluster.py,api/store.py,config.py}`;
  `components/dark-ipfs/scripts/smoke-test.sh`;
  `notebooks/scenarios/local-ha-deposit-lifecycle.ipynb`; `tests/`.
- Fuentes externas: `ipfs/boxo` PR #1201 (descripción, diff y fecha de fusión),
  `bitswap/network/bsnet/ipfs_impl.go` y `bitswap/client/internal/peermanager/peermanager.go`
  en `boxo` v0.40.0 (la versión que fija `kubo` v0.42.0),
  `bitswap/client/internal/session/{session,sessionwantsender}.go`,
  `routing/providerquerymanager`, las incidencias abiertas de `ipfs/boxo` #70 y #83,
  `ipfs/kubo` #9175, y los hilos equivalentes en `discuss.ipfs.tech`.
- Verificado por esta revisión sobre el repositorio, además de lo anterior: `sh -n` de los dos
  entrypoints, `git diff --check`, la suite completa de `tests/` (53 pruebas en la última
  ejecución) y comprobación de que el compose generado lleva el digest del canario y
  `IPFS_TELEMETRY: 'off'`.
- `scripts/ipfs-bitswap-probe.sh`: verificado con `bash -n`, con los dos modos en `--dry-run`
  y ejecutado de extremo a extremo contra un `docker` simulado que modela el fallo, cubriendo
  los cuatro desenlaces y los códigos de salida 0/1/2. Las ejecuciones reales y los defectos
  que destaparon están en §6 del documento del incidente y en §10 de aquí.
- Lo que esta revisión no pudo hacer ella misma: ejecutar el probe contra el Docker del
  operador. El entorno de sesión no tiene CLI de Docker, ni socket, ni acceso de red al daemon;
  la prueba la lanzó el operador y su salida es la que se analizó aquí.
- Fechas y versiones posteriores a mayo de 2025 proceden de consulta web del 2026-09-14 y
  deben re-verificarse antes de decidir un ancla de imagen.

**Fuentes:**

- [ipfs/boxo PR #1201 — fix(bitswap): see peers connected before startup](https://github.com/ipfs/boxo/pull/1201)
- [ipfs/boxo issue #83 — Failure to fetch content from connected nodes](https://github.com/ipfs/boxo/issues/83)
- [ipfs/boxo issue #70 — Question about sendWants and send blocks in peermanager](https://github.com/ipfs/boxo/issues/70)
- [ipfs/kubo issue #9175 — bitswap network Notify Event may be registered after connect](https://github.com/ipfs/kubo/issues/9175)
- [discuss.ipfs.tech — Private network with 2 nodes: unable to broadcast new CID](https://discuss.ipfs.tech/t/private-network-with-2-nodes-unable-to-broadcast-new-cid-to-the-other-node/11952)
- [discuss.ipfs.tech — Unable to get blocks when the node is connected to multiple peers](https://discuss.ipfs.tech/t/unable-to-get-blocks-when-the-node-is-connected-to-multiple-peers/18711)
