# Revisión crítica del diagnóstico de replicación IPFS en `local-ha`

Revisión: 2026-09-14
Documento revisado: `docs/ipfs-private-swarm-bitswap-replication-diagnosis-2026-09.md`
Objeto: validez del razonamiento, de la evidencia y del plan. No es una repetición del
diagnóstico.

Método: lectura del documento; verificación de sus afirmaciones contra el repositorio
(entrypoint de Kubo y de Cluster, `deployment_v3/services.py`, inventario `local-ha`,
bundle generado, historial git del componente `dark-ipfs`, `dark-store-api`, notebook de
aceptación); contraste de la secuencia de trazas con el código de `boxo` v0.40.0 (la
versión que Kubo v0.42.0 fija en su `go.mod`); y revisión de las hipótesis alternativas
conocidas (MTU, resource manager, gater/PNET, negociación de protocolo, anuncios).

---

## 1. Veredicto

El documento es un buen ejercicio de diagnóstico diferencial y es honesto: registra lo que
no sabe, separa "asignado" de "replicado", rechaza el `pinning` transitorio como criterio
de éxito y deja explícito que `context canceled` es el artefacto de un timeout y no una
causa. Sus criterios de cierre son mejores que el síntoma que persigue.

Tiene tres problemas, en orden de importancia.

**Primero: se detiene un paso antes de la causa.** La evidencia que el documento ya
contiene —`partners [0]` en ambos nodos (aunque, ver §10, ese contador sólo informa
mientras hay una lectura en vuelo, y el documento lo muestreó antes y después), proveedor
descubierto *y conectado*, cero bytes y
cero bloques, ningún rechazo del resource manager, ningún WANT— es la firma exacta de un
defecto documentado de Bitswap: el nodo que recupera nunca registró esa conexión porque la
conexión existía antes de que Bitswap arrancara. Existe un arreglo publicado aguas arriba
(PR de `ipfs/boxo` #1201, fusionado el 2026-08-14) cuyo planteamiento del problema describe
esta traza casi literalmente. El documento sitúa el fallo "entre el peer que la sesión
considera disponible y la cola de mensajes o stream de Bitswap" —esa localización es
correcta— pero lo trata como un misterio local que hay que buscar con una matriz de
configuración, cuando ya tiene nombre, mecanismo y parche.

**Segundo: dos descartes están invertidos.** Que `Peering.Peers` no lo arregle y que
reconectar el bootstrap "de forma no fiable" no lo arregle no son pruebas contra la
hipótesis de conexión: son predicciones de ella. El documento los usa para cerrar la línea
de investigación que era la correcta.

**Tercero: el plan ataca el eje equivocado y cierra la única vía real de arreglo.** Los
próximos pasos proponen aislar individualmente `AppendAnnounce`, `autoconf-off`,
`Routing.Type`, el perfil `server` y `AddrFilters`, y descartan cambiar de versión. Pero el
fallo ya se reproduce en dos configuraciones muy distintas (con y sin PNET), lo que apunta
a *cuándo* se establece la conexión y no a *cómo* está configurada. La matriz de
configuración cuesta semanas; la prueba que decide cuesta cinco minutos y no se ha hecho.
Y la conclusión "cambiar de versión no es la siguiente línea de trabajo" —correcta dentro
de la familia 0.41/0.42— descarta precisamente el único arreglo real disponible.

---

## 2. La causa que el documento tiene a un paso

### 2.1 Mecanismo

Bitswap no pregunta "¿hay conexión?"; escucha eventos. Al arrancar registra un notificador
de libp2p, y libp2p solo reporta las conexiones que se abren **después** de ese registro.
Un peer que ya estaba conectado —por ejemplo porque el entrypoint lo dejó en `Bootstrap`
antes de `exec ipfs daemon`, y la red Docker resuelve el nombre y completa el handshake en
menos de un milisegundo— nunca llega a registrarse. Sin registro no hay cola de mensajes
por peer, y `PeerManager.SendWants` devuelve `false` **sin escribir un solo log** cuando no
hay cola: no hay error, no hay stream, no hay WANT. El deseo queda en la lista del cliente
para siempre, la sesión sí considera al proveedor "disponible" (de ahí `Found peer for CID`
y `Added peer to session`, que no dependen del registro de conexión), y nada se
autocorrige: mientras esa conexión viva, no habrá otro evento `Connected` que dispare el
reintento.

El propio PR que lo arregla lo describe así:

> Bitswap can wait forever for a block a connected peer already has. `Start` registers a
> libp2p notifier, and libp2p only reports connections opened after that, so a peer
> connected while the node is still coming up is never recorded. Bitswap never sends it a
> want, and nothing corrects that later: the peer stays invisible for the life of the
> connection. Routing usually hides this by finding the content elsewhere; without that
> fallback the fetch just hangs.

El arreglo es explícito y mínimo: tras registrar el notificador, recorrer
`host.Network().Conns()` y marcar como conectados los peers de las conexiones ya abiertas
(saltando las "limited", igual que hace el notificador). El fichero afectado es
`bitswap/network/bsnet/ipfs_impl.go`.

Encaja con toda la evidencia del documento, línea por línea:

| Evidencia del documento | Qué predice el mecanismo |
| --- | --- |
| `Found peer for CID` y `Added peer to session` | El descubrimiento DHT y la sesión funcionan: no dependen del registro de conexión de Bitswap. |
| Ningún WANT y `blocks sent/received: 0` en ambos | El deseo nunca se convierte en mensaje: es el `return false` silencioso de `SendWants`. |
| `partners [0]` en ambos nodos, muestreado **mientras la lectura está en vuelo** | No existe ningún stream Bitswap registrado en ninguno de los dos sentidos. Fuera de esa ventana el contador vale 0 aunque la transferencia haya funcionado, así que sólo discrimina medido en vuelo. |
| Sin rechazo del resource manager, sin error de stream | No se llegó a intentar abrir el stream; el fallo está aguas arriba de `NewStream`. |
| `swarm connect` explícito B→A "correcto" y el fallo persiste | Conectar a un peer ya conectado reutiliza la conexión existente y no genera evento nuevo. |
| Se reproduce sin Cluster, sin PNET, y en 0.41 y 0.42 | Es una propiedad del arranque de Bitswap: ni Cluster, ni la PSK, ni la versión dentro de esa ventana lo cambian. |
| `Peering.Peers` no lo arregla | El peering también conecta durante el arranque, antes de que el notificador importe. |
| El wrapper retirado no lo arreglaba "de forma fiable" | "No fiable" es la firma de una carrera de arranque. Y el wrapper no desconectaba (§5.1), así que no forzaba conexión nueva. |

El detalle que explica la reproducibilidad: en una red Docker con RTT por debajo del
milisegundo, la carrera se pierde prácticamente siempre. Aguas arriba esto apareció como
un *flake* de CI (Kubo vía mDNS), es decir, como un fallo intermitente; aquí es
determinista. Eso no lo hace un bug distinto, lo hace un banco de pruebas mejor.

### 2.2 Estado de la confirmación

Esto **no está probado en el entorno local todavía**, y el informe no debe presentarlo
como cerrado. Lo que se puede afirmar es que es la única explicación disponible que da
cuenta de *toda* la evidencia a la vez, que el mecanismo está confirmado en el código de
la versión que usan (`boxo` v0.40.0), y que tiene una prueba discriminante de cinco
minutos. La primera ejecución de esa prueba (§10) confirma la parte que más importa —el
fallo se cura con un evento de conexión nuevo— y deja pendientes dos comprobaciones.

Si esa prueba falla, quedan dos candidatos, ambos distinguibles con los mismos logs: una
conexión marcada como "limited" (improbable sin relays) y el defecto vecino boxo #1163
—pérdida silenciosa de un peer tras un único fallo de envío en un rebote de conexión,
corregido en `boxo` v0.41.0—, que es plausible en un arranque de Compose con
reconexiones.

---

## 3. Las pruebas que faltan

Por orden de coste. Las tres primeras se ejecutan sobre el despliegue actual.

**3.1 Rebote de conexión en el nodo que recupera.** En B, no `connect` (que ya se probó y
es un no-op sobre una conexión viva), sino ciclo completo:

```bash
docker exec <kubo-b> ipfs swarm disconnect /p2p/<peerID-A>
docker exec <kubo-b> ipfs swarm connect /ip4/172.30.0.2/tcp/4001/p2p/<peerID-A>
docker exec <kubo-b> ipfs stats bitswap --human      # leer DURANTE la lectura, no después
docker exec <kubo-b> sh -c "timeout 15 ipfs block stat $cid"
```

**3.2 Reiniciar el nodo que sirve (A) dejando B en pie.** La conexión se rehará cuando B ya
tenga Bitswap arrancado. La misma prueba desde el otro lado.

**3.3 Control de streams.** `ipfs ping <peerID-A>` y `ipfs id <peerID-A>` desde B. Debe
funcionar en ambos casos (son protocolos distintos de Bitswap). Dos segundos de trabajo que
descartan de golpe MTU, resource manager y gater, sin matriz de configuración.

**3.4 Log con los subsistemas correctos, capturado desde el arranque del contenedor.** No
basta con `bitswap=debug` a posteriori:

```bash
docker exec <kubo-b> ipfs log ls     # lista los nombres reales de subsistema
# y relanzar con:
GOLOG_LOG_LEVEL="bitswap/connevtman=debug,bitswap/client/peermgr=debug,bitswap/client/msgq=debug,bitswap/bsnet=debug,bitswap/session=debug,routing/provqrymgr=debug"
```

La prueba es negativa: la **ausencia** de `PeerConnected notification` y de
`getOrCreate: new queue` para el peer A, junto con la ausencia de `sent message ...
WANT_BLOCK` en la cola. El contraejemplo es la presencia de esas líneas. Conviene además
`ipfs bitswap wantlist` durante el cuelgue: si el CID sigue ahí pendiente de un peer que
nunca recibió nada, el deseo nunca salió.

**3.5 Volcado de goroutines durante el cuelgue.** `ipfs diag profile`, o directamente
`curl -s "http://127.0.0.1:5001/debug/pprof/goroutine?debug=2"`. Dice dónde está atascado
el cliente mejor que cualquier log y no depende de haber elegido bien el nivel de log. El
documento propone "recoger trazas de libp2p y Bitswap"; añadir el volcado convierte esa
línea de trabajo en algo concluyente en un solo intento.

**3.6 El experimento 2×2 (la reproducción mínima, bien planteada).** Dos Kubo del mismo
`boxo`, con exactamente la misma configuración en los dos casos —la del entrypoint, sin
Cluster— y una sola variable: **cuándo se establece la conexión**.

- **Caso orden-tardío (control):** arrancar ambos demonios, esperar a que Bitswap haya
  arrancado y *entonces* `ipfs swarm connect`. Publicar un bloque en A y leerlo desde B.
  Debe **funcionar**.
- **Caso orden-temprano:** el mismo par, pero con el peer en `Bootstrap` antes de arrancar
  el demonio, que es lo que hace el entrypoint real. Debe **fallar** con `partners` a 0
  mientras la lectura está en vuelo, y contadores a cero.

No se varía ninguna clave de configuración a propósito: así el resultado no se puede
atribuir a un perfil ni a un filtro, y el control no depende de ninguna suposición sobre
el gater de direcciones privadas. La secuencia completa en cada matriz es: intento de
lectura en ambas direcciones, rebote de conexión en cada dirección, y los mismos intentos
otra vez, de modo que el contraste "falla → se cura" quede dentro de una sola ejecución.

Está implementado en `scripts/ipfs-bitswap-probe.sh isolated`, que además captura el log
del nodo con los subsistemas de Bitswap en debug y cuenta las líneas que discriminan
(`PeerConnected notification`, `new queue for`, `WANT_BLOCK`) antes y después del rebote.
El mismo script en modo `inplace` ejecuta la secuencia equivalente sobre el despliegue que
ya está en pie, sin Cluster de por medio más que como observador, y es material publicable
para el PR aguas arriba: una reproducción determinista en Docker, con la variante `--pnet`
para igualar el swarm privado de producción.

---

## 4. Revisión de los descartes y de la evidencia

Las descartes de Cluster, Store API, conectividad, PSK e identidad son correctas y están
bien sostenidas. Las siguientes necesitan matiz.

**MTU/PMTU.** No está evaluado en el documento y merece una línea: un primer WANT de
Bitswap son decenas de bytes y ambos contenedores están en el mismo bridge, así que no
puede explicar un silencio total. Lo decide el `ipfs ping` de 3.3.

**Resource manager.** El documento dice que no observó rechazo, pero no dice cómo lo
verificó, y en la sección de próximos pasos pide justamente comprobarlo. Kubo no limita
streams *salientes* por defecto, y cuando limita lo dice con una cadena propia
(`Protected from exceeding resource limits` en el subsistema `resourcemanager`). Hay que
declararlo verificado con ese grep, no como ausencia de observación.

**Negociación de protocolo y `Websocket=false`.** No pueden suprimir el WANT: Bitswap abre
el stream y negocia sobre la conexión TCP existente, y un fallo de negociación deja un
error visible. Coherente con lo observado, pero por la razón contraria a la que sugiere el
documento: no se ve error **porque no se intentó abrir el stream**.

**`AppendAnnounce`.** No puede suprimir un WANT entre dos peers ya conectados. Sí es un
riesgo real, pero para *otros* peers (un `/dns4/<container>` solo resuelve dentro del
bridge), y pertenece a otro informe.

**`findprovs` es evidencia más débil de lo que el documento le atribuye.** Kubo cachea los
registros de proveedor en el datastore, así que una consulta repetida puede resolverse
localmente sin intercambio de stream alguno. Es un matiz importante porque el documento
usa ese resultado para declarar probado que "B conoce tanto el CID como el peer que lo
sirve", y sobre esa base descarta hipótesis. El documento nunca demuestra, en la misma
ventana, que un stream libp2p transportó datos: `ipfs ping` lo habría demostrado en dos
segundos y habría aislado el problema al protocolo Bitswap sin ambigüedad.

**"Ambos nodos anuncian los protocolos Bitswap".** Falta decir cómo se verificó: `ipfs id`
sobre uno mismo no prueba lo que ve el peer, y las listas de protocolos que reporta
identify pueden estar desactualizadas. Poco relevante para el resultado, pero es una
afirmación sin fuente.

---

## 5. Correcciones al documento (verificadas contra el repositorio)

**5.1 El wrapper retirado nunca desconectaba.** El documento enumera entre los
experimentos probados "desconectar y reconectar explícitamente el bootstrap tras arrancar".
El historial del componente dice otra cosa: el wrapper que se retiró en `e3545cf` hacía
`ipfs swarm connect` tras esperar a que la API estuviera lista, y **nunca** ejecutó
`ipfs swarm disconnect` (`git log -S 'swarm disconnect' -- scripts/ipfs-entrypoint.sh`
sobre todo el historial del fichero: cero resultados). La sección de evidencia del
documento confirma lo mismo: solo documenta `swarm connect` explícito.

Consecuencia: la única prueba que discrimina entre "problema de conexión" y "problema de
configuración" no se ha ejecutado en forma válida en ningún momento, ni en el entrypoint
ni a mano. Y hay una segunda lectura que refuerza la hipótesis: el documento dice que el
wrapper no lo arreglaba "de forma fiable". *No fiable* es exactamente lo que se espera de
una carrera de arranque; se ha leído como "la conexión no es la causa" cuando significa lo
contrario.

**5.2 El bundle generado.** El documento advierte que un bundle previo "puede conservar una
copia" del entrypoint antiguo. Verificado hoy: la única copia generada —la que `compose.yaml`
monta realmente, `.../local/local/sources/components/dark-ipfs/scripts/ipfs-entrypoint.sh`—
es idéntica byte a byte al fuente. El riesgo que describe no está abierto; la regla que
extrae ("regenerar antes de recrear contenedores", porque lo que ejecuta Docker es la copia
generada y no el fuente) sigue siendo correcta y conviene conservarla.

**5.3 Trampa relacionada, no mencionada.** El entrypoint no aplica un estado declarado:
solo escribe claves concretas. Por eso, cualquier sondeo manual que se haya añadido a mano
al `config.json` de un volumen **sobrevive a los reinicios**. El documento dice que los
cambios "se revirtieron de los volúmenes" —correcto—, pero fue un paso manual y no
verificado por nada, en un diagnóstico cuyo tema central es distinguir el estado real del
estado supuesto. Merece una línea en el documento, y a medio plazo que el renderer genere
el `config.json` completo (o que el entrypoint lo reafirme en cada arranque), de modo que
un experimento no pueda contaminar la siguiente prueba.

**5.4 "Cambiar de versión no es la siguiente línea de trabajo".** Correcto dentro de la
familia 0.41/0.42: ninguna de las dos contiene el arreglo, y presentar el salto 0.41→0.42
como solución habría sido un error. Pero la conclusión general descarta el único arreglo
real. El pin está hardcodeado en `deployment_v3/services.py` (`ipfs/kubo:v0.42.0`), así que
la decisión es una línea más regenerar el bundle, no una reingeniería.

**5.5 Dos `context canceled` distintos tratados como uno.** Ver §6.

---

## 6. El segundo `context canceled`

El documento identifica bien que el `context canceled` del test (`timeout 15 ipfs block
stat`) es un artefacto del propio comando. Pero el `pin_error: context canceled` que
aparece en `peer_map` es **otro contexto**: el de Cluster, y su procedencia nunca se
estableció. El documento los agrupa y concluye que el texto no es una causa —cierto para
el primero, no demostrado para el segundo.

Hay un mecanismo plausible y concreto. La promoción se pide desde `dark-store-api`
(`POST /pins/{cid}?replication-min=2&replication-max=2`, en
`app/backends/ipfs_cluster.py`), con el `httpx.AsyncClient` del backend a 30 segundos por
defecto y sin configuración propia en `store-api.env`. La respuesta de ese endpoint de
Cluster es un flujo que termina cuando el pin termina; si el segundo réplica no puede
recuperar los bloques, el pin no termina, el timeout del cliente vence, httpx cancela la
petición y Cluster registra `pin_error: context canceled`. En `_promote_cid`, esa
cancelación se clasifica como error de transporte: se marca el endpoint como fallido, se
reintenta contra el *otro* endpoint y el resultado devuelto es `promotion_requested`. Es
decir, la Store API puede informar éxito mientras Cluster registra un pin fallido, y el
endpoint documenta "without waiting for pins" en un camino que sí espera.

Prueba que lo decide, sin nada de Bitswap de por medio: **parar el Kubo de B y promover un
CID**. Si aparece `pin_error: context canceled` a los ~30 segundos, el error es del cliente
de promoción y no del transporte de bloques. Si en cambio el pin se queda en `pinning`
indefinidamente, el error pertenece a Cluster.

Por qué importa: el criterio de cierre 6 del documento ("sin `context canceled` ni
reintentos indefinidos") seguiría fallando después de arreglar Bitswap, y el plan de
próximos pasos no lo cubre en ninguna línea. Son dos defectos, no uno, y el segundo tiene
dueño claro en el repositorio.

---

## 7. Próximos pasos: qué mantener y qué redirigir

**Paso 1 del documento** (reproducción mínima fuera de Cluster). Mantener la idea,
redirigir la variable: orden de conexión, no claves de configuración (§3.6). Tal como está
—"la misma secuencia de bootstrap del entrypoint"— reproduce la misma carrera y volverá a
fallar sin enseñar nada nuevo. Ya está escrito: `scripts/ipfs-bitswap-probe.sh isolated`.

**Paso 2** (comparar con el entrypoint: `autoconf-off`, routing DHT LAN, `AppendAnnounce`,
bootstrap resuelto por API). Retirar de momento. Es el eje equivocado mientras no haya un
resultado que muestre dependencia de configuración; el fallo ya ocurre en dos
configuraciones muy distintas, incluida una sin PNET. Si 3.6 confirma el mecanismo, este
paso desaparece entero.

**Paso 3** (trazas). Mantener, con dos correcciones: especificar subsistemas y capturar
desde el arranque, no a posteriori; y añadir el volcado de goroutines.

**Paso 4** (abrir incidencia a Kubo). Redirigir. El arreglo ya existe aguas arriba
(#1201), así que una incidencia nueva se cerrará como duplicada. Lo que sí es material
nuevo y valioso es aportar a ese PR (y a la incidencia que enlaza) una reproducción
determinista en Docker con PNET: dos Kubo, `partners` a 0 con la lectura en vuelo,
contadores a cero, proveedor descubierto y ausencia de WANT. Eso convierte un flake de CI en un caso
reproducible, que es exactamente lo que falta allí.

**Paso 5** (cambio mínimo en el renderer o el entrypoint). Redirigir: la corrección no es
de configuración. El orden real de preferencia es (a) anclar la imagen a un Kubo cuyo
`boxo` contenga el arreglo —verificar la matriz `boxo`/Kubo en el momento de decidirlo,
porque hay que confirmar que existe una release que lo incluya y puede que haya que
construir imagen propia—; (b) solo si (a) no es viable, un paliativo explícito y
**verificado**: un wrapper de reconexión con desconexión real, que además compruebe el
resultado en lugar de confiar en él. Si los tres primeros puntos dan resultado, (b) deja
de ser necesario.

**Paso nuevo** (no está en el documento): separar y arreglar el `context canceled` de la
promoción de Cluster (§6).

**Y antes que nada**: 3.1, 3.2 y 3.3 son cinco minutos de trabajo sobre un despliegue que
ya está en pie. Ninguno de los pasos anteriores debería empezar sin ese resultado.

---

## 8. Criterios de cierre y cobertura en el repositorio

Los criterios de cierre son buenos. Añadiría dos exigencias, porque el fallo es una
carrera de arranque: una **prueba negativa** (con el orden de conexión invertido a
propósito, el flujo debe fallar; de lo contrario la prueba no demuestra nada) y **N
repeticiones**, ya que en una red real la carrera es probabilística aunque en Docker sea
casi determinista.

Sobre la cobertura, hay un hueco concreto y verificable. `components/dark-ipfs/scripts/smoke-test.sh`
añade el contenido a través del proxy local de Cluster y luego lo lee con `ipfs cat` **en el
mismo nodo**: el bloque siempre está local, así que ese smoke test pasa aunque la
replicación esté rota. No puede detectar esta clase de fallo por construcción. El notebook
de aceptación (`notebooks/scenarios/local-ha-deposit-lifecycle.ipynb`) sí detecta el
síntoma —`wait_for_replication` imprime exactamente la línea `total=… assigned=… pinning=… queued=…` del documento y lanza excepción si hay `error_replicas`— pero solo lee la vista
de Cluster. Los criterios 4 y 5 del documento (leer el CID desde B, verificar bytes y
bloques Bitswap en ambos sentidos) no están cubiertos en ninguna parte automatizada. Añadir
esas dos aserciones desde el segundo contenedor Kubo es la forma de que este incidente no
pueda repetirse en silencio.

---

## 9. Riesgos de aplicar una solución sin la prueba

Una reconexión ciega reproduce el wrapper que ya se retiró y que "a veces" funcionaba:
exactamente el tipo de parche que deja un fallo intermitente y caro de diagnosticar la
próxima vez. Tocar los knobs de Bitswap que se probaron (`Libp2pEnabled`, `ServerEnabled`,
`BroadcastControl`) no puede arreglar un deseo que nunca se crea, y contamina volúmenes
persistentes. Y subir de versión sin verificar la matriz `boxo`/Kubo puede además introducir
cambios de comportamiento ajenos al incidente —por ejemplo, Kubo v0.43 convierte la
combinación PNET con `Routing.Type=auto` en error de arranque; aquí usan `dht`, así que no
aplica hoy, pero conviene saberlo antes de tocar ese perfil.

---

## 10. Resultado de la primera prueba discriminante (2026-09-14)

Se ejecutó `scripts/ipfs-bitswap-probe.sh inplace` sobre `dark-operator-local-ha` en pie,
con los dos Kubo descubiertos por etiquetas de compose
(`…-storage-2-ipfs-storage-b-1` y `…-storage-1-ipfs-storage-a-1`). Salida en
`.generated/probe-inplace.txt`; código de salida 0. Esa primera pasada contenía un defecto
en el rebote: invocaba `ipfs swarm disconnect <peerID>` con el peer ID desnudo. Kubo exige
un multiaddr, por ejemplo `/p2p/<peerID>`; el comando falló y el `connect` siguiente fue un
no-op sobre la conexión todavía viva. Por tanto, esa pasada no prueba una cura.

**Resultado válido tras reinicio y rebote manual.** Se reiniciaron únicamente Kubo A y Kubo
B. Tras el arranque, B → A volvió a colgar con un CID nuevo, proveedor visible y
`partners [0]`; en sentido A → B sí hubo transferencia de 81 B. El ping libp2p, repetido
después de que el probe informara un fallo transitorio, funcionó en ambos sentidos.

Se aplicó entonces el rebote válido en B:

```bash
ipfs swarm disconnect /p2p/<peerID-A>
ipfs swarm connect /dns4/ipfs-storage-a/tcp/4001/p2p/<peerID-A>
```

La misma lectura B → A devolvió inmediatamente `Key` y `Size: 81`. Los contadores finales
mostraron en ambos nodos un bloque enviado, uno recibido y 81 B transmitidos y recibidos.
Con los mismos demonios y el mismo CID, el único cambio fue un evento de conexión efectivo.
Esto confirma de forma fuerte el mecanismo de registro de conexión de Bitswap.

**Limitaciones y correcciones del probe.** La pasada inicial tuvo cuatro defectos, tres del
script y uno de la medición:

- El contenido del bloque de diagnóstico era fijo, así que ambos nodos produjeron el mismo
  CID: la segunda dirección leyó un bloque que ya tenía localmente y su `ok` no mide
  ninguna transferencia (coherente con el `findprovs: unexpected answer` de esa línea).
  Corregido: el contenido incluye ahora el nombre del nodo y un sufijo aleatorio.
- `partners` se muestreaba *después* de la lectura, y en una transferencia que ya terminó
  vale 0 aunque haya funcionado: esta pasada lo mostró a 0 también en los casos correctos.
  La firma `partners [0]` del diagnóstico original sólo significa algo **mientras la
  lectura está en vuelo**; el script muestrea ahora en ese momento.
- El rebote de la segunda dirección usó `172.30.0.3:57924`, un puerto efímero que quedó en
  el peerstore por una conexión entrante; ahora se prefiere el nombre `/dns4` o el puerto
  estándar 4001.
- El `disconnect` usaba un peer ID desnudo, que Kubo rechaza. El script ahora usa
  `/p2p/<peerID>` y falla de forma explícita si no logra desconectar, para no convertir un
  `connect` no-op en una falsa confirmación.
- La subida del nivel de log en caliente no produjo ninguna línea de las que discriminan,
  incluida `Found peer for CID`, que el diagnóstico anterior sí capturó con Bitswap en
  debug. O el cambio de nivel no se aplicó, o el daemon no está escribiendo a
  `docker logs`. El script imprime ahora el nivel leído tras el cambio y el total de
  líneas de log desde el inicio de la prueba, para que una sola ejecución lo resuelva.

**Lo que falta para cerrar §2.** Que el fallo *reaparezca* tras reiniciar los dos Kubo
—control positivo— y que se vuelva a curar con el rebote, ya con CIDs distintos en cada
dirección. Si no reaparece, la causa no es el orden de arranque y hay que volver al volcado
de goroutines.

Si reaparece, la consecuencia operativa es más incómoda que el diagnóstico: la replicación
se rompería en cada arranque en frío del stack y quedaría rota hasta que algo forzara una
conexión nueva, sin señal visible en Cluster más allá del `pinning` que no termina. Eso
convierte el ancla de versión (o un paliativo de reconexión verificado, §7) en algo que hay
que decidir, no en una mejora opcional.

## Apéndice. Cómo se verificó

- Documento revisado: lectura completa.
- Repositorio: `components/dark-ipfs/scripts/ipfs-entrypoint.sh` y `cluster-entrypoint.sh`
  (fuente y copia generada, comparadas byte a byte); historial git del componente
  `dark-ipfs`, incluido el diff de `e3545cf` que retira el wrapper; `deployment_v3/services.py`
  (pin de versiones); `examples/deployment-v3/local-ha.json` y `examples/operator-inventory/local-ha.json`;
  bundle generado (`compose.yaml`, `env/ipfs-storage-{a,b}.env`, `storage-endpoints.json`);
  `components/dark-store-api/app/backends/ipfs_cluster.py`, `app/api/store.py`, `app/config.py`;
  `components/dark-ipfs/scripts/smoke-test.sh`; `notebooks/scenarios/local-ha-deposit-lifecycle.ipynb`;
  `tests/`.
- Fuentes externas: `ipfs/boxo` PR #1201 (descripción, diff y fecha de fusión),
  `bitswap/network/bsnet/ipfs_impl.go` y `bitswap/client/internal/peermanager/peermanager.go`
  en `boxo` v0.40.0 (la versión que fija `kubo` v0.42.0), `bitswap/client/internal/session/session.go`
  y `sessionwantsender.go`, `routing/providerquerymanager`, issues abiertas de `ipfs/boxo`
  #70 y #83, `ipfs/kubo` #9175, y los hilos equivalentes en `discuss.ipfs.tech`
  (dos nodos en red privada con `partners [0]` y `findprovs` correcto).
- Ejecutado contra Kubo real: la prueba discriminante de §3.6 en modo `inplace` la corrió
  el operador sobre `dark-operator-local-ha` (resultado en §10). No la ejecuté yo: mi
  entorno de sesión no tiene CLI de Docker, ni socket, ni acceso de red al daemon. La
  cura está medida; la reproducibilidad tras un reinicio y la evidencia de log de que no
  se envió ningún WANT siguen pendientes, y por eso la causa de §2 sigue siendo la
  hipótesis líder con una confirmación parcial, no un resultado cerrado.
- `scripts/ipfs-bitswap-probe.sh`: verificado con `bash -n`, con los dos modos en
  `--dry-run` y ejecutado de extremo a extremo contra un `docker` simulado que modela el
  fallo, cubriendo los cuatro desenlaces (confirmado en dos direcciones, confirmado en una,
  no reproducido, y control fallido) y los códigos de salida 0/1/2. La primera ejecución
  real (§10) encontró tres cosas que el banco simulado no podía encontrar, porque el
  simulador reproduce mis supuestos en lugar de contrastarlos: el bloque de diagnóstico
  idéntico en los dos nodos producía el mismo CID y anulaba la segunda dirección, la
  lectura de `partners` estaba fuera de la ventana en la que significa algo, y el rebote
  podía usar un puerto efímero del peerstore. Las tres están corregidas. Queda por
  confirmar en la próxima pasada que el contador de partners se imprime con el formato
  esperado y que el arranque del daemon deja una línea con `broadcast control` (si no, el
  script avisa y continúa).
  La primera versión del script usaba arrays asociativos y `declare -g`, y falló en el
  macOS del operador, cuyo `/bin/bash` es 3.2: una verificación hecha en un entorno con
  bash 5.1 no puede detectar esa clase de incompatibilidad, y por eso la pasó por alto.
  El script está ahora limitado a bash 3.2 —sin arrays asociativos, sin `declare -g`, con
  el estado por nodo en ficheros y una sola trampa de salida—, pero esa comprobación se
  hizo por inspección del código, no ejecutándolo en bash 3.2.
- Fechas y versiones posteriores a mayo de 2025 proceden de consulta web del 2026-09-14 y
  deben re-verificarse antes de decidir un bump de imagen.

**Fuentes:**
- [ipfs/boxo PR #1201 — fix(bitswap): see peers connected before startup](https://github.com/ipfs/boxo/pull/1201)
- [ipfs/boxo issue #83 — Failure to fetch content from connected nodes](https://github.com/ipfs/boxo/issues/83)
- [ipfs/boxo issue #70 — Question about sendWants and send blocks in peermanager](https://github.com/ipfs/boxo/issues/70)
- [ipfs/kubo issue #9175 — bitswap network Notify Event may be registered after connect](https://github.com/ipfs/kubo/issues/9175)
- [discuss.ipfs.tech — Private network with 2 nodes: unable to broadcast new CID](https://discuss.ipfs.tech/t/private-network-with-2-nodes-unable-to-broadcast-new-cid-to-the-other-node/11952)
- [discuss.ipfs.tech — Unable to get blocks when the node is connected to multiple peers](https://discuss.ipfs.tech/t/unable-to-get-blocks-when-the-node-is-connected-to-multiple-peers/18711)

## Addendum 2026-09-14 — candidato Kubo con el arreglo de Boxo

Se descargó y probó la imagen oficial de desarrollo
`ipfs/kubo:master-2026-08-17-a73e8c0` (Kubo `0.44.0-dev-a73e8c0`, digest
`sha256:c11759f51eca6af1b2e45d93125af9058b63925704821bd37477449e3f3d9411`),
posterior a la fusión de Boxo #1201. No se cambió el `local-ha` activo.

El control aislado con dos Kubo, PSK privada, DHT y AutoConf desactivado transfirió bloques
raw en ambas direcciones cuando la conexión se hizo después del arranque: `findprovs` vio el
proveedor y los dos `ipfs block stat` devolvieron `Key` dentro de 3 s. Los logs del build dev
mostraron `bitswap/connevtman: PeerConnected notification` y `bitswap/bsnet:
NewMessageSender`, que faltaban en la 0.42 afectada.

No es aún una confirmación válida del caso decisivo *bootstrap antes de Bitswap*. Al endurecer
el banco aparecieron defectos propios: el contenedor oficial declara `/data/ipfs` como volumen
(un `repo.lock` de una pasada interrumpida contaminaba la siguiente) y, al aislarlo con tmpfs,
el script reutilizaba IDs/CIDs de la fase previa. Ambos están corregidos en
`scripts/ipfs-bitswap-probe.sh`; una ejecución posterior siguió teniendo una carrera de
arranque del arnés, así que no se atribuye su resultado negativo a Kubo.

Conclusión operativa: es un candidato técnicamente compatible y con señal directa de que
registra conexiones Bitswap, pero **no se debe promover a producción todavía**. El siguiente
paso es repetir solamente la fase bootstrap con un único proceso limpio y conservar los logs;
si transfiere sin rebote, fijar esta imagen por digest en un inventario de ensayo y añadir
`AutoConf.Enabled=false` explícito al entrypoint, pues esta familia rechaza el endpoint
público de AutoConf en un swarm privado.

### Preparación del canario en el deployer

El canario quedó preparado en código, pero no desplegado por decisión del operador. El
renderer fija ambos servicios Kubo a la imagen y digest anteriores, añade
`IPFS_TELEMETRY=off`, y el entrypoint declara explícitamente `AutoConf.Enabled=false` y
vacía `DNS.Resolvers`, `Routing.DelegatedRouters` e `Ipns.DelegatedPublishers`; conserva el
perfil `autoconf-off` sólo como compatibilidad con versiones que todavía lo incluyen.

Validación offline realizada: sintaxis POSIX del entrypoint, `git diff --check`, 50 pruebas
de deployment V3 y operator inventory, preflight Docker/Compose/arm64 y render completo del
inventario `examples/operator-inventory/local-ha.json`. El render contiene el digest y la
telemetría desactivada en `ipfs-storage-a` e `ipfs-storage-b`. Al cerrar esta preparación,
los dos contenedores activos seguían sanos y ejecutando `ipfs/kubo:v0.42.0`; no se recreó
ningún servicio.

Para aplicar el canario cuando el operador decida probarlo:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json \
  --skip-acquire --refresh-bundle --verbose
```

Después deben verificarse la versión de ambos Kubo, salud, peers de swarm y Cluster y una
replicación nueva de extremo a extremo. No reutilizar un CID ya presente en ambos nodos.

### Validación del despliegue aplicada por el operador — 2026-09-14

El operador aplicó el canario y se verificó el entorno en ejecución. Ambos contenedores Kubo
están `healthy` y ejecutan `ipfs version 0.44.0-dev`; ambos peers de IPFS Cluster responden y
cada uno ve al otro. La configuración efectiva devuelve `AutoConf.Enabled=false` y
`DNS.Resolvers=null`.

El probe `inplace` utilizó CIDs nuevos y terminó correctamente en los dos sentidos:

```text
storage-b -> storage-a: ok; después de reconectar: ok
storage-a -> storage-b: ok; después de reconectar: ok
```

Antes de cualquier rebote ambos fetches ya funcionaban, por lo que el fallo anterior no se
reprodujo en el despliegue corregido. Durante la prueba los logs registraron, en ambos nodos,
`PeerConnected notification` (2), `new queue for` (2), `Found peer for CID` (1),
`Added peer to session` (1) y `WANT_BLOCK` (2), sin ningún `cannot reserve`. Los bloques raw
temporales fueron eliminados automáticamente al terminar; no se dejaron datos de diagnóstico.

Los contadores finales fueron coherentes con una transferencia real: cada nodo mostró un
bloque recibido y uno enviado, `wantlist [0 keys]`, y 80/81 B transmitidos según la dirección.
Conclusión: el fix queda validado en el `local-ha` Docker actual para la reproducción de
Bitswap. Falta únicamente la prueba prolongada de aceptación de Cluster/Store sobre un flujo
de aplicación real y decidir, fuera de este canario, la promoción de la imagen a un canal
estable cuando exista una release que incluya el arreglo.

### Centralización posterior de versiones de runtime

Después de validar el canario se retiraron los pines de imágenes del código Python y del
probe. La única fuente de versiones de runtime es ahora `base.images` en
`deployment_v3/catalog_data/dark-platform-baseline-v1.0.json`: Besu, Kubo, IPFS Cluster, PostgreSQL,
MySQL, Redis, Dashboard y edge proxy. El renderer, las tareas auxiliares de permisos y la
generación de artefactos Besu consumen ese bloque; el probe aislado también toma de allí su
imagen Kubo por defecto. Una clave ausente hace fallar la validación en vez de aplicar un
fallback oculto.

Los inventarios compactos heredan las imágenes al resolver el catálogo y los inventarios
full existentes se normalizan contra el mismo catálogo instalado. Se eliminaron sus antiguos
campos `blockchain.besu_image` para evitar copias divergentes. Validación posterior: 51 tests,
todos los ejemplos full y compactos, sintaxis del probe y `git diff --check`, sin pines de las
imágenes gestionadas fuera de `dark-platform-baseline-v1.0.json`. Este refactor no recreó contenedores;
el despliegue canario ya activo conserva exactamente las mismas imágenes.
