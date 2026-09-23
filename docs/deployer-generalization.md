# Deployer generalization: síntesis del análisis

- **Fecha:** 2026-09-16
- **Ampliación y revisión crítica:** 2026-09-17
- **Estado:** Documento de diseño (síntesis — no implementa nada)
- **Alcance:** Distancia entre el deployer actual y un sistema capaz de incorporar componentes
  arbitrarios (Java/Spring Boot, Solr, VuFind, …) **describiéndolos** y desplegarlos nombrándolos
  en un inventario.
- **Relacionado:** `docs/deployment-v3-inventory-and-artifact-flow.md`,
  `docs/old/deployment-v3-operator-inventory-proposal.md` (archivado)

> Este documento sintetiza tres rondas de análisis sucesivas. Para evitar el debate escenificado,
> las afirmaciones se clasifican en tres categorías: **firmes** (verificadas en código o con las que
> hay acuerdo razonado), **bifurcaciones abiertas** (decisiones reales sin ganador obvio, que hay que
> tomar antes de implementar) y **descartadas** (afirmaciones previas que se retiraron por
> sobreestimadas o falsas).

### Cómo leer esta versión ampliada

Se conserva el contenido de las rondas anteriores, incluidos sus bosquejos y argumentos, para
no perder el razonamiento. La estrategia inicial fija la **dirección arquitectónica**; no convierte
automáticamente cada ejemplo posterior en un contrato implementable. Las secciones 3, 8 y 10
conservan preguntas históricas: sus alternativas principales tienen una elección estratégica,
pero algunos detalles siguen abiertos.

Las secciones **16–24** amplían y matizan esa estrategia: delimitan la reutilización real,
concretan conexiones, hooks, ejecución y validación, y proponen una secuencia revisada. Cuando
una afirmación anterior resulte más categórica que estas precisiones, prevalece la formulación
de la ampliación. Sus propuestas de formato son **candidatas para validación**, no APIs existentes
ni decisiones ya aprobadas de implementación. En particular, los ejemplos de las secciones 11,
13 y 14 siguen siendo ilustrativos y todavía no acreditan equivalencia funcional con dARK.

| Estado | Contenido |
| --- | --- |
| Dirección elegida | Núcleo reutilizable, catálogos, Compose + manifiesto, extracción gradual |
| Alcance inicial | Instalación y recuperación explícitas; sin reconciliador continuo |
| Contratos por validar | Binding de capacidades, endpoints múltiples, hooks, merge, lifecycle y artefactos |
| Evidencia pendiente | Corte vertical no-dARK y equivalencia funcional del catálogo dARK |

La composición de varios catálogos, incluida en la sección 10, **no queda resuelta** por elegir
Compose ni la extracción in-place. La sección 24 propone un alcance inicial para esa decisión.

---

## Estrategia elegida

Convertir el núcleo genérico del deployer en un **orquestador multi-host de Docker reutilizable**,
con un **lenguaje de componentes basado en Compose + un manifiesto lateral**, y dejar **dARK como
el primer catálogo** que lo usa. El punto final no es "un deployer más flexible" sino "un
orquestador reutilizable + un lenguaje de componentes + catálogos", donde dARK es un catálogo más.

### Cierre de las tres bifurcaciones (con el criterio único: genéricidad + reusabilidad)

**Control (3.A) → lenguaje agnóstico al modelo de control.** La definición de un componente
(provides/requires, plantillas de env, sondas, ciclo de vida) es la misma tanto si el apply es una
instalación única como si es reconciliación continua. Se mantiene el control loop de instalación
actual; el drift queda fuera de alcance *por ahora*; pero el lenguaje no asume "topología
completa", de modo que un reconciliador futuro reutiliza la capa de componentes sin rediseñarla.

**Formato (3.B) → fragmento Compose + manifiesto lateral.** La spec del contenedor es Compose (un
estándar que el ecosistema ya produce); el manifiesto lateral lleva lo que Compose no puede
expresar: provides/requires, plantillas de env, sondas, categoría y hooks. El fragmento es *por
servicio*, no por stack, así que la limitación multi-host de Compose no aplica: el orquestador
cose fragmentos entre hosts, como ya hace hoy. Esto evita reinventar la descripción del contenedor
(el patrón Helm que hay que evitar).

**Encaje (3.C) → extracción in-place preservando comportamiento con snapshots dorados.** Un
paquete paralelo puro produce dos codebases —la forma en que muere un proyecto de un mantenedor—.
La extracción in-place mueve el núcleo genérico a una capa inferior y deja lo dARK como catálogo
encima; es modificar el existente *en una dirección*, sin romperlo. **Exige relajar la preferencia
"nunca tocar el existente" a su lectura razonable: "nunca romper el comportamiento existente".**
Los snapshots dorados (13 inventarios → v3 resuelto → plan → bundle) son la garantía.

### Las dos piezas de diseño reales (comprometidas)

- **Capacidades como interfaces con versión y regla de compatibilidad.** No matching por nombre
  libre (falsa composabilidad) ni un sistema de tipos completo el primer día. Empezar con nombres
  versionados y un predicado de compatibilidad por `requires`.
- **Motor de plantillas de env restringido** sobre los endpoints resueltos
  (`jdbc:{{scheme}}://{{host}}:{{port}}/{{db}}`), sin código arbitrario: solo interpolación de
  campos del endpoint.

### Sondas y hooks

Librería declarativa (`tcp`, `http`, `jsonrpc`, `command`) para lo genérico. Los predicados
derivados de dARK (quórum, lag, convergencia de membresía) se quedan como **hooks** del catálogo
dARK, no se fuerzan al esquema. Esto mantiene el núcleo genérico limpio sin mutilar dARK.

### dARK como primer catálogo

Las piezas dARK se reescriben en el nuevo formato (fragmento Compose + manifiesto + hooks). El
código dARK específico que hoy vive en `services.py`/`render.py`/`readiness.py` pasa a ser datos y
hooks del catálogo. Ese dogfooding es la prueba de genéricidad.

### Secuencia

1. **Snapshots dorados** de los 13 ejemplos (inventario → v3 → plan → bundle). Sin tocar
   funcionalidad.
2. **Extraer el núcleo genérico in-place**: `SERVICE_TYPES`, `CONNECTION_CONTRACTS`, `PHASES`,
   `DEFAULT_PORTS` dejan de ser literales Python y se cargan desde el catálogo. Snapshots en verde.
3. **Definir el formato del manifiesto** y escribir en él Solr, Spring Boot y VuFind.
4. **Render/apply/readiness/verify consumen el manifiesto** en vez de ramificar por tipo; las
   piezas dARK se convierten en manifiestos con hooks.
5. **Criterio de éxito**: Solr + Spring Boot + VuFind se despliegan sin tocar código; los módulos
   genéricos no contienen ningún sustantivo dARK; el editor y los esquemas se derivan del catálogo.

### Costes aceptados (honestos)

- No haber "añade un servicio a un stack que ya corre" hasta un reconciliador futuro.
- Heredar las rarezas de Compose.
- Relajar "no tocar ficheros" a "no romper comportamiento".
- Construir y asegurar un motor de plantillas pequeño.

### Por qué es la mejor para "genérico y reutilizable"

- **Genérico**: cualquier cosa con un Compose encaja; las interfaces de capacidad componen; sondas
  + hooks cubren tanto piezas simples como ricas.
- **Reutilizable**: el núcleo extraído es un orquestador sin dARK, reutilizable por otros
  catálogos; la capa de componentes es agnóstica al control loop (un reconciliador futuro la
  reutiliza); dARK como primer catálogo demuestra la historia de reusabilidad.

> Las secciones 3 (bifurcaciones), 8 (orden) y 10 (preguntas) de abajo quedan **resueltas** por esta
> estrategia. Se conservan como respaldo del razonamiento.

---

## 1. Resumen

El deployer tiene resuelto lo caro de un despliegue declarativo multi-host sobre Docker plano
(topología, redes, peerings, secretos, staging, bundle determinista, locks, resume). Lo que no
tiene es una **capa de componentes como datos**: la especificación de cada contenedor vive en
código Python, y el catálogo solo puede nombrar tipos que el código ya sabe construir. Por eso hoy
es imposible añadir un componente fuera del stack dARK — no por acoplamiento difuso, sino por un
puñado de barreras verificadas — y por eso el objetivo requiere un **cambio arquitectónico**
(extraer la capa de componentes a datos), no un cambio de vocabulario.

La síntesis deja tres decisiones de producto/arquitectura sin tomar (sección 3) que condicionan
todo lo demás y conviene tomar **antes** de diseñar el esquema. El resto (qué campos necesita la
definición de componente, cómo queda el pipeline, qué probar) es firme y relativamente
independiente de esas decisiones.

---

## 2. Lo que es firme

### 2.1 El esqueleto genérico ya está y es el activo caro

Capacidades reutilizables tal cual: derivación de endpoints por red/transporte (`network.endpoint_for`),
exposiciones `loopback/private/public` + listeners multi-red, distribución de secretos indexada por
servicio (no por tipo), staging SSH, bundle determinista con manifest SHA-256, locking, resume por
fingerprint, orden topológico de `_layers`, y el modelo de topología declarada (`sites`, `routing`,
`routes`, `proxies`, `placement`).

### 2.2 La capa de componentes no existe como datos (hallazgo central)

Contenido real de una entrada del catálogo
(`catalog_data/dark-platform-baseline-v1.0.json`, `base.services.*`):

```
minter-api      -> ['type', 'machine', 'connections']
resolver-api    -> ['type', 'machine', 'connections']
minter-postgres -> ['type', 'machine']
```

No hay imagen, build, command, volúmenes, env, puertos ni sondas. La spec del contenedor vive en
código: el compose en `services.py:96-182` (if/elif por tipo), el env en `render.py:123-240`
(`_env`, if/elif por tipo), los puertos en `network.DEFAULT_PORTS`. El catálogo es una **receta de
composición**, no una biblioteca de componentes. Esto explica todas las barreras de la sección 2.3.

### 2.3 Las barreras verificadas (no se puede añadir un componente hoy)

Declarar `"type": "solr"` produce esta cadena, verificada en el código.

**Barreras duras:**

| # | Localización | Qué ocurre |
| --- | --- | --- |
| 1 | `inventory.py:30` (`SERVICE_TYPES`) | `frozenset` cerrado → *"type is unknown: solr"* |
| 2 | `inventory.py:679, 705-708` (`CONNECTION_CONTRACTS`) | El contrato se busca por tipo; un tipo nuevo devuelve `{}` y la regla exige `set(connections) == set(contract)`. **Un componente nuevo está obligado a declarar cero conexiones** |
| 3 | `inventory.py:718-721` (tipos requeridos) | Todo inventario debe contener `besu-rpc`, `minter-api`, `minter-postgres`, `store-api`, `admin-api`, `resolver-api`, `dashboard`, `ipfs-kubo`, `ipfs-cluster`. Un stack ajeno se rechaza por *no tener un minter-api* |
| 4 | `planner.py:54-56, 68` (`PHASES`) | `phase_order[item.type]` → **KeyError 'solr'** |
| 5 | `services.py:96-182, 179` | Cadena `if/elif` sin `else`: el servicio no se construye y revienta en `result[service.id]["labels"]` → **KeyError** |

**Huecos silenciosos:**

| # | Localización | Qué ocurre |
| --- | --- | --- |
| 6 | `render.py:123-240` (`_env`) | Sin `else` de nivel superior: un tipo nuevo recibe solo `DARK_DEPLOYMENT_ID`, sin variables de sus dependencias |
| 7 | `network.py:10-16, 40` (`DEFAULT_PORTS`) | `.get(type, 0)` → puerto interno 0 |
| 8 | `readiness.py`, `verify.py` | Comprobaciones por tipo: un componente nuevo no se verifica |
| 9 | `inventory_editor/textual_app.py` (`_choices`) | Vocabulario cerrado de tipos en el editor |

**Acoplamiento medido por líneas** (sustantivos dARK por módulo): `services.py` 31 %, `render.py`
30 %, `inventory_resolver.py` 19 %, `artifacts.py` 18 %, `verify.py` 16 %, `inventory.py` 13 %,
`readiness.py` 13 %, resto ≤ 10 %, plumbing (`model`, `state`, `acquire`, `executor`) 0–2 %. La
tabla **subestima el bloqueo** — una lista cerrada son pocas líneas y bloquea todo —, por eso la
tabla es apoyo, no diagnóstico.

### 2.4 Es un cambio arquitectónico, no de vocabulario

Renombrar tipos es vocabulario; aceptar componentes que el sistema no conoce exige sacar la capa de
componentes del código a los datos y abrir el sistema de tipos. La afirmación anterior de que era
"un cambio de vocabulario" se descarta (sección 4).

### 2.5 dARK se encapsula, no se generaliza

QBFT, artefactos de cadena, contratos y settings del minter quedan como piezas ricas del catálogo
dARK. El lenguaje describe *qué* se combina; las piezas ricas traen su *cómo* mediante artefactos y
hooks. El lenguaje no debe intentar expresar la inicialización de una cadena.

### 2.6 Las capacidades son un sistema de interfaces; el env es un motor de plantillas

Dos costes de diseño reales que conviene no infravalorar:

- **Capacidades ≠ matching por nombre.** `provides: jdbc-database` desde Postgres 14 y desde MySQL 8
  no son la misma cosa; un driver de Spring Boot no habla igual a ambos. El matching por nombre da
  falsa composabilidad. Hace falta un sistema de interfaces con reglas de compatibilidad
  (versionado, qué proveedores satisface cada `requires`). Es trabajo de tipos de verdad.
- **El cableado de env es templating, no `as_env`.** Spring Boot quiere
  `SPRING_DATASOURCE_URL=jdbc:postgresql://host:5432/db` —una URL construida con esquema, host,
  puerto y base de datos—, y Solr quiere `SOLR_ZK_HOST=host:2181` con otra forma. El componente
  debe declarar **plantillas** sobre los endpoints resueltos: `jdbc:{{scheme}}://{{host}}:{{port}}/{{db}}`.
  Decidir ese motor (¿Jinja? ¿estricto? ¿inyección de secretos en plantillas?) es una decisión con
  riesgo, no un campo más del esquema.

### 2.7 Los tests actuales resistirán el refactor; caracterizar con snapshots

Los tests existentes son de *comportamiento dARK*, no del pipeline genérico. En cuanto se abra el
sistema de tipos se rompen en masa, y "mantenerlos verdes" se vuelve una restricción que **pelea**
contra la generalización (cada refactor debe preservar el comportamiento dARK exacto). La jugada
correcta es caracterizar primero el comportamiento actual como *snapshots dorados*
(inventario → v3 resuelto → plan → bundle) para verificar refactor estructuralmente, y escribir
tests nuevos genéricos con el corpus de la sección 7.

### 2.8 Escepticismo justificado sobre la reutilización de activos

La mecánica P2P actual (`peerings`, `p2p_edges`, `p2p_bindings`) modela una malla simétrica
`from/to/address/port` con semántica de enode de Besu. Que la *forma* se parezca a la replicación
de Solr/Cassandra/Kafka no implica que el *modelo* sirva: la replicación de Solr es
leader/follower con una URL de master, no una malla simétrica. **No asumir reutilización sin
verificarlo con un caso real.**

---

## 3. Bifurcaciones abiertas (decisiones que condicionan todo, sin ganador obvio)

Estas tres decisiones hay que tomarlas **antes** de diseñar el esquema, porque determinan su forma
y su coste. Ninguna tiene una respuesta clara; presentarlas como resueltas sería engañoso.

### 3.A Instalador de topologías vs. reconciliador continuo

Toda la arquitectura actual (lock, render del bundle entero, iteración de pasos, resume por
fingerprint) está construida alrededor de *"instalas una topología completa"*. El apply
incremental y la reconciliación de drift no son una funcionalidad que se añade a ese modelo: son
*otro* modelo de control. Pretender tener los dos extendiendo el primero produce un híbrido frágil.

Si el producto es "instalador de topologías estáticas multi-host", el drift queda fuera de alcance
— y el caso "añade un servicio a un stack que ya corre" no se soporta. Si el producto es
"reconciliador continuo", se está diseñando otro sistema y el deployer actual es punto de partida,
no base a extender. Verificado: no existe lógica de apply incremental ni reconciliación en
`deployment_v3` (solo `--resume` con fingerprint).

### 3.B Esquema propio vs. fragmentos Compose + manifiesto lateral

Dos formas de la definición de componente, ambas con costes reales:

- **Esquema propio + intérprete** (lo que sugieren las rondas previas): `services.py`/`render.py`
  se reescriben como intérpretes de un DSL. Coste: construyes un motor de plantillas de Compose —
  es el patrón de Helm, que la comunidad detesta precisamente por eso.
- **Fragmentos Compose + manifiesto lateral**: la definición es un `docker-compose.yml` (la parte
  del contenedor) más un manifiesto diminuto con `provides`/`requires`, plantillas de env y sondas.
  El deployer *cose* fragmentos entre hosts en vez de generarlos. Coste: los fragmentos necesitan
  placeholders (que es templating otra vez, pero sobre un estándar), y Compose no está diseñado
  para multi-host.

"cualquier componente desplegable en Docker" es más literal con la segunda (si tiene un Compose,
lo tienes), pero la primera da más control. **No hay ganador obvio; es una decisión de diseño real.**

### 3.C Paquete paralelo puro vs. extracción in-place

La preferencia registrada del usuario es "lo nuevo va en su propio directorio, nunca modifiques el
código existente". La lectura estricta ("nunca tocar los ficheros existentes") **choca con el
objetivo**: un paquete paralelo puro solo puede envolver `deployment_v3` (y estrellarse contra los
mismos tipos cerrados) o reimplementarlo (dos codebases que mantener — la forma en que mueren los
proyectos de un solo mantenedor). La lectura razonable ("no romper el comportamiento existente")
sí es compatible: extraer el núcleo genérico de `deployment_v3` hacia abajo, dejando lo dARK como
catálogo, es modificar el existente *en una dirección*, preservando comportamiento.

Conviene **aclarar la preferencia** antes de empezar: si "no tocar" significa "no romper", el
camino de extracción es viable; si significa literalmente no editar ficheros, el objetivo se
bloquea o se duplica.

---

## 4. Lo que se descarta (afirmaciones previas retiradas)

- **"Es un cambio de vocabulario, no de arquitectura."** Falso para el objetivo: es arquitectónico.
- **"El 60–70 % del código de dominio está acoplado."** Estimación a ojo; medido por líneas, el
  peor módulo está en 31 % y el acoplamiento es concentrado, no difuso.
- **"La mecánica P2P es un activo reutilizable para Solr/Cassandra/Kafka."** Downgradado a
  "verificar con un caso real"; el modelo puede no encajar.
- **"Consume Compose es mejor y más barato."** Downgradado a "bifurcación abierta sin ganador"
  (sección 3.B).
- **"La preferencia de encapsulación choca duro con el objetivo."** Downgradado a "la lectura
  estricta choca; la razonable no" (sección 3.C).

---

## 5. El lenguaje de componentes (lo que hace falta, independiente de la bifurcación 3.B)

Cualquiera de las dos opciones de 3.B necesita estos campos; la bifurcación solo decide **qué
transporta la spec del contenedor** (un JSON propio o un fragmento Compose + manifiesto).

- **Identidad y versión**: id, versión, digest del catálogo contenedor.
- **Runtime**: imagen o build, command/entrypoint, usuario, volúmenes, recursos.
- **`provides`**: capacidad + puerto + protocolo + transporte + sondeo opcional.
- **`requires`**: capacidades (no tipos), con reglas de compatibilidad (sección 2.6).
- **Cableado de env**: plantillas sobre endpoints resueltos, declaradas por el componente
  (`jdbc:{{scheme}}://{{host}}:{{port}}/{{db}}`).
- **Sondas** de readiness y health: `tcp`, `http`, `command`, `jsonrpc`… con parámetros. Los
  predicados derivados (quórum, lag, convergencia de membresía) necesitan **hooks** o quedan como
  código del catálogo dARK; no forzar el esquema a expresarlos.
- **Ciclo de vida**: servicio largo vs. one-shot (timeout, condición de éxito).
- **Puertos y exposiciones** (modelo ya existente), **secretos consumidos** (ya existente),
  **constraints** de placement (co-localización, exclusividad, dominio de fallo).
- **Categoría/orden**: sustituye a `PHASES`; `planner._layers` ya hace el orden topológico.
- **Ficheros de configuración del componente** (schema de Solr, `solrconfig.xml`, plantillas nginx).

---

## 6. Cómo queda el pipeline

Con la capa de componentes como datos, el flujo no necesita código por componente:

```
inventario nombra componentes
  → resolver: matching requires ↔ provides por capacidad + placement
  → planner: orden topológico (ya genérico) con la categoría declarada
  → render: compose + env emitidos DESDE la definición (o fusionando fragmentos)
  → apply: staging, secretos, redes (ya genérico)
  → readiness/verify: sondas y health declaradas
```

Ninguna etapa necesita conocer "Solr" o "Spring Boot". dARK pasa a ser el primer catálogo que usa
el lenguaje, no el producto.

---

## 7. Corpus de prueba y criterios de éxito

**Corpus:** Spring Boot + Solr + VuFind. Mejor que un stack "fácil" porque es heterogéneo y rompe
supuestos: Spring Boot fuerza el mapeo declarativo de env y el build JVM; Solr fuerza ficheros de
configuración, puertos declarados y, si se replica, verifica si la mecánica P2P encaja o no
(sección 2.8); VuFind fuerza dos consumidores de la misma capacidad (`search-index`) y valida el
matching por capacidad.

**Criterios de éxito medibles:**

1. Un stack no-dARK se despliega con los comandos existentes y sin editar `deployment_v3`.
2. Los módulos genéricos contienen **cero** sustantivos dARK (`planner.py`, `inventory.py`,
   `services.py`, `render.py`, `readiness.py`, `textual_app.py`).
3. El editor y los esquemas JSON se derivan del catálogo, no se mantienen a mano
   (`schema.json` + `operator_schema.json` + `_choices()` son hoy tres vocabularios paralelos).

---

## 8. Orden de trabajo recomendado

1. **Tomar las tres decisiones de la sección 3.** Es lo primero; condicionan la forma y el coste
   de todo lo demás. Sin ellas, cualquier implementación se construye sobre base sin decidir.
2. **Caracterizar el comportamiento actual** como snapshots dorados (inventario → v3 → plan →
   bundle) para los 13 ejemplos existentes. Es la red de seguridad para refactor con preservación
   de comportamiento.
3. **Diseñar el sistema de interfaces y el motor de plantillas de env** (sección 2.6). Es el
   trabajo de tipos de verdad; hacerlo antes de los campos del esquema.
4. **Extraer el núcleo genérico** de `deployment_v3` (según lo decidido en 3.C), preservando
   comportamiento con los snapshots.
5. **Escribir los tres componentes de prueba** (Solr, Spring Boot, VuFind) en el formato decidido
   en 3.B. Cada campo debe responder "¿qué barrera de la sección 2.3 elimina?".
6. **Alcanzar los criterios de la sección 7.**

---

## 9. Riesgos

- **Construir Helm**: si se va por la opción de esquema propio + intérprete (3.B), el motor de
  plantillas de Compose es exactamente el patrón que la comunidad odia de Helm. Mantener la spec
  mínima y apoyarse en Compose donde sea posible.
- **DSL Turing-completo**: el lenguaje no debe intentar expresar inicializaciones complejas. Las
  piezas ricas traen su *cómo*.
- **Hooks**: si las sondas/piezas ricas aportan código (Python importable, scripts, one-shots),
  el "lenguaje" es en realidad un sistema de plugins, con preguntas duras (sandboxing, versionado
  de la API, qué pasa si un hook revienta en el host). Decidir si hay hooks y de qué tipo antes de
  diseñar el esquema.
- **Falsa composabilidad**: matching de capacidades por nombre sin reglas de compatibilidad
  (sección 2.6) deja conectar cosas que no funcionan y el error lo da el runtime en producción.
- **Dos codebases**: si 3.C se resuelve con paquete paralelo puro, el riesgo de mantener dos
  sistemas y estancar el producto es alto para un proyecto de un mantenedor.

---

## 10. Preguntas abiertas

- ¿Matching de capacidades por nombre libre, jerarquía o interfaz con validación de versión?
- ¿Motor de plantillas de env: Jinja, restringido, o un mini-lenguaje propio?
- ¿Hay hooks? ¿Scripts, contenedores one-shot o Python importable? ¿Cómo se versiona su API?
- ¿El apply incremental entra en el alcance desde el principio o el producto se acota a
  instalador de topologías (3.A)?
- ¿Esquema propio o fragmentos Compose (3.B)?
- ¿Extracción in-place o paquete paralelo, y qué significa exactamente "no tocar el existente" (3.C)?
- ¿Un catálogo activo por despliegue o composición de catálogos (base + extensiones)?

---

## 11. Apéndice: bosquejos indicativos (no normativos)

### 11.1 Solr como proveedor de capacidad

```json
{
  "id": "solr",
  "kind": "service",
  "runtime": {"image": "solr:9", "command": ["solr-precreate", "vufind"]},
  "provides": [{"name": "search-index", "port": 8983, "protocol": "tcp", "probe": "http /solr/admin/ping"}],
  "volumes": {"data": "/var/solr"},
  "readiness": [{"type": "http", "port": 8983, "path": "/solr/admin/ping", "status": 200}],
  "category": "data"
}
```

### 11.2 VuFind y Spring Boot como consumidores, con env wiring por plantilla

```json
{
  "id": "vufind",
  "kind": "service",
  "runtime": {"build": {"context": "components/vufind"}, "config_files": ["solr/schema.xml", "config/vufind/*"]},
  "provides": [{"name": "vufind-http", "port": 80, "protocol": "tcp"}],
  "requires": [{"capability": "search-index", "env": {"VUFIND_SOLR_HOST": "{{host}}", "VUFIND_SOLR_PORT": "{{port}}"}}],
  "category": "applications"
}
```

```json
{
  "id": "spring-boot-api",
  "kind": "service",
  "runtime": {"build": {"context": "components/api"}},
  "provides": [{"name": "http-api", "port": 8080, "protocol": "tcp"}],
  "requires": [
    {"capability": "jdbc-database", "env": {"SPRING_DATASOURCE_URL": "jdbc:{{scheme}}://{{host}}:{{port}}/{{db}}"}},
    {"capability": "search-index", "env": {"SPRING_DATA_SOLR_HOST": "{{host}}"}}
  ],
  "readiness": [{"type": "http", "port": 8080, "path": "/actuator/health", "status": 200}],
  "category": "applications"
}
```

### 11.3 Pieza dARK rica expresada en el mismo lenguaje

```json
{
  "id": "besu-validator",
  "kind": "service",
  "runtime": {"image": "{{catalog.images.besu}}", "volumes": ["data:/data", "genesis:/config/genesis.json", "nodekey:/config/nodekey"]},
  "provides": [
    {"name": "p2p", "port": 30303, "protocol": "tcp", "publish_when_remote": true},
    {"name": "jsonrpc", "port": 8545, "protocol": "tcp", "exposure": "loopback"}
  ],
  "p2p_family": {"name": "blockchain", "port_source": "p2p_port_start+index"},
  "artifacts": {"requires": "chain-artifact"},
  "readiness": [{"type": "compose-status", "state": "running"}],
  "category": "validators"
}
```

La prueba del paso 5 de la sección 8 es que estas descripciones basten para producir, con el mismo
pipeline, un plan equivalente al que hoy produce el catálogo cableado en código.

---

## 12. Diseño de arquitectura

La estrategia elegida (sección *Estrategia elegida*) se materializa en tres capas con dependencias
unidireccionales: el núcleo genérico abajo, los catálogos en medio, los inventarios arriba.

```
núcleo genérico  (deployment_core/)          ← orquestador, sin sustantivos dARK
  · loader      carga inventario + catálogo
  · resolver    matching requires↔provides por capacidad + placement + routing
  · planner     orden topológico por dependencias + categoría declarada
  · renderer    mergea fragmentos Compose + inyecta env plantillado + puertos + secretos + config
  · executor    SSH/local, staging, preflight
  · probes      librería de sondas (tcp/http/jsonrpc/command/compose-status) + runner de hooks
  · secrets, state, network, artifacts-generic
        ↑ depende de
catálogos        (catalogs/<id>/)             ← bibliotecas de piezas, datos + hooks
  · catalog.json     imágenes, defaults, registro de interfaces, recetas
  · components/<id>/ component.json + compose.yml + hooks.py + config/
        ↑ nombrados por
inventarios      (operator / v3)              ← instancia: máquinas, placement, routing, proxies
```

**Flujo de un componente en runtime:**

```
inventario nombra "solr" en placement
  → loader resuelve el catálogo activo y encuentra el componente "solr"
  → resolver hace matching: cada requires del componente satisface un provides de otro colocado
  → planner asigna categoría "data" y ordena tras sus proveedores
  → renderer:
      · lee solr/compose.yml (fragmento), rellena {{catalog.images.solr}}, {{data}}, {{secrets_root}}
      · calcula env desde requires: {{host}}/{{port}}/{{scheme}}... → variables
      · añade puertos desde provides (publish_when_remote, exposure)
      · monta secretos y config_files
      · agrupa en el proyecto Compose del host
  → executor aplica (staging + docker compose up)
  → probes corre la sonda http /solr/admin/ping declarada
```

Ninguna etapa conoce "solr". Lo único que conoce el núcleo es el formato del manifiesto.

**Qué se reutiliza tal cual del deployer actual:** `network.endpoint_for`/`derive_endpoints`,
`network.allocate_docker_subnets`, el modelo `exposure`/`listeners`, `secrets.distribute_secrets`,
`executor` (SSH/rsync/preflight), `state`/locking, `runner._stage`, el bundle con manifest, el
resume por fingerprint, `_layers` (orden topológico), y la topología declarada (`sites`, `routing`,
`routes`, `proxies`, `placement`).

**Qué se extrae y se vuelve data-driven:** `SERVICE_TYPES`, `CONNECTION_CONTRACTS`, `PHASES`,
`DEFAULT_PORTS` dejan de ser literales Python y se cargan desde el catálogo (interfaces + categorías
declaradas en cada componente). `services.py:96-182` (ramas por tipo) → mergeador de fragmentos.
`render.py:_env` (ramas por tipo) → motor de plantillas sobre endpoints. `readiness.py`/`verify.py`
(ramas por tipo) → runner de sondas + hooks.

**Qué se queda como código del catálogo dARK (hooks):** los predicados derivados
(`peer_count` para Besu, `observer_lag`, `cluster_membership` para IPFS Cluster) y la inicialización
de cadena (`artifacts.py`, `contracts.py`). El núcleo los llama por contrato; no los conoce.

---

## 13. Formatos

### 13.1 Componente (directorio)

Un componente es un directorio dentro de un catálogo:

```
catalogs/dark-platform-baseline-v1.0/components/minter-postgres/
  component.json     ← manifiesto (la "connection contract" + sondas + lifecycle)
  compose.yml        ← fragmento Compose (la spec del contenedor)
  hooks.py           ← opcional: predicados derivados (solo piezas ricas)
  config/            ← opcional: ficheros bundled (nginx templates, solrconfig, scripts)
```

### 13.2 `component.json` (manifiesto)

```json
{
  "schema_version": 1,
  "id": "minter-postgres",
  "kind": "service",                         // "service" | "one-shot"
  "category": "data",                        // sustituye a PHASES; conjunto abierto
  "runtime": {
    "fragment": "compose.yml",               // referencia al fragmento
    "resources": {"disk_min_gb": 1}
  },
  "provides": [
    {
      "capability": "jdbc-database",         // nombre de interfaz, ver 13.5
      "version": 1,
      "port": 5432,
      "protocol": "tcp",
      "exposure": "private",                 // loopback | private | public | none
      "fields": {"scheme": "postgresql", "database": "minter"}   // metadata para env templates
    }
  ],
  "requires": [                              // cada entrada satisface una capacidad
    {
      "capability": "jsonrpc",
      "min_version": 1,
      "env": {"DARK_RPC_URL": "{{url}}"}     // plantilla sobre campos del endpoint resuelto
    }
  ],
  "secrets": [
    {"id": "minter-runtime-env", "mount": "/run/secrets/minter-runtime-env", "mode": "0600"}
  ],
  "probes": {
    "readiness": [{"type": "tcp", "port": 5432, "timeout_seconds": 30}],
    "health":    [{"type": "tcp", "port": 5432}]
  },
  "constraints": {"colocate_with": ["minter-api"]},   // ver 13.5
  "p2p_family": null,                        // {name, port, port_source} para piezas en malla
  "artifacts": null,                         // {requires: "chain-artifact"} o {produces: ...}
  "hooks": null,                             // {module, api_version, readiness?, verify?, env_extra?}
  "config_files": []                         // rutas relativas al dir del componente
}
```

Campos clave:

- `provides.fields`: metadata del endpoint (scheme, database, user, base_path...) que los
  consumidores referencian en sus plantillas de env. Así `jdbc:{{scheme}}://{{host}}:{{port}}/{{database}}`
  toma `scheme` y `database` del proveedor, no solo host/port.
- `requires.env`: mapping `VAR → plantilla`. Las variables disponibles son los campos del endpoint
  resuelto (`host`, `port`, `url`) **más** los `fields` declarados por el proveedor.
- `requires.env_file`: para capacidades satisfechas por un one-shot que produce un fichero de env
  (p. ej. `contracts-handoff` → `contracts.env`). El núcleo distribuye ese fichero a los
  consumidores, generalizando `_distribute_contract_runtime`.
- `provides.kind = "artifact-env-file"` con `output`: declara que un one-shot produce un fichero de
  env consumible por otros.
- `p2p_family`: `{name, port, port_source}`. El núcleo deriva la matriz de peerings desde la
  política de routing (como hoy) para esa familia; sustituye a las tres familias cableadas.
- `hooks`: referencia a funciones del catálogo con contrato versionado (sección 13.8).

### 13.3 `compose.yml` (fragmento)

Un fragmento Compose de **un servicio**. Placeholders globales que el renderer rellena:
`{{catalog.images.<id>}}`, `{{data}}` (data_root + deployment_id), `{{secrets_root}}`,
`{{workspace_root}}`, `{{deployment_id}}`, `{{machine.id}}`.

```yaml
services:
  minter-postgres:
    image: "{{catalog.images.postgres}}"
    volumes:
      - "{{data}}/minter-postgres:/var/lib/postgresql/data"
    restart: unless-stopped
```

El env **no** va en el fragmento: se inyecta desde `requires.env` (plantillas) y `requires.env_file`
(one-shots). Los puertos publicados se calculan desde `provides` + exposure y se inyectan. Los
secretos y `config_files` se montan desde `secrets`/`config_files`. Así el fragmento solo describe
el contenedor; la conexión va en el manifiesto.

### 13.4 Catálogo

```
catalogs/dark-platform-baseline-v1.0/
  catalog.json
  components/<id>/...
```

`catalog.json`:

```json
{
  "schema_version": 1,
  "id": "dark-platform-baseline-v1.0",
  "version": "1.0",
  "images": {"besu": "...", "postgres": "...", "mysql": "...", "redis": "...",
             "dashboard": "...", "kubo": "...", "ipfs_cluster": "...", "edge_proxy": "..."},
  "defaults": {"ssh": {...}, "paths": {...}, "docker": {"subnet_pool": "...", "subnet_prefix": 24}},
  "infrastructure": {
    "besu":  {"network": "vpn", "p2p_port_start": 30303, "rpc_port": 8545},
    "ipfs":  {"network": "vpn", "api_port": 5001, "swarm_port": 4001},
    "cluster": {"network": "vpn", "api_port": 9094, "p2p_port": 9096}
  },
  "interfaces": {                           // registro de capacidades (sección 13.5)
    "jdbc-database": {"version": 1, "compatibility": "version>=min"},
    "jsonrpc":        {"version": 1, "compatibility": "version>=min"},
    "store-http":     {"version": 1, "compatibility": "version>=min"},
    "contracts-handoff": {"version": 1, "compatibility": "version>=min"},
    "http-api":       {"version": 1, "compatibility": "version>=min"},
    "p2p":            {"version": 1, "compatibility": "version>=min"}
  },
  "recipes": {
    "minter-stack": {
      "members": ["minter-api", "minter-worker-metadata", "minter-worker-replication",
                   "minter-worker-chain", "minter-postgres", "minter-migrate"],
      "constraints": {"colocate": "all"}
    }
  }
}
```

### 13.5 Registro de interfaces y matching

Una `provides` declara `{capability, version, fields...}`. Una `requires` declara
`{capability, min_version, env..., optional provider_filter}`. Matching: un proveedor satisface un
require si `capability` coincide, `version >= min_version`, y `provider_filter` (predicado simple
sobre los `fields` del proveedor, p. ej. `{"protocol": "postgresql"}`) pasa.

Esto es **versionado + predicado**, no matching libre por nombre. Empieza simple (versión entera +
filtro por campos) y puede crecer hacia compatibilidad real (rangos, esquemas) sin romper los
manifiestos existentes. Las `interfaces` del catálogo registran las capacidades válidas y su regla
de compatibilidad, de modo que un `requires` a una capacidad no declarada se rechaza en validación.

`constraints.colocate_with` referencia capacidades o ids de componente; el resolver agrupa
servicios que deben coexistir en una máquina (generaliza la regla actual "minter-api, minter-postgres
y los workers comparten máquina").

### 13.6 Motor de plantillas de env

Restringido, sin código ni condicionales. Sintaxis `{{field}}`. Campos disponibles en una plantilla
de `requires.env`:

- `host`, `port`, `url`: del endpoint resuelto por `network.endpoint_for` (Docker DNS mismo-host,
  IP LAN/VPN entre hosts, alias docker-lab — ya existe).
- cualquier `field` declarado por el proveedor (`scheme`, `database`, `user`, `base_path`...).

Ejemplos:

```
"DATABASE_URL": "postgresql://{{host}}:{{port}}/{{database}}"
"SPRING_DATASOURCE_URL": "jdbc:{{scheme}}://{{host}}:{{port}}/{{database}}"
"DARK_RPC_URL": "{{url}}"
"METADATA_STORE_API_URL": "{{url}}"
"VUFIND_SOLR_HOST": "{{host}}"
```

Sin `{{secrets.*}}` en plantillas: los secretos se montan como ficheros, no se interpolan en env
(coincide con el modelo actual de `secrets.distribute_secrets`).

### 13.7 Librería de sondas

```
tcp          {host, port, timeout_seconds}
http         {url|port+path, status, body_contains?, timeout_seconds}
jsonrpc      {url, method, params, expect_field?, expect_value?, timeout_seconds}
command      {container, command, exit_code}
compose-status {state: "running"}
```

Para predicados derivados (quórum, lag, convergencia de membresía) se usa un **hook** (13.8), no
una sonda. El runner de readiness: corre las sondas declaradas y, si hay hook de readiness, lo
corre también; el resultado es `ok = sondas_ok AND hook_ok`.

### 13.8 Hooks

Un hook es una función Python en el catálogo con contrato versionado. Se ejecuta en el controlador
(no en el contenedor). Contrato `api_version: 1`:

```python
def readiness(plan, service, runtime) -> {"ok": bool, "detail": str}
def verify(plan, service, runtime) -> {"ok": bool, "detail": str}
def env_extra(plan, service, runtime) -> dict[str, str]   # env adicional no derivable de endpoints
```

`runtime` es un handle acotado: consulta Docker Compose (`compose ps`), exec en contenedores,
consultas HTTP/JSON-RPC — exactamente lo que hoy hacen `readiness.py`/`verify.py` inline. El núcleo
no conoce "besu"; llama al hook del catálogo dARK.

Ejemplo (catálogo dARK, `components/besu-validator/hooks.py`):

```python
def readiness(plan, service, runtime):
    # runtime.jsonrpc(url, method, params) está disponible
    peers = runtime.jsonrpc(primary_rpc_url(plan), "net_peerCount", [])
    needed = validator_count(plan) + rpc_count(plan) - 1
    return {"ok": peers >= needed, "detail": f"peers={peers}, needed={needed}"}
```

Esto reemplaza la rama `validators` de `readiness.py` sin que el núcleo sepa qué es un validador.

### 13.9 Mergéo de fragmentos en el renderer

El renderer, por servicio colocado:

1. Carga `compose.yml` del componente y rellena placeholders globales.
2. Inyecta `env_file` y `environment`: env público (desde `requires.env` plantillado con
   `network.endpoint_for`), `env_file` de one-shots proveedores (`requires.env_file`), secretos
   runtime. Orden: público → one-shot → secretos (igual que hoy: Compose da prioridad al último).
3. Calcula `ports` desde `provides` (exposure, listeners, `publish_when_remote`, p2p_family) —
   generaliza `_ports`/`_p2p_ports`/`_ipfs_network_ports` a partir de los `provides`, no del tipo.
4. Monta volúmenes de secretos (`secrets`) y de `config_files`.
5. Etiquetas (`org.dark.deployment.id`, `org.dark.service.id`).
6. Agrupa en el proyecto Compose del host (`{deployment_id}-{machine_id}[-{group_id}]`).

Las ramas `if/elif` por tipo de `services.py` desaparecen: el comando de Besu, el entrypoint de
Kubo, los volúmenes concretos viven en el fragmento de cada componente.

---

## 14. Componentes dARK implementados en el formato

Estos son fieles al código actual (`services.py`/`render.py`); muestran cómo cada rama por tipo se
convierte en datos. Los `env` concretos se simplifican donde el código actual deriva muchos
settings; esos settings pasan a `catalog.json` defaults o `hooks.env_extra`.

### 14.1 `minter-postgres`

Reemplaza `services.py:127-128`.

`component.json`:

```json
{
  "schema_version": 1, "id": "minter-postgres", "kind": "service", "category": "data",
  "runtime": {"fragment": "compose.yml"},
  "provides": [{"capability": "jdbc-database", "version": 1, "port": 5432, "protocol": "tcp",
                "exposure": "private",
                "fields": {"scheme": "postgresql", "database": "minter"}}],
  "requires": [],
  "secrets": [],
  "probes": {"readiness": [{"type": "tcp", "port": 5432, "timeout_seconds": 30}],
             "health":    [{"type": "tcp", "port": 5432}]},
  "constraints": {"colocate_with": ["minter-api"]}
}
```

`compose.yml`:

```yaml
services:
  minter-postgres:
    image: "{{catalog.images.postgres}}"
    volumes:
      - "{{data}}/minter-postgres:/var/lib/postgresql/data"
    restart: unless-stopped
```

### 14.2 `minter-api`

Reemplaza `services.py:129-133` y la rama `minter-api` de `render.py:_env`.

`component.json`:

```json
{
  "schema_version": 1, "id": "minter-api", "kind": "service", "category": "applications",
  "runtime": {"fragment": "compose.yml"},
  "provides": [{"capability": "http-api", "version": 1, "port": 8001, "protocol": "tcp",
                "exposure": "private", "fields": {"base_path": "/"}}],
  "requires": [
    {"capability": "jdbc-database", "min_version": 1,
     "env": {"DATABASE_URL": "postgresql://{{host}}:{{port}}/{{database}}"}},
    {"capability": "jsonrpc", "min_version": 1,
     "env": {"DARK_RPC_URL": "{{url}}"}},
    {"capability": "store-http", "min_version": 1,
     "env": {"METADATA_STORE_API_URL": "{{url}}"}},
    {"capability": "contracts-handoff", "min_version": 1,
     "env_file": "contracts.env"}
  ],
  "secrets": [{"id": "minter-runtime-env", "mount": "/run/secrets/minter-runtime-env"}],
  "probes": {"readiness": [{"type": "http", "port": 8001, "path": "/health", "status": 200}],
             "health":    [{"type": "http", "port": 8001, "path": "/health", "status": 200}]},
  "constraints": {"colocate_with": ["minter-postgres", "minter-worker-metadata"]},
  "hooks": {"module": "hooks.py", "api_version": 1,
            "env_extra": "minter_settings"}    // settings de replicación/metadata/chain
}
```

`compose.yml`:

```yaml
services:
  minter-api:
    build: {"context": "{{workspace_root}}/components", "dockerfile": "dark-core-minter-api/Dockerfile"}
    command: []
    volumes:
      - "{{data}}/minter-metadata:/app/metadata_storage"
    restart: unless-stopped
```

Los settings de `render.py` (replication/metadata/chain) los aporta `hooks.env_extra("minter_settings")`,
que lee `plan.raw["settings"]["minter"]` — el catálogo dARK sí conoce su propio settings; el núcleo
no.

### 14.3 `besu-validator`

Reemplaza `services.py:120-126` y la rama Besu de `readiness.py`.

`component.json`:

```json
{
  "schema_version": 1, "id": "besu-validator", "kind": "service", "category": "validators",
  "runtime": {"fragment": "compose.yml"},
  "provides": [
    {"capability": "p2p", "version": 1, "port": 30303, "protocol": "tcp",
     "publish_when_remote": true}
  ],
  "requires": [],
  "secrets": [],
  "probes": {"readiness": [{"type": "compose-status", "state": "running"}],
             "health":    [{"type": "compose-status", "state": "running"}]},
  "p2p_family": {"name": "blockchain", "port": 30303, "port_source": "p2p_port_start+index"},
  "artifacts": {"requires": "chain-artifact"},
  "hooks": {"module": "hooks.py", "api_version": 1, "readiness": "peer_count"}
}
```

`compose.yml` (el comando y los volúmenes que hoy están en la rama `besu-validator` viven aquí):

```yaml
services:
  besu-validator:
    image: "{{catalog.images.besu}}"
    entrypoint: ["/usr/local/bin/dark-besu-entrypoint.sh"]
    command:
      - "--config-file=/config/besu-config.toml"
      - "--genesis-file=/config/genesis.json"
      - "--node-private-key-file=/config/nodekey"
      - "--static-nodes-file=/config/static-nodes.json"
      - "--rpc-http-enabled=false"
      - "--p2p-port=30303"
    volumes:
      - "{{data}}/{{service.id}}:/data"
      - "{{workspace_root}}/blockchain/scripts/besu-entrypoint.sh:/usr/local/bin/dark-besu-entrypoint.sh:ro"
      - "{{workspace_root}}/blockchain/config/besu-config.toml:/config/besu-config.toml:ro"
      - "{{secrets_root}}/{{blockchain.artifact.path}}/genesis.json:/config/genesis.json:ro"
      - "{{secrets_root}}/{{blockchain.artifact.path}}/nodes/{{node.id}}/nodekey:/config/nodekey:ro"
      - "{{secrets_root}}/{{blockchain.artifact.path}}/nodes/{{node.id}}/static-nodes.json:/config/static-nodes.json:ro"
    restart: unless-stopped
```

Nótese: el núcleo no sabe qué es un "nodekey"; solo monta el artefacto de cadena en las rutas que el
fragmento indica. `{{node.id}}` y `{{blockchain.artifact.path}}` son placeholders de deployment
resueltos por el renderer desde el plan (no del catálogo).

### 14.4 `ipfs-kubo`

Reemplaza `services.py:165` y las ramas IPFS de `readiness.py`/`verify.py`.

`component.json`:

```json
{
  "schema_version": 1, "id": "ipfs-kubo", "kind": "service", "category": "storage",
  "runtime": {"fragment": "compose.yml"},
  "provides": [
    {"capability": "ipfs-api", "version": 1, "port": 5001, "protocol": "tcp", "exposure": "private"},
    {"capability": "p2p", "version": 1, "port": 4001, "protocol": "tcp",
     "publish_when_remote": true}
  ],
  "requires": [],
  "secrets": [{"id": "ipfs-swarm-key", "mount": "/run/secrets/ipfs-swarm-key"}],
  "probes": {"readiness": [{"type": "http", "port": 5001, "path": "/api/v0/id", "status": 200}],
             "health":    [{"type": "http", "port": 5001, "path": "/api/v0/id", "status": 200}]},
  "p2p_family": {"name": "ipfs", "port": 4001, "port_source": "infrastructure.ipfs.swarm_port"},
  "hooks": {"module": "hooks.py", "api_version": 1, "readiness": "kubo_id"}
}
```

`compose.yml`:

```yaml
services:
  ipfs-kubo:
    image: "{{catalog.images.kubo}}"
    entrypoint: ["/usr/local/bin/ipfs-entrypoint.sh"]
    environment: {"IPFS_TELEMETRY": "off"}
    volumes:
      - "{{workspace_root}}/components/dark-ipfs/scripts/ipfs-entrypoint.sh:/usr/local/bin/ipfs-entrypoint.sh:ro"
      - "{{data}}/{{service.id}}:/data/ipfs"
    restart: unless-stopped
```

### 14.5 `edge-proxy`

Reemplaza `services.py:170-176` y `render._nginx_proxy_config`. La configuración nginx se genera
desde las `routes` del inventario (como hoy) y se escribe en `config/` del bundle; el fragmento la
monta.

`component.json`:

```json
{
  "schema_version": 1, "id": "edge-proxy", "kind": "service", "category": "applications",
  "runtime": {"fragment": "compose.yml"},
  "provides": [{"capability": "http", "version": 1, "port": 80, "protocol": "tcp",
                "exposure": "from_listener"}],
  "requires": [],                            // las conexiones a backends se derivan de configuration.routes
  "secrets": [],                             // tls se declara por sitio en configuration
  "probes": {"readiness": [{"type": "http", "port": 80, "path": "/", "status": "<500"}],
             "health":    [{"type": "http", "port": 80, "path": "/", "status": "<500"}]},
  "hooks": {"module": "hooks.py", "api_version": 1, "env_extra": "proxy_routes"}
}
```

`compose.yml`:

```yaml
services:
  edge-proxy:
    image: "{{catalog.images.edge_proxy}}"
    volumes:
      - "./config/nginx-{{service.id}}.conf:/etc/nginx/conf.d/default.conf:ro"
    restart: unless-stopped
```

`hooks.env_extra("proxy_routes")` genera el `nginx.conf` a partir de
`service.configuration.sites[].routes` (exactamente lo que hoy hace `_nginx_proxy_config`) y lo
escribe en `config/`; los secretos TLS (`certificate_secret`, `key_secret`) se montan cuando
`tls.mode == "direct"`. El núcleo solo ve "un componente con un hook que produce un fichero de
config"; no conoce nginx.

### 14.6 `contracts-deploy` (one-shot)

Reemplaza `services.py:177-178` y la distribución de `contracts.env`.

`component.json`:

```json
{
  "schema_version": 1, "id": "contracts-deploy", "kind": "one-shot", "category": "contracts",
  "runtime": {"fragment": "compose.yml"},
  "provides": [
    {"capability": "contracts-handoff", "version": 1, "kind": "artifact-env-file",
     "output": "contracts.env",
     "fields": {"DARK_AUTHORITY_ADDRESS": "...", "DARK_CONTRACT_ADDRESS": "..."}}
  ],
  "requires": [{"capability": "jsonrpc", "min_version": 1, "env": {"DARK_RPC_URL": "{{url}}"},
                "provider_filter": {"role": "rpc"}}],
  "secrets": [{"id": "contract-signer", "mount": "/run/secrets/contract-signer"}],
  "lifecycle": {"one_shot": {"timeout_seconds": 1800, "success": {"exit_code": 0}}},
  "probes": {},
  "hooks": {"module": "hooks.py", "api_version": 1, "env_extra": "chain_context"}
}
```

`compose.yml`:

```yaml
services:
  contracts-deploy:
    profiles: ["setup"]
    build: {"context": "{{workspace_root}}", "dockerfile": "deployment_v3/Dockerfile.contracts"}
    volumes:
      - "./artifacts/contracts:/contracts:ro"
      - "{{data}}/contracts:/runtime"
    restart: "no"
```

El núcleo ve: un one-shot que requiere `jsonrpc` y provee `contracts-handoff` como `artifact-env-file`
con `output: contracts.env`. Tras ejecutarse con éxito, distribuye `contracts.env` a todo componente
con `requires.env_file: "contracts.env"` (minter-api, admin-api, resolver-api...). Eso **generaliza**
`_distribute_contract_runtime` sin que el núcleo conozca los contratos.

### 14.7 `store-api`

Reemplaza `services.py:138-139`.

`component.json`:

```json
{
  "schema_version": 1, "id": "store-api", "kind": "service", "category": "data",
  "runtime": {"fragment": "compose.yml"},
  "provides": [{"capability": "store-http", "version": 1, "port": 8003, "protocol": "tcp",
                "exposure": "private", "fields": {"base_path": "/"}}],
  "requires": [],                            // las conexiones a clusters se derivan dinámicamente
  "secrets": [],
  "probes": {"readiness": [{"type": "http", "port": 8003, "path": "/health", "status": 200,
                            "query": {"refresh": "true"}}],
             "health":    [{"type": "http", "port": 8003, "path": "/health", "status": 200}]},
  "constraints": {},
  "hooks": {"module": "hooks.py", "api_version": 1, "env_extra": "storage_endpoints"}
}
```

`compose.yml`:

```yaml
services:
  store-api:
    build: {"context": "{{workspace_root}}/components", "dockerfile": "dark-store-api/Dockerfile"}
    volumes:
      - "./config/storage-endpoints.json:/config/storage-endpoints.json:ro"
    restart: unless-stopped
```

`hooks.env_extra("storage_endpoints")` genera `storage-endpoints.json` y las conexiones a los
clusters desde el grafo resuelto (hoy eso lo hace el resolver para `store-api`). El modo
`read_only` (los *readers*) es un parámetro del componente instanciado en el inventario, no un tipo
distinto.

---

## 15. Mapeo: qué código desaparece al portar estos componentes

| Componente (sección 14) | Reemplaza en el código actual |
| --- | --- |
| `minter-postgres` | rama `services.py:127-128`; `DEFAULT_PORTS["minter-postgres"]` |
| `minter-api` | rama `services.py:129-133`; rama `render.py:_env` minter-api; regla de co-localización `_validate_domain:743-746` |
| `besu-validator` | rama `services.py:120-126`; `_p2p_ports`; rama `readiness.py` validators; `phase "validators"` de `PHASES` |
| `ipfs-kubo` | rama `services.py:165`; `_ipfs_network_ports`; rama `readiness.py` storage; `phase "storage"` |
| `edge-proxy` | rama `services.py:170-176`; `render._nginx_proxy_config`; regla "un edge-proxy por máquina" `_validate_domain:722-727` |
| `contracts-deploy` | rama `services.py:177-178`; `_distribute_contract_runtime`; rama `render.py:_env` contracts-deploy; `phase "contracts"` |
| `store-api` | rama `services.py:138-139`; rama `render.py:_env` store-api; regla store-api/clusters `_validate_domain:680-695` |

Tras portar los ~20 componentes dARK a este formato, las listas cerradas (`SERVICE_TYPES`,
`CONNECTION_CONTRACTS`, `PHASES`, `DEFAULT_PORTS`) y todas las ramas `if/elif` por tipo de
`services.py`/`render.py`/`readiness.py`/`verify.py` quedan vacías y se eliminan. Lo que queda en el
núcleo es el mergeador de fragmentos, el motor de plantillas, el runner de sondas/hooks y la
maquinaria de topología — sin sustantivos dARK. Los hooks y los fragmentos con comandos/volúmenes
específicos viven en `catalogs/dark-platform-baseline-v1.0/components/`.

Ese es el punto donde el criterio de éxito (sección 7) se cumple: un stack no-dARK se despliega con
el mismo núcleo, y los módulos genéricos no contienen ningún sustantivo dARK.

---

## 16. Evaluación de la estrategia y límites de sus promesas

### 16.1 Qué se sostiene tras contrastarlo con el código

El diagnóstico central permanece: el catálogo actual compone tipos conocidos, mientras la
semántica de esos tipos vive en Python. La generalización necesita mover esa semántica a
componentes y contratos de extensión. La separación núcleo → catálogos → inventarios es una
base razonable para hacerlo sin mantener dos implementaciones completas del deployer.

Compose + manifiesto conserva una descripción reconocible del contenedor y permite que el
manifiesto se concentre en las relaciones que el orquestador debe entender. Sin embargo, el
coste no desaparece: el núcleo sigue necesitando reglas de composición, resolución, validación
y ejecución. Evitar un DSL de contenedores propio no equivale a evitar diseñar un lenguaje.

Las referencias anteriores a Helm expresan una preferencia por reducir la superficie de
plantillas, no una demostración técnica de que una opción sea siempre más barata ni una
conclusión verificable sobre lo que opina toda su comunidad.

### 16.2 Sustituir universalidad por un alcance verificable

«Cualquier cosa con un Compose encaja» debe entenderse como aspiración. El compromiso inicial
más preciso sería:

> El núcleo despliega componentes que cumplen un perfil documentado de Compose y el contrato
> del manifiesto. Las necesidades fuera de ese perfil se rechazan con un diagnóstico explícito
> o se incorporan mediante una extensión deliberada del contrato.

Separar un stack en fragmentos por servicio no resuelve automáticamente su distribución entre
hosts. Persisten restricciones sobre redes, almacenamiento, referencias entre servicios,
archivos locales, contextos de build y alcance de los nombres. Esas restricciones deben hacerse
visibles antes de ejecutar, no descubrirse como errores remotos a mitad de un despliegue.

La generalidad se demuestra cuando componentes independientes pueden describirse sin modificar
el núcleo. «Cero sustantivos dARK» ayuda como comprobación de dependencias, pero no basta:
renombrar una regla de Besu como `p2p_family` no demuestra que sirva para otros protocolos.
Tampoco una etiqueta histórica `org.dark.*` significa por sí sola acoplamiento semántico; puede
mantenerse temporalmente como compatibilidad mientras se migra a etiquetas neutrales.

### 16.3 Tres niveles de control, no dos

La sección 3.A distingue instalación y reconciliación continua. Conviene separar tres alcances:

1. **Instalación y recuperación:** ejecutar un plan conocido y retomar pasos interrumpidos.
2. **Actualización explícita:** comparar una nueva declaración con el último estado aplicado y
   ejecutar cambios bajo petición del operador.
3. **Reconciliación continua:** observar el estado real periódicamente y corregir diferencias.

El segundo no exige necesariamente implementar el tercero. Añadir un servicio podría llegar a
soportarse mediante una actualización explícita; eso requiere identidad estable, diferencias
entre planes, política para cambios destructivos y tratamiento del estado persistente. No se
propone incluirlo en la primera entrega ni se afirma que `--resume` ya lo resuelva.

Mantener el lenguaje independiente del control exige evitar que los componentes asuman una
instalación vacía, un único orden global o la ejecución exactamente una vez de sus hooks. La
independencia no se obtiene solo declarando que habrá un reconciliador futuro.

---

## 17. Reutilización real: conservar mecanismos, extraer políticas

### 17.1 El alcance de la extracción es mayor que las listas cerradas

Las barreras identificadas en 2.3 son correctas, pero no agotan el trabajo. La revisión del código
actual añade estas superficies:

| Superficie | Evidencia actual | Consecuencia para la generalización |
| --- | --- | --- |
| `executor.run_network_preflight` | Excepciones para contratos y familias Besu/IPFS/Cluster | Separar transporte de comprobaciones de dominio |
| `runner._apply_service` | Preparación y ejecución por tipo, migraciones y contratos | Modelar lifecycle y mover adaptadores al catálogo |
| `runner._service_fingerprint` | Lista de entrypoints por tipo y caso especial de contratos | Registrar entradas de cada componente y sus digests |
| `runner` y datos persistentes | Clasificación y protección de datos por tipos conocidos | Declarar recursos persistentes y políticas de reutilización |
| `network.internal_port` / `endpoint_for` | Selección de un puerto principal por servicio | Resolver endpoints por puerto/capacidad además de instancia |
| `network.derive_endpoints` | Clave consumidor/proveedor y excepción `contracts-deploy` | Distinguir conexiones de red y dependencias de artefactos |
| `planner` | Categorías y barreras de readiness asociadas a fases dARK | Expresar condiciones de dependencia, además de orden |

Las funciones y nombres indicados describen el código revisado, no una API pública futura.

### 17.2 Qué significa «reutilizar» en este proyecto

Hay tres casos distintos:

- **Mecanismo conservable:** ejecutar comandos SSH, transferir archivos, calcular hashes,
  mantener locks. Sus interfaces pueden necesitar adaptación sin reescribir el mecanismo.
- **Mecanismo con política embebida:** resolución de endpoints, fingerprints, staging,
  readiness y planificación. Se conserva la lógica útil después de extraer supuestos dARK.
- **Política de catálogo:** inicialización de cadena, membresía, settings, migraciones y
  reglas de colocación de componentes concretos.

Por tanto, «reutilizable tal cual» en las secciones anteriores debe leerse con esta distinción.
El coste se estima por las decisiones y contratos que hay que separar, no por el porcentaje de
líneas que contienen un nombre de dominio.

### 17.3 Mantener una dirección de dependencia comprobable

El núcleo puede conocer estructuras como `ComponentDefinition`, `ResolvedBinding`,
`ExecutionStep` o `ArtifactReference`. No debe importar módulos del catálogo dARK ni interpretar
campos como `blockchain.artifact.path` por su significado particular.

El catálogo puede traducir sus parámetros a entradas genéricas: referencias de artefactos,
archivos de configuración y valores de plantilla. El adaptador de inventario antiguo conserva
su vocabulario y lo convierte a esas estructuras. Así la compatibilidad con inventarios dARK
no obliga a mantener su semántica dentro del núcleo.

---

## 18. Capacidades, conexiones e identidad de endpoints

### 18.1 Validar compatibilidad y seleccionar proveedor son tareas diferentes

Una capacidad describe lo que una instancia ofrece. Un requisito describe lo que el consumidor
necesita. El binding identifica **qué instancia y qué oferta concreta** satisfacen ese requisito.

La selección explícita en el inventario debe estar disponible desde el principio. Si se admite
selección automática, solo debería resolver un requisito cuando existe un único candidato
válido dentro de su alcance; cero candidatos es un error y varios candidatos son una ambigüedad.
No debe elegirse silenciosamente el primero del catálogo ni el más cercano sin una política
expresada por el operador.

Ejemplo candidato, todavía no normativo:

```yaml
# En el componente consumidor
requires:
  database:
    capability: database.postgresql
    interface_major: 1
    cardinality: one
    condition: healthy
    env:
      SPRING_DATASOURCE_URL: "jdbc:postgresql://{{host}}:{{port}}/{{database}}"

# En el componente proveedor
provides:
  sql:
    capability: database.postgresql
    interface_major: 1
    endpoint: sql
    fields:
      database: app

# En el inventario: nombres de instancias, no solo tipos
bindings:
  api-a.database:
    provider: database-a.sql
    network: backend
```

Este ejemplo ilustra la separación entre definición e instancia. La forma exacta de las claves
y su relación con `connections` existente debe decidirse al diseñar el adaptador.

### 18.2 Versión de interfaz y versión del producto

La versión de PostgreSQL, la versión del componente y la versión de la interfaz son conceptos
separados. Un proveedor puede actualizar su imagen sin cambiar la interfaz que expone.

`version >= min_version` solo es suficiente si existe una garantía explícita de compatibilidad
ascendente para todas las versiones admitidas. Una alternativa inicial conservadora es exigir
igualdad de versión mayor de interfaz y validar campos obligatorios. Los rangos más expresivos
pueden posponerse; la semántica de ruptura de compatibilidad no.

El registro de interfaces debería definir el esquema de `fields`, sus tipos y sus restricciones.
Si una plantilla necesita `database`, la ausencia del campo debe fallar durante la resolución.
Un `provider_filter` no reemplaza ese esquema. Además, los ejemplos previos mezclan
`protocol: tcp` con un filtro `protocol: postgresql`; conviene separar transporte, protocolo de
aplicación y esquema de URL para evitar esa ambigüedad.

### 18.3 Cardinalidad, rol y ámbito

Hay que distinguir requisitos de uno, cero o uno, y varios proveedores. No hace falta soportar
los tres desde la primera entrega, pero sí rechazar los no soportados. También hace falta un
nombre local de requisito: una aplicación puede necesitar dos bases de datos con la misma
interfaz, una para lectura y otra para escritura.

Las constraints de co-localización deben aplicarse a instancias o bindings resueltos. Referir
solo `minter-postgres` como tipo no basta si existen dos stacks minter. Una capacidad compatible
no implica que todos sus proveedores deban compartir máquina.

### 18.4 Endpoints como entidades propias

Una instancia puede exponer SQL, HTTP administrativo y métricas. Un modelo candidato separa:

- Instancia proveedora y nombre de oferta.
- Nombre de endpoint y puerto interno.
- Transporte (`tcp`/`udp`) y protocolo de aplicación.
- Dirección resuelta según consumidor, red y listener.
- Metadatos de aplicación y autenticación referenciada.

La clave de resolución debe incluir el requisito u oferta seleccionada, no únicamente el par
consumidor/proveedor. Dos bindings entre las mismas instancias pueden usar distintos puertos.
Los artefactos de un one-shot no necesitan un endpoint de red y no deben representarse con un
puerto ficticio ni excepciones por nombre de componente.

---

## 19. Contrato de composición de Compose y plantillas

### 19.1 Definir un perfil soportado

El perfil inicial puede limitar cada fragmento a un servicio e identificar qué campos acepta,
cuáles transforma y cuáles reserva al orquestador. Debe resolver al menos:

| Aspecto | Regla candidata |
| --- | --- |
| Identidad del servicio | El renderer usa el id de instancia; el nombre del fragmento es local |
| `container_name` | Rechazar inicialmente si impide varias instancias del componente |
| Redes y publicación de puertos | Gestionadas por el núcleo a partir del plan |
| Dependencias entre componentes | Declaradas en bindings; no inferidas de nombres Compose |
| Volúmenes | Distinguir persistentes, temporales y archivos de configuración |
| Rutas y build contexts | Referencias resueltas y empaquetadas antes del staging remoto |
| Valores estáticos de entorno | Admitidos con precedencia explícita respecto a valores generados |
| Funciones Compose fuera del perfil | Error temprano con el campo y una explicación |

Esto precisa la afirmación de 13.3 de que «el env no va en el fragmento»: los valores estáticos,
como `IPFS_TELEMETRY`, sí pueden vivir allí; el cableado de dependencias pertenece al manifiesto.
La coexistencia debe definirse, no dejarse al orden accidental de diccionarios.

### 19.2 Precedencia y conflictos

El renderer necesita una tabla única de propiedad de campos. Para cada campo debe especificar
si se conserva, se sustituye, se combina o se rechaza cuando aparece en dos fuentes. Una colisión
de montajes sobre el mismo destino o dos asignaciones incompatibles del mismo puerto no debería
resolverse silenciosamente.

Para env, distinguir valores estáticos, parámetros de instancia, bindings, archivos producidos
por jobs y secretos. La intención «público → one-shot → secretos» debe traducirse y verificarse
contra la configuración efectiva, considerando por separado `environment` y `env_file`; no basta
con concatenar indiscriminadamente ambas formas de configuración.

### 19.3 Plantillas sobre estructuras y con errores tempranos

La interpolación restringida debe especificar variables disponibles, tipos, escaping, tratamiento
de valores ausentes y representación de listas o números. Conviene interpolar valores dentro de
una estructura YAML ya parseada, conservando tipos y evitando que un valor se convierta en una
clave, un comentario o una nueva sección del documento.

No debe haber evaluación de expresiones ni ejecución de comandos en la plantilla. Los campos
que requieren construir URLs necesitan reglas explícitas para sus valores, incluidas direcciones
IPv6 y caracteres reservados. Puede empezarse con formatos restringidos y validación; no es
necesario introducir un lenguaje general de transformaciones.

Los placeholders dARK de 14.3 deben llegar como datos preparados por el catálogo mediante un
contexto de parámetros o referencias de artefactos. Su presencia no debe obligar al renderer a
conocer la estructura interna de la cadena.

---

## 20. Contratos de extensión: hooks, configuración y confianza

### 20.1 Separar etapas y efectos

`env_extra` es insuficiente como contrato universal. La siguiente división conserva la idea de
hooks y hace explícitas sus responsabilidades; los nombres son candidatos:

| Extensión | Entrada | Salida | Efectos permitidos |
| --- | --- | --- | --- |
| `validate` | Parámetros y bindings resueltos | Diagnósticos | Ninguno |
| `render_config` | Contexto de render e inputs declarados | Archivos y valores públicos | Ninguno fuera de la salida declarada |
| `prepare` | Contexto de ejecución | Resultado y referencias de artefactos | Operaciones declaradas y registradas |
| `readiness` | Servicio y bindings, runtime | Resultado estructurado | Consultas de comprobación |
| `verify` | Ámbito del componente o grupo, runtime | Evidencia y resultado | Comprobaciones según contrato |

La generación de `nginx.conf` o `storage-endpoints.json` pertenece a `render_config`, no a una
función que promete devolver únicamente `dict[str, str]`. El núcleo escribe los archivos
retornados dentro del bundle, comprueba sus rutas y calcula sus hashes. Para acciones que
modifican el sistema, el contrato debe reconocer esos efectos en lugar de llamarlos sondas.

### 20.2 Contexto estable y acotado

Pasar `plan.raw` completo a todos los hooks expone detalles internos y convierte cada cambio del
plan en un posible cambio de API. Es preferible un contexto versionado con parámetros del
componente, identidad de instancia, bindings, referencias de artefactos y metadatos del ámbito
permitido. Un hook de quórum puede requerir una vista de grupo; esa necesidad debe declararse.

Los adaptadores dARK pueden traducir temporalmente el inventario antiguo a ese contexto. Esta
capa de compatibilidad permite migrar sin exigir que todos los hooks conozcan el formato legado.

### 20.3 Política de ejecución

Un handle `runtime` limitado es una interfaz, no un sandbox para Python importado en el proceso.
El plugin conserva las capacidades del proceso que lo ejecuta. Para la primera versión resulta
razonable aceptar solo catálogos locales de confianza y documentarlo; no prometer ejecución
segura de catálogos arbitrarios descargados.

Cada invocación necesita identidad, timeout, resultado estructurado y clasificación de errores:
fallo transitorio, configuración inválida o fallo del propio plugin. Si se requiere poder detener
un hook bloqueado, hay que elegir una frontera de ejecución que lo permita; un parámetro timeout
por sí solo no garantiza interrumpir una función Python.

El versionado de API debe incluir el contexto y el formato de resultados, además del nombre de
la función. La idempotencia de hooks con efectos debe declararse y probarse donde corresponda.

---

## 21. Lifecycle, disponibilidad y artefactos

### 21.1 Orden topológico no equivale a dependencia satisfecha

El DAG es reutilizable, pero «proveedor arrancado» no significa «proveedor disponible». Un
consumidor puede depender de que otro servicio haya sido creado, esté saludable o haya terminado
con éxito. El plan debería representar esas condiciones y sus pasos de comprobación.

Las categorías pueden conservarse como agrupación visual o prioridad entre pasos igualmente
habilitados. No deben sustituir dependencias explícitas ni imponer para todos los catálogos la
secuencia validators → rpc → contracts → storage → data → applications.

También conviene separar dependencias de arranque y relaciones operativas. Dos miembros de un
cluster pueden necesitar comunicarse mutuamente sin exigir que cada uno esté saludable antes
de iniciar al otro. Un ciclo operativo no debe convertirse automáticamente en un ciclo imposible
del plan; puede requerir iniciar el grupo y comprobar luego su convergencia.

### 21.2 Precisar desde dónde se ejecutan las sondas

Una sonda a `host:port` necesita perspectiva: controlador, host remoto, contenedor consumidor o
contenedor proveedor. Un puerto accesible por Docker DNS puede ser inaccesible desde el
controlador. Esa perspectiva debe formar parte del contrato y aprovechar el executor existente.

La librería de sondas debe concretar métodos HTTP, headers, cuerpos cuando hagan falta,
autenticación mediante referencias, plazos, intervalos y condiciones de éxito. Los ejemplos
anteriores incluyen campos como `query` y estados `"<500"` que requieren gramática y validación.
Readiness de instalación y vigilancia periódica de health son usos diferentes: declarar una
sonda de health no implica que el instalador ya la esté ejecutando de forma continua.

### 21.3 Jobs y entrega de resultados

Un one-shot necesita más que timeout y código de salida. Hay que definir:

- Cuándo se considera completado: ejecución exitosa y outputs esperados presentes y válidos.
- Qué permite reintentarlo y qué evidencia se conserva si hubo efectos parciales.
- Cuándo puede reutilizarse su resultado y qué cambio de inputs lo invalida.
- Cómo se entrega el output a consumidores en otros hosts y se confirma su integridad.
- Qué ocurre si el job terminó pero falló la distribución posterior.

Para `contracts-handoff`, el output debería tener identidad, productor, tipo, esquema o formato,
digest y política de sensibilidad. El consumidor referencia esa identidad; `contracts.env` es un
nombre físico de archivo, insuficiente para distinguir dos productores o despliegues.

La recuperación debe permitir reintentar la distribución sin volver a ejecutar innecesariamente
un job con efectos externos. No se puede garantizar ejecución exactamente una vez simplemente
registrando un estado local después de que el comando remoto haya terminado.

### 21.4 Secretos: preservar las formas de consumo existentes

La distribución de secretos como archivos en el host y su consumo por la aplicación son dos
capas diferentes. Actualmente `services.py` añade archivos secretos a `env_file` para varios
servicios, incluido `minter-postgres`; otros secretos se montan dentro del contenedor.

Por tanto, la frase de 13.6 sobre secretos montados como archivos no describe por completo el
comportamiento actual. Un contrato candidato distingue `file-mount` y `env-file`, mantiene los
valores fuera del bundle público y declara consumidores y destino. No debe convertir una
aplicación a lectura de archivos si esa aplicación no lo soporta.

Antes de considerar equivalentes los ejemplos de la sección 14, hay que restituir el consumo de
`minter-runtime-env` por PostgreSQL y verificar las demás credenciales, valores de entorno,
montajes y condiciones de arranque. La simplificación de ejemplos no puede convertirse en una
omisión de comportamiento durante la migración.

---

## 22. Reproducibilidad y estrategia de pruebas

### 22.1 Los snapshots son una capa de evidencia

Los snapshots de los 13 inventarios siguen siendo un buen primer paso. Deben fijar inputs y
normalizar únicamente valores que no formen parte del comportamiento: por ejemplo, rutas de
un directorio temporal. No se deben normalizar diferencias de puertos, conexiones, secretos
referenciados o comandos que podrían ocultar regresiones.

Preservar snapshots no demuestra equivalencia de ejecución. La sección 2.7 debe matizarse: los
tests de comportamiento dARK no son un obstáculo inherente; son parte del contrato de
compatibilidad. Los tests ligados a detalles internos pueden necesitar adaptación, pero sus
aserciones funcionales no deben eliminarse solo para facilitar el refactor.

### 22.2 Evidencia por capa

| Capa | Qué demuestra | Casos prioritarios |
| --- | --- | --- |
| Caracterización | Compatibilidad de salidas | Inventario → resolución → plan → bundle |
| Contratos | Errores tempranos y semántica | Ambigüedad, incompatibilidad, campos ausentes, múltiples endpoints |
| Ejecución | Aplicación correcta del plan | Servicio disponible, job fallido, timeout, distribución interrumpida |
| Integración | Funcionamiento de componentes reales | Aplicación no-dARK y dependencias entre hosts |
| Regresión dARK | Conservación del producto existente | Secretos, cadena, migraciones, storage, proxy y recuperación |

El corpus debe incluir fallos esperados, no solo un despliegue exitoso. Una generalización útil
explica por qué no puede desplegar una topología y evita efectos antes de detectar errores que
podían validarse estáticamente.

### 22.3 Identidad de las entradas y resume

Los fingerprints deben incorporar las entradas que afectan al componente: manifiesto, fragmento,
configuración, hooks, recursos empaquetados, bindings y referencias de artefactos. Las imágenes y
fuentes de build requieren una política de identificación reproducible; una etiqueta mutable no
es por sí sola una identidad inmutable.

La versión del catálogo no sustituye el digest de su contenido. Un cambio de hook o de un archivo
montado puede alterar el comportamiento aunque el nombre y la versión no cambien. El catálogo
resuelto debería quedar registrado junto al bundle y al estado, con el material sensible tratado
por la política de secretos.

La reutilización por fingerprint es una condición de recuperación, no una prueba de que el
estado real siga siendo correcto. Las comprobaciones necesarias antes de omitir un paso deben
quedar definidas por lifecycle y tipo de recurso.

---

## 23. Secuencia revisada: demostrar antes de migrar todo el catálogo

Esta secuencia conserva los objetivos de las secciones iniciales, pero reduce el riesgo de
congelar un esquema basado únicamente en dARK.

### Fase 1. Caracterización y mapa de responsabilidades

Capturar los snapshots existentes, conservar las pruebas funcionales y completar el inventario
de políticas específicas, incluidos runner, preflight, fingerprints y persistencia. Definir qué
compatibilidad se promete para CLI, inventarios, etiquetas y archivos generados.

**Salida:** baseline verificable y mapa de extracción. No se modifica la funcionalidad.

### Fase 2. Contrato mínimo y corte vertical

Diseñar solo lo necesario para un caso completo: aplicación, proveedor, binding explícito,
endpoint, configuración, secreto, sonda y job. Implementarlo por la misma ruta CLI → resolver →
plan → bundle → apply → verify que se pretende conservar.

El caso debería incluir dos proveedores compatibles para demostrar selección explícita, una
conexión entre dos hosts y un output de job consumido después. Puede realizarse con una
aplicación Spring Boot, dos instancias PostgreSQL y un job de inicialización. No exige incorporar
todos los tipos de sonda ni resolver todos los patrones de cluster.

**Salida:** el núcleo no necesita conocer esos nombres de componentes; las ambigüedades y
entradas inválidas fallan antes de aplicar. La recuperación de un fallo de distribución se prueba.

### Fase 3. Extracción incremental con compatibilidad

Migrar mecanismos al núcleo siguiendo lo aprendido. Un adaptador mantiene los inventarios
actuales y carga temporalmente implementaciones dARK mientras se trasladan al catálogo. Esto
no crea dos productos: debe existir una sola ruta de ejecución y cada rama antigua se retira
cuando su sustitución queda probada.

Empezar por componentes simples permite probar el renderer; incluir después un job y un
componente rico impide retrasar indefinidamente los problemas del contrato de hooks.

**Salida:** cada componente migrado conserva su comportamiento y reduce la política específica
dentro del núcleo. No es necesario esperar a portar los veinte para obtener evidencia.

### Fase 4. Corpus heterogéneo y componentes ricos

Completar Solr + Spring Boot + VuFind con la configuración que sus aplicaciones reales necesitan.
No asumir que declarar variables de entorno basta si la imagen o el entrypoint no las consume.
Verificar archivos, inicialización y conectividad de extremo a extremo.

Portar las piezas ricas dARK y comprobar que quórum, membresía y artefactos se expresan mediante
contratos estables. Si necesitan acceso general al plan o nuevas excepciones en el núcleo,
revisar el contrato antes de considerar exitosa la extracción.

**Salida:** dos dominios distintos operan con el mismo núcleo y perfiles documentados.

### Fase 5. Consolidación del formato y herramientas

Derivar del catálogo las opciones del editor y los esquemas correspondientes a componentes;
el esquema estructural del inventario sigue siendo responsabilidad del núcleo. Documentar el
perfil Compose, APIs, errores, compatibilidad y límites de actualización.

**Salida:** añadir un componente soportado requiere modificar su catálogo, no el núcleo,
los esquemas manuales y el editor por separado.

### Criterios de aceptación ampliados

Además de los tres criterios de la sección 7:

1. Dos instancias del mismo componente no colisionan en nombres, datos ni artefactos.
2. Dos proveedores compatibles requieren una selección no ambigua.
3. Un servicio con varias ofertas de red resuelve cada endpoint correctamente.
4. Los consumidores esperan la condición declarada, no solo la creación del proveedor.
5. Los secretos conservan su modo de consumo y no aparecen en salidas públicas.
6. Jobs y entrega de outputs se recuperan según una política documentada.
7. Cambios en hooks, configuración y recursos relevantes invalidan la reutilización apropiada.
8. Las funciones fuera del perfil soportado se rechazan antes de realizar efectos remotos.
9. dARK conserva sus pruebas funcionales además de los snapshots.

---

## 24. Decisiones pendientes y límites de la primera versión

La ampliación no propone resolver un orquestador universal antes de obtener valor. Propone
hacer explícitos los contratos que el alcance ya elegido necesita.

| Decisión | Propuesta inicial para validar | Qué puede posponerse |
| --- | --- | --- |
| Selección de proveedores | Binding explícito; autoselección solo con candidato único | Optimizador de placement y selección por preferencias |
| Compatibilidad | Mayor de interfaz compatible y esquema de campos | Rangos complejos y negociación dinámica |
| Catálogos | Un catálogo resuelto por despliegue, fijado por digest | Composición base + extensiones y resolución de conflictos |
| Compose | Perfil acotado, con errores para campos no soportados | Compatibilidad con cualquier stack Compose existente |
| Hooks | Catálogos de confianza y API versionada por etapa | Sandbox para plugins de terceros |
| Control | Instalación y recuperación explícitas | Actualizaciones generales y reconciliador continuo |
| Persistencia | Recursos declarados y política explícita de conservación | Migración automática de datos entre hosts |
| P2P | Adaptadores de catálogo y metadatos genéricos comprobados | Abstracción universal para protocolos de replicación |
| Artefactos | Identidad, integridad, sensibilidad y entrega registradas | Registro remoto de catálogos y artefactos |

Las cuestiones de API, perfil Compose, condiciones de dependencia y recuperación deben quedar
resueltas para el corte vertical. La composición de catálogos o un reconciliador pueden permanecer
fuera de alcance sin impedirlo.

La decisión práctica que sostiene todo el documento es convertir dARK en un consumidor real de
un núcleo reutilizable. Su prueba más exigente será que las reglas de dARK permanezcan completas
en el catálogo y que otro dominio pueda usar el mismo núcleo sin heredar esas reglas. La
extracción se considera terminada por esa evidencia funcional, no únicamente porque desaparezcan
listas cerradas o nombres de los módulos Python.
