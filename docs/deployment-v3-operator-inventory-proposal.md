# Propuesta: un inventario práctico para operar dARK

Fecha: 2026-09-12. Estado: diseño implementado parcialmente como capa compacta
compatible con el contrato v3. Este documento conserva la arquitectura,
decisiones y fases de evolución para completar el trabajo futuro.

Este documento propone una entrada compacta para describir instalaciones de
dARK y un compilador que la transforme en el inventario completo v3. La capa
compacta, el resolver, el catálogo inicial y los comandos de inspección ya están
implementados. Los ejemplos de `examples/operator-inventory/` son ejecutables;
las fases avanzadas pendientes siguen indicadas como evolución futura.

## 1. Objetivo y alcance

El operador debería poder describir una instalación indicando su identidad,
máquinas, distribución de funciones, redes, almacenamiento, acceso y fuentes de
secretos. No debería necesitar reproducir las dependencias internas de cada
aplicación ni mantener listas paralelas de los mismos nodos.

La propuesta conserva el inventario v3 como contrato de ejecución. Añade una
capa de autoría que elimina repetición y expresa decisiones del operador. La
expansión produce un documento completo, inspeccionable y validado antes de
construir el plan.

La primera implementación conserva las capacidades y restricciones actuales:
cuatro validadores y un RPC, Minter y sus workers en una máquina, y almacenamiento
mediante pares Kubo/Cluster. No introduce escalado arbitrario, bases externas,
descubrimiento de infraestructura, gestión de firewall ni migración de datos.

## 2. Diagnóstico del modelo actual

La revisión de los ejemplos y de `deployment_v3/inventory.py` muestra:

| Ejemplo actual | Líneas | Servicios | Conexiones | Máquinas |
| --- | ---: | ---: | ---: | ---: |
| local-simple | 531 | 22 | 43 | 1 |
| local-ha | 588 | 24 | 45 | 1 |
| production-five-host | 659 | 25 | 47 | 5 |
| lima-five-host | 737 | 25 | 47 | 5 |

Los cuatro ejemplos construyeron el plan durante la revisión. Esa comprobación
es offline: no acredita instalación, conectividad ni funcionamiento en Docker.
Los números corresponden al checkout revisado, no son límites del formato.

### 2.1 Repetición de relaciones internas

Minter API y los tres workers declaran las mismas cinco conexiones: PostgreSQL,
Store, RPC, contratos y migración. `CONNECTION_CONTRACTS` ya conoce sus tipos y
el validador impone restricciones de colocación. Una receta versionada puede
crear estas relaciones de forma explícita en la salida.

### 2.2 Una operación exige cambios en varias secciones

Añadir un peer requiere coordinar servicios Kubo y Cluster, su conexión, las
conexiones del Store, `storage.nodes`, consumidores de secretos y pertenencia a
grupos. Debe convertirse en una operación sobre un objeto lógico de storage.

### 2.3 Colocación duplicada

La ubicación aparece en servicios y grupos. Los grupos pueden combinar
`members`, IDs de servicio y `services`, con asignación implícita cuando una
máquina tiene un único grupo. Local-ha enumera los servicios; producción usa
inferencias. La entrada nueva debe tener una sola autoridad para cada ubicación.

### 2.4 Defaults copiados

`components` y `settings` son idénticos en los cuatro ejemplos. Los parámetros
de tuning deberían venir de un catálogo versionado y escribirse sólo cuando
hay una excepción. El catálogo debe fijarse para que actualizar el deployer no
cambie silenciosamente una instalación.

### 2.5 Configuración local confusa

Los ejemplos locales incluyen una clave SSH ficticia, direcciones ilustrativas
y rutas que el runner sustituye. La entrada nueva debe permitir una máquina
local mínima y explicar las rutas efectivas que usará.

### 2.6 Conectividad mezclada con dependencias

Cambiar un servicio de máquina puede requerir editar red, protocolo y exposición
en distintos puntos. Las dependencias internas pueden proceder de la receta;
las rutas entre máquinas deben proceder de una política de redes explícita.

## 3. Arquitectura propuesta

```text
Inventario editable + catálogo versionado
                  |
                  v
              resolve()
        normaliza y expande decisiones
                  |
                  v
     Inventario v3 completo + procedencia
                  |
                  v
   validación v3 + grupos + construcción del plan
                  |
                  v
       render / push / apply / verify
```

Tres responsabilidades:

1. **Catálogo de dARK:** define recetas, defaults y relaciones conocidas de una
   versión de la aplicación.
2. **Inventario del operador:** define decisiones particulares y excepciones.
3. **Inventario resuelto:** contiene todos los campos que exige v3 y es la
   entrada efectiva al plan y al runner.

El compilador debe ser puro: no abre conexiones SSH, no consulta Docker, no
clona repositorios, no genera secretos ni crea una cadena. La misma entrada,
catálogo y versión de resolución producen el mismo resultado.

## 4. Versionado y formato

Mantener JSON inicialmente. Cambiar a YAML no resuelve la repetición y añade
otra decisión de parser y persistencia. Un soporte YAML futuro puede producir
el mismo objeto de entrada, con las mismas reglas.

El documento compacto utiliza `format: "dark-operator-inventory"` y
`format_version: 1`. El documento expandido conserva `version: 3`. Son contratos
distintos: no llamar v4 al formato compacto ni cambiar el significado de v3.

El campo `catalog` contiene un identificador exacto, por ejemplo `dark-standard-1`.
Su contenido publicado es inmutable. Un cambio en defaults o recetas requiere
otro identificador. Se registra además su digest para detectar alteraciones.

No admitir inicialmente includes, herencia entre archivos, plantillas Jinja,
expansión de variables de entorno ni descargas de catálogos. Un archivo editable
y un catálogo instalado bastan para mantener explicable el resultado.

## 5. Contrato del inventario editable

| Sección propuesta | Responsabilidad | Qué se deriva |
| --- | --- | --- |
| `deployment` | ID y etiqueta | Nombres operativos según las reglas actuales |
| `catalog` | Versión exacta de recetas/defaults | Servicios, relaciones y parámetros base |
| `machines` | Transporte, direcciones y overrides de rutas/SSH | Objetos efectivos de máquina |
| `defaults` | Defaults SSH/rutas compartidos en remoto | Valores efectivos por máquina |
| `networks` | CIDR y tipo de redes reales | Validación y rutas privadas |
| `routing` | Red de cada clase de tráfico remoto | Conexiones y publicaciones necesarias |
| `placement` | Grupo lógico → máquina | Ubicación de todos sus servicios |
| `blockchain` | Identidad, nodos y artefacto | Servicios Besu y lista pública de nodos |
| `storage` | Peers, grupos y política de réplicas | Kubo, Cluster, conexiones y consumidores |
| `access` | Puertos accesibles al operador/usuarios | Exposiciones y edge proxy |
| `secrets` | Fuentes privadas del controlador | Destinos y consumidores según receta |
| `overrides` | Excepciones tipadas | Modificaciones acotadas sobre defaults |

### 5.1 Colocación: una fuente de verdad

`placement` asocia IDs de grupo a máquinas. Cada nodo blockchain y peer de
storage declara un grupo; la receta de aplicaciones pertenece a `apps`.
Explorer pertenece por defecto a `apps`, con override específico de grupo.

El compilador genera `services.*.machine` y los grupos v3 completos. La entrada
compacta no acepta una segunda ubicación por servicio. Los grupos deben tener
un tipo coherente: `apps`, `validators` o `storage`; mezclar clases en un mismo
grupo falla. Varios grupos pueden compartir una máquina.

Ejemplo: mover `storage-2` de `local` a `storage-host-b` cambia una sola
asignación. Requiere que esa máquina y sus redes estén definidas. Se recalculan
las rutas y se reportan los nuevos puertos necesarios. No mueve datos.

### 5.2 Identidades estables

Los IDs explícitos de validadores, RPC y peers son persistentes. Nunca se
renumeran por posición de una lista. La expansión conserva los nombres actuales:
peer `storage-a` → `ipfs-storage-a` y `cluster-storage-a`.

Eliminar o renombrar un peer debe aparecer como eliminación/creación en el diff,
no como una modificación inocua. El orden usado para puertos P2P e identidades
debe conservar el contrato existente. Una migración no puede ordenar mapas y
alterar inadvertidamente puertos o artefactos de cadena.

### 5.3 Redes

La política propuesta distingue `blockchain_p2p`, `storage_p2p`, `storage_api` y
`application_api`. Cada clase remota selecciona una red declarada. Se admite un
override de ruta por pareja consumidor/rol de conexión cuando haga falta.

Algoritmo:

1. Si consumidor y proveedor comparten máquina, usar Docker DNS y puerto interno.
2. Si están separados, determinar la clase de conexión desde la receta.
3. Resolver su red desde el override o `routing`; comprobar que ambos hosts
   pertenecen a ella.
4. Derivar el protocolo conocido y la exposición privada requerida.
5. Comprobar colisiones y compatibilidad con la política de puertos v3.

No elegir automáticamente la primera red común. No transformar una exposición
en pública para satisfacer una dependencia. Si un proveedor requiere dos binds
privados que el v3 actual no representa, fallar con explicación; ampliar ese
contrato sería otro trabajo.

### 5.4 Acceso externo

Separar el acceso solicitado por el operador de las publicaciones internas
derivadas. `access.mode` tiene tres valores iniciales:

- `local-direct`: puertos loopback de las aplicaciones habituales según catálogo.
- `gateway`: crear edge proxy para dashboard y explorer, como la receta actual.
- `none`: no añadir accesos externos; sí permitir los privados requeridos.

`gateway` exige `bind` explícito (`loopback`, `private` o `public`) y puerto;
`private` exige red. No prometer TLS o publicación de otras APIs si el proxy
actual no los implementa. Los puertos concretos deben aparecer en la resolución.

### 5.5 Secretos y artefacto

El operador indica fuentes, nunca valores. La receta deriva consumidores,
destinos relativos y modo 0600. Un `source_root` opcional resuelve fuentes por
nombre según una convención documentada; `sources` permite excepciones exactas.
La raíz es del controlador, no de la máquina remota.

La ausencia de fuente local puede conservar el flujo actual de provisión de
`install`. En remoto, la resolución puede emitir una advertencia de preparación
pendiente; `preflight`/`apply` deben exigir el material necesario antes de iniciar
servicios. Resolver nunca genera ni lee el contenido de esos archivos.

`blockchain.artifact.source` identifica el artefacto existente del controlador;
su destino procede de la receta. Declarar una cadena no autoriza regenerarla.
Los comandos actuales de inicialización siguen siendo acciones explícitas.

Las rutas relativas de fuentes se resuelven respecto al archivo editable,
nunca respecto al directorio temporal de validación ni al cwd del comando.
Registrar esta base en el contexto de resolución. Para reproducibilidad entre
controladores, distinguir el digest de intención del de la resolución con rutas
absolutas; este último puede cambiar al mover el archivo.

### 5.6 Excepciones

Primera versión: aceptar sólo `overrides.settings`, `overrides.components`,
`overrides.explorer.group` y overrides tipados de rutas/puertos que se implementen
con pruebas. No abrir un parche genérico a cualquier campo del JSON resuelto.

Fusionar mapas recursivamente sobre claves permitidas; reemplazar escalares;
reemplazar listas completas, nunca concatenarlas implícitamente. Rechazar claves
desconocidas y `null` salvo que el esquema lo admita expresamente. Validar el
resultado completo con las reglas v3.

Los componentes pueden seguir usando branches como hoy. Fijar el catálogo no
fija el commit de una branch mutable: conservar `source_evidence()` y la
comprobación de commits. Un lock de fuentes es distinto del lock de resolución.

## 6. Ejemplos propuestos

Los siguientes documentos muestran la estructura objetivo. Sus IDs de catálogo
son nombres propuestos y requieren implementación antes de poder usarse.

### 6.1 Local simple

```json
{
  "format": "dark-operator-inventory",
  "format_version": 1,
  "catalog": "dark-standard-1",
  "deployment": {"id": "dark-local-simple"},
  "machines": {"local": {"execution": "local"}},
  "placement": {"apps": "local", "validators": "local", "storage-1": "local"},
  "blockchain": {
    "chain_id": 2025,
    "rpc": {"id": "rpc01", "group": "apps"},
    "validators": {
      "validator01": {"group": "validators"},
      "validator02": {"group": "validators"},
      "validator03": {"group": "validators"},
      "validator04": {"group": "validators"}
    }
  },
  "storage": {
    "cluster_name": "dark-local",
    "peers": {"storage-a": {"group": "storage-1"}},
    "replication": {"publish_after_replicas": 1, "target_replicas": 1}
  },
  "access": {"mode": "local-direct"}
}
```

Esta variante usa tres grupos lógicos; el local-simple actual usa uno sintético.
Por tanto no es una conversión idéntica de sus proyectos Compose. La migración
de instalaciones existentes debe preservar el agrupamiento anterior mediante
un adaptador específico o mantenerse en v3. Las plantillas compactas pueden
adoptar el nuevo agrupamiento para instalaciones nuevas.

### 6.2 Local HA

Parte de la misma estructura, con estas decisiones completas de colocación y
storage (fragmento, no documento independiente):

```json
{
  "placement": {
    "apps": "local",
    "blockchain-a": "local",
    "blockchain-b": "local",
    "storage-1": "local",
    "storage-2": "local"
  },
  "blockchain": {
    "chain_id": 2025,
    "rpc": {"id": "rpc01", "group": "apps"},
    "validators": {
      "validator01": {"group": "blockchain-a"},
      "validator02": {"group": "blockchain-a"},
      "validator03": {"group": "blockchain-b"},
      "validator04": {"group": "blockchain-b"}
    }
  },
  "storage": {
    "cluster_name": "dark-local",
    "peers": {
      "storage-a": {"group": "storage-1"},
      "storage-b": {"group": "storage-2"}
    },
    "replication": {"publish_after_replicas": 1, "target_replicas": 2}
  }
}
```

Dos peers en un host simulan replicación, pero comparten dominio de fallo. La
vista resumida debe informar «2 peers, 1 máquina» sin presentarlo como tolerancia
a la pérdida de un host.

### 6.3 Producción y Lima

Producción mantiene los nodos y grupos de local-ha y cambia máquinas, asignación,
rutas, fuentes privadas y acceso. Ejemplo de decisiones de entorno:

```json
{
  "defaults": {
    "ssh": {"user": "dark", "port": 22, "private_key_file": "/secure/deploy.key"},
    "paths": {"workspace_root": "/srv/dark", "data_root": "/srv/dark/data", "secrets_root": "/srv/dark/secrets"}
  },
  "networks": {
    "lan": {"kind": "lan", "cidr": "192.0.2.0/24"},
    "vpn": {"kind": "vpn", "cidr": "198.51.100.0/24"}
  },
  "machines": {
    "apps": {"execution": "ssh", "management_address": "192.0.2.10", "addresses": {"lan": "192.0.2.10", "vpn": "198.51.100.10"}},
    "blockchain-a": {"execution": "ssh", "management_address": "192.0.2.11", "addresses": {"lan": "192.0.2.11", "vpn": "198.51.100.11"}},
    "blockchain-b": {"execution": "ssh", "management_address": "192.0.2.12", "addresses": {"lan": "192.0.2.12", "vpn": "198.51.100.12"}},
    "storage-1": {"execution": "ssh", "management_address": "192.0.2.13", "addresses": {"lan": "192.0.2.13", "vpn": "198.51.100.13"}},
    "storage-2": {"execution": "ssh", "management_address": "192.0.2.14", "addresses": {"lan": "192.0.2.14", "vpn": "198.51.100.14"}}
  },
  "placement": {"apps": "apps", "blockchain-a": "blockchain-a", "blockchain-b": "blockchain-b", "storage-1": "storage-1", "storage-2": "storage-2"},
  "routing": {"blockchain_p2p": "vpn", "storage_p2p": "vpn", "storage_api": "vpn", "application_api": "lan"},
  "access": {"mode": "gateway", "bind": "public", "port": 8080},
  "secrets": {"source_root": "/secure/dark-production"},
  "overrides": {"explorer": {"group": "blockchain-a"}}
}
```

Es un fragmento de entorno: debe integrarse con identidad, catálogo, blockchain,
storage y `blockchain.artifact.source`. Las direcciones son de documentación.
La migración exacta debe preservar también las publicaciones privadas del
ejemplo actual que no sean imprescindibles para conexiones del grafo; no
eliminarlas silenciosamente por considerarlas redundantes.

Lima utiliza el mismo modelo SSH. El generador descubre dirección de gestión,
puerto forwarded, usuario, clave, known_hosts y direcciones guest y escribe
`machines`/`defaults`. No se crea un tipo de servicio ni una receta específica
para Lima. El descubrimiento sigue fuera del compilador puro.

## 7. Catálogo y expansión

Añadir un catálogo local con:

- esquema, ID y versión de resolución compatible;
- defaults actuales de componentes, settings, puertos e imágenes;
- receta de aplicaciones y jobs de migración/contratos;
- receta de validadores/RPC y de cada par Kubo/Cluster;
- asignaciones de consumidores de secretos y sus destinos;
- clasificación de conexiones para routing.

Las reglas de validez siguen en el validador canónico. Evitar copiar
`CONNECTION_CONTRACTS` en dos fuentes divergentes: la receta puede referenciar
los contratos existentes, y una prueba debe verificar que cada servicio
expandido cumple todas sus conexiones obligatorias.

Orden de resolución:

1. Detectar formato y validar su esquema.
2. Cargar catálogo exacto y verificar su digest/versiones compatibles.
3. Normalizar IDs, rutas y defaults efectivos.
4. Construir índices de máquinas, grupos y nodos antes de resolver referencias.
5. Expandir servicios y asignar cada uno a un grupo y máquina.
6. Crear conexiones internas desde recetas.
7. Aplicar políticas de redes y acceso, detectando conflictos.
8. Derivar listas blockchain/storage y consumidores de secretos.
9. Aplicar excepciones permitidas en su etapa correspondiente; no modificar
   ubicación después de calcular endpoints.
10. Validar el documento completo v3 y sus grupos.
11. Construir plan y comprobar subredes/dependencias.
12. Emitir documento resuelto, procedencia y advertencias.

Las excepciones de settings/componentes se aplican antes de la validación final;
las de ubicación antes de rutas; las de puertos antes de comprobar colisiones.
La implementación debe expresar estas etapas, no depender de un merge final
que invalide cálculos anteriores.

## 8. Cambios concretos por módulo

| Archivo/módulo | Trabajo propuesto |
| --- | --- |
| `deployment_v3/operator_schema.json` (nuevo) | Esquema estricto del formato compacto, discriminación local/SSH y overrides tipados |
| `deployment_v3/catalogs/` (nuevo) | Catálogos inmutables y defaults versionados |
| `deployment_v3/inventory_resolver.py` (nuevo) | Detección, expansión pura, procedencia y resultado estructurado |
| `deployment_v3/inventory.py` | Extraer validación de un objeto en memoria; conservar `load_inventory(path)` como wrapper compatible |
| `deployment_v3/planner.py` | Separar lectura de construcción; añadir construcción desde documento v3 validado |
| `deployment_v3/model.py` | Contexto de entrada/resolución separado del `raw` v3; evitar metadatos extra rechazados por schema |
| `deployment_v3/cli.py` | Despachar ambos formatos y añadir resolve/explain/diff sin efectos operativos |
| `deployment_v3/render.py` | Guardar el v3 resuelto y evidencia de resolución junto al bundle |
| `deployment_v3/runner.py` | Comparar resolución y bundle para apply/resume; conservar rutas y protecciones existentes |
| `deployment_v3/sources.py` | Mantener evidencia de fuentes separada de catálogo/resolución |
| `deployment_v3/inventory_editor/document.py` | Validar en memoria con contexto de ruta real y guardar el formato original |
| `deployment_v3/inventory_editor/textual_app.py` | Formularios por decisiones, preview resuelto y operaciones atómicas de nodos/grupos |
| `deployment_v3/inventory_editor/templates.py` | Ofrecer plantillas compactas además de las v3 existentes |
| `local-infra/generate-lima-inventory.py` | Salida compacta optativa manteniendo salida v3 compatible |
| `tests/` y `examples/` | Fixtures completos de ambos formatos, equivalencia y errores operativos |

Contrato orientativo del nuevo módulo:

```python
@dataclass(frozen=True)
class ResolutionResult:
    document: dict              # exclusivamente el contrato v3
    provenance: dict            # JSON Pointer de salida -> origen/regla
    warnings: tuple[str, ...]
    metadata: dict              # catálogo, digests, versión del resolver

def resolve_inventory(raw: dict, *, source_path: Path, catalogs) -> ResolutionResult:
    ...
```

No usar archivos temporales como interfaz normal entre resolver y validador.
Actualmente `InventoryDocument.validate()` escribe un temporal: extraer la
validación en memoria también evita romper fuentes relativas en el formato nuevo.

El `schema.json` v3 conserva su contrato. La adaptación local debe inicialmente
generar campos de compatibilidad que v3 exige, documentándolos como derivados
sin efecto SSH. Una segunda mejora interna puede hacer opcionales esos campos
para `execution=local`, auditando executor/network/runner y pruebas. Nunca ocultar
una dirección ficticia presentándola como red real del host.

## 9. CLI y evidencia

Comandos nuevos propuestos:

```bash
# Sólo resolución, validación y escritura explícita de salida pública
venv/bin/python deploy.py inventory-resolve --inventory site.json --output resolved.json

# Explicar por qué existe un valor o una publicación
venv/bin/python deploy.py inventory-explain --inventory site.json --path /services/store-api/connections

# Comparar el efecto de dos entradas, incluyendo catálogo y defaults
venv/bin/python deploy.py inventory-diff --before previous.json --after site.json
```

Los comandos actuales deben aceptar ambos formatos cuando la integración esté
completa. `validate` ejecutará toda la validación estructural y semántica
necesaria, incluidos grupos; `plan` y `render` operarán sobre la misma resolución.
Los comandos informativos no adquieren componentes ni leen valores de secretos.

Procedencia por campo: entrada explícita, default del catálogo, receta o regla
de derivación; incluir JSON Pointer de origen y regla aplicada. Un error debe
apuntar al campo editable responsable además del campo v3 que falló.

Ejemplo de diagnóstico deseado: «storage.peers.storage-b utiliza storage-2, pero
su máquina no tiene dirección en vpn, seleccionada por routing.storage_api».

El bundle debe conservar `shared/deployment-topology.json` con el documento
resuelto para compatibilidad. Añadir metadatos de resolución y, si se guarda la
entrada original, integrarla en el manifest público sin incluir valores privados.
Las rutas a secretos son información operativa visible, como en v3.

El lock de resolución registra digest de entrada canónica, catálogo, resolver y
salida resuelta. No equivale a un lock de commits ni a un artefacto blockchain.
`resume` usa el bundle revisado y rechaza divergencias semánticas; no recompila
con nuevos defaults en silencio. Cambios sólo de formato JSON no deben causar
una divergencia. La compatibilidad con bundles v3 antiguos requiere una rama
explícita de comportamiento y pruebas.

## 10. Editor orientado al trabajo diario

Pantallas propuestas: identidad, máquinas, distribución, blockchain, storage,
redes/acceso, fuentes privadas y ajustes avanzados. Mostrar una vista resuelta
de sólo lectura y el resumen del cambio pendiente.

- «Añadir peer» solicita ID, grupo y máquina; actualiza el documento compacto
  en una transacción y muestra servicios/rutas derivados.
- «Mover grupo» elige una máquina existente y muestra puertos y rutas afectados.
- «Cambiar réplicas» muestra peers y máquinas disponibles y valida ambos umbrales.
- «Ajustes avanzados» distingue valor heredado y override, con acción restaurar.

Usar selectores para vocabularios y referencias existentes; entradas libres para
IDs, rutas y direcciones. No exigir validar un estado intermedio inconsistente:
editar un borrador de operación y validar el conjunto al confirmar.

Conservar backups y guardado atómico. Editar un v3 existente no debe convertirlo
automáticamente ni cambiar su estructura. No intentar reconstruir un documento
compacto a partir de una salida editada manualmente sin una conversión explícita.

## 11. Migración gradual

### Fase 1: extraer validación y preservar comportamiento

Separar parseo, validación en memoria y construcción de plan. Demostrar que los
ejemplos v3 existentes mantienen servicios, grupos, endpoints y artefactos
públicos. No introducir todavía nuevos defaults en la ruta legacy.

### Fase 2: compilador offline y catálogo inicial

Implementar schema compacto, recetas, procedencia y `inventory-resolve`.
Crear fixtures completos para los tres escenarios. Mantener producción/Lima
como plantillas adaptables, sin valores privados reales.

### Fase 3: integración con CLI y bundles

Integrar ambos formatos en comandos actuales y añadir comprobaciones de
resolución para push/apply/resume. Implementar explain y diff. Probar que toda
ruta de ejecución usa el mismo documento resuelto.

### Fase 4: editor y generador Lima

Añadir formularios y plantillas compactas. Actualizar generador Lima con opción
de formato. Publicar guía de operaciones frecuentes y diferencias de colocación.

### Fase 5: conversión asistida

Un convertidor futuro reconoce sólo topologías representables sin pérdidas.
Debe preservar IDs, grupos Compose, rutas, exposiciones, puertos, orden relevante,
secretos y componentes. Si algo no es representable, informa la diferencia y
mantiene el documento v3; nunca descarta campos silenciosamente.

La adopción inicial se dirige a nuevas instalaciones. Cambiar de representación
en una instalación existente sólo se considera neutro después de comparar su
plan efectivo. Conservar la entrada y bundle anteriores permite volver al
formato anterior; no revierte datos ni una reubicación ya ejecutada.

## 12. Pruebas y criterios de aceptación

| Área | Evidencia exigida |
| --- | --- |
| Compatibilidad v3 | Ejemplos actuales conservan planes y render semánticamente equivalentes |
| Expansión | Cada peer produce exactamente su Kubo/Cluster y conexiones/consumidores correctos |
| Colocación | Cada servicio tiene un grupo y máquina; mover un grupo actualiza todas sus relaciones |
| Red | DNS local, rutas remotas explícitas, fallo por red ausente/ambigua y colisión de puertos |
| Identidad | Reordenar información irrelevante no renombra nodos ni cambia puertos; preservar orden significativo |
| Defaults | Catálogo inmutable; overrides válidos sustituyen sólo lo indicado |
| Errores | Claves desconocidas y referencias rotas apuntan a la entrada editable |
| Secretos | Sólo referencias; fuentes relativas usan la base correcta; consumidores no se amplían accidentalmente |
| Estado | Bundle y resolución coherentes; resume falla antes de actuar si cambian decisiones |
| Editor | Añadir/mover/eliminar se valida como operación completa; cancelar y backups conservan su comportamiento |
| Operación | Instalación local y SSH/Lima verificadas separadamente de las pruebas offline |

Para equivalencia comparar objetos y resultados operativos, no sólo texto:
servicios, proyectos Compose, montajes persistentes, endpoints, publicaciones,
variables, secretos y contexto blockchain. Ignorar únicamente diferencias
documentadas como rutas de archivos de evidencia o formato JSON.

Casos negativos mínimos: tres validadores, peer duplicado, grupo inexistente,
réplicas superiores a peers, proveedor requerido en dos redes incompatibles,
override desconocido, catálogo ausente/modificado y cambio de identidad con
bundle anterior. Añadir también el caso de dos peers en una sola máquina, que
es válido pero debe describirse correctamente.

La mejora de usabilidad se comprueba con tareas: crear local-simple sin campos
SSH; añadir storage con una declaración; mover un grupo sin editar cada
servicio; modificar un parámetro sin copiar todo settings; explicar cualquier
puerto publicado. El número de líneas es una señal secundaria.

## 13. Decisiones pendientes y límites

- Confirmar nombres definitivos del formato/catálogo antes de publicar su schema.
- Fijar la convención de nombres bajo `secrets.source_root` de acuerdo con la
  salida real de `secrets-init`, evitando dos convenciones incompatibles.
- Precisar cómo preservar agrupamiento sintético de local-simple en conversión.
- Definir qué exposiciones adicionales soportan los overrides iniciales para
  representar exactamente producción sin ampliar acceso de manera implícita.
- Decidir después si se admite YAML, múltiples stacks o lock de commits; ninguno
  es necesario para el primer compilador.

Las decisiones anteriores no impiden implementar el resolver offline, pero
deben cerrarse con fixtures antes de habilitar instalación compacta en producción.

## 14. Documentación que debe acompañar la implementación

Actualizar `deployment-v3-inventory-and-artifact-flow.md` para distinguir entrada
editable y contrato resuelto, y añadir una sección dedicada a `storage`.
Actualizar el README de ejemplos: actualmente afirma que local-simple conserva
dos peers, aunque su JSON contiene uno, y repite la advertencia de producción.

Mantener ejemplos v3 como referencia del contrato completo. Añadir compactos en
un directorio separado y señalar siempre cuáles son ejecutables y cuáles son
fragmentos explicativos. Documentar las fuentes de defaults, cómo inspeccionarlas
y cómo revisar una actualización de catálogo antes de aplicarla.

El resultado buscado es una entrada que describa decisiones humanas y una salida
que conserve la precisión técnica del inventario actual. El trabajo principal
está en normalización, recetas, trazabilidad y compatibilidad; el motor de
despliegue sigue ejecutando el contrato v3 validado.
