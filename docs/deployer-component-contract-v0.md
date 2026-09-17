# Deployer component contract v0 (corte vertical)

- **Fecha:** 2026-09-17
- **Estado:** Especificación de contrato mínima para el corte vertical (Fase 2 de `deployer-generalization.md`).
  No es una API existente ni una decisión aprobada; es candidata a validación por implementación.
- **Alcance:** lo mínimo para desplegar un caso completo —aplicación + dos proveedores compatibles
  + binding explícito cross-host + job con output consumido— por la ruta CLI → resolver → plan →
  bundle → apply → verify. Lo que queda fuera: composición de catálogos, reconciliador continuo,
  familias P2P, convergencia de cluster, sandbox de hooks.
- **Relacionado:** `docs/deployer-generalization.md` (justificación y razonamiento).

Este documento es **normativo para el corte vertical** y corrige las imprecisiones de los bosquejos
de `deployer-generalization.md` secciones 13–14 (esas se quedan como ilustrativas). Donde haya
contradicción, prevalece este contrato.

---

## 1. Estructuras centrales

### 1.1 ComponentDefinition (`component.json`)

```json
{
  "schema_version": 0,
  "id": "spring-boot-api",
  "kind": "service",
  "category": "applications",
  "runtime": {
    "fragment": "compose.yml",
    "resources": {"disk_min_gb": 1, "memory_mb": 512}
  },
  "provides": [
    {
      "offer": "http",
      "capability": "http-api",
      "interface_major": 1,
      "endpoint": "http",
      "port": 8080,
      "transport": "tcp",
      "app_protocol": "http",
      "exposure": "private",
      "fields": {"base_path": "/"}
    }
  ],
  "requires": [
    {
      "name": "database",
      "capability": "database.postgresql",
      "interface_major": 1,
      "cardinality": "one",
      "condition": "healthy",
      "env": {
        "SPRING_DATASOURCE_URL": "jdbc:postgresql://{{host}}:{{port}}/{{database}}"
      }
    },
    {
      "name": "schema",
      "capability": "schema-migration.output",
      "interface_major": 1,
      "cardinality": "one",
      "condition": "completed",
      "env_file": "schema.env"
    }
  ],
  "secrets": [
    {"id": "api-credentials", "consumption": "env-file", "path": "api-credentials.env"}
  ],
  "probes": {
    "readiness": [
      {"type": "http", "perspective": "consumer-host",
       "port": 8080, "path": "/actuator/health", "status": 200, "timeout_seconds": 30}
    ],
    "health": [
      {"type": "http", "perspective": "provider-container",
       "port": 8080, "path": "/actuator/health", "status": 200}
    ]
  },
  "constraints": {"colocate_with": [], "anti_colocate_with": []},
  "hooks": {
    "api_version": 0,
    "validate": null,
    "render_config": null,
    "prepare": null,
    "readiness": null,
    "verify": null
  }
}
```

Cambios respecto a los bosquejos previos:

- `provides.offer` (nombre local de la oferta) se separa de `capability` y de `endpoint` (nombre
  local del endpoint). Una instancia puede tener varias ofertas y varios endpoints.
- `interface_major` sustituye a `version`: compatibilidad por major de interfaz, no versión de
  producto.
- `transport` (tcp/udp) se separa de `app_protocol` (http/postgresql/jsonrpc…). El filtro de
  proveedor va sobre `app_protocol`/`fields`, no sobre transporte.
- `requires.name` es el nombre local del requisito (una app puede necesitar dos bases de datos:
  `database-read` y `database-write`).
- `requires.condition`: `created` (proveedor aplicado) | `healthy` (readiness OK) | `completed`
  (one-shot con éxito). Separa dependencia de arranque de relación operativa (§8).
- `secrets.consumption`: `file-mount` | `env-file`. El respeta cómo consume cada aplicación; no
  convierte una en la otra (corrige el error de `minter-postgres`, §5).
- `probes.perspective` (§6).
- `hooks` lista las cinco etapas explícitamente (§7).

### 1.2 `provides` para un one-shot (salida artefacto)

Un one-shot no expone puerto. Su `provides` declara una oferta de tipo artefacto:

```json
{
  "offer": "output",
  "capability": "schema-migration.output",
  "interface_major": 1,
  "kind": "artifact-env-file",
  "output": "schema.env",
  "fields": {"SCHEMA_VERSION": {}}
}
```

Sin `port`, `transport`, `exposure`. El `kind` y el `output` dicen al núcleo que esta oferta se
satisface distribuyendo un fichero, no resolviendo una dirección. El registro de interfaces (§3)
declara el `kind` de la capacidad, de modo que el `requires` **no** declara `satisfaction`: lo
hereda de la interfaz.

### 1.3 Registro de interfaces (`catalog.json`)

```json
"interfaces": {
  "database.postgresql": {
    "interface_major": 1,
    "kind": "network",
    "fields": {
      "database": {"type": "string", "required": true},
      "scheme":   {"type": "string", "const": "postgresql"}
    },
    "compatibility": "same-major-and-required-fields"
  },
  "http-api": {
    "interface_major": 1,
    "kind": "network",
    "fields": {"base_path": {"type": "string", "default": "/"}},
    "compatibility": "same-major-and-required-fields"
  },
  "schema-migration.output": {
    "interface_major": 1,
    "kind": "artifact-env-file",
    "fields": {"SCHEMA_VERSION": {"type": "string", "required": true}},
    "compatibility": "same-major-and-required-fields"
  }
}
```

- `kind`: `network` | `artifact-env-file`. Determina la forma de satisfacción de todo `requires`
  que la referencia.
- `fields` es el esquema de los campos que las plantillas de env pueden referenciar. Si una
  plantilla usa `{{database}}` y ni el proveedor ni el esquema lo aportan, la resolución falla.
- `compatibility`: en v0, `same-major-and-required-fields` (mismo `interface_major` y campos
  obligatorios presentes). Rangos y negociación dinámica se posponen.

### 1.4 Parámetros de instancia

Una instancia en el inventario puede declarar `params` (ver §11.4). Los params están disponibles
como placeholder `{{params.<nombre>}}` en dos sitios:

- en valores del fragmento Compose (p. ej. `POSTGRES_DB: "{{params.database}}"`);
- en valores de `provides.fields` (p. ej. `"database": "{{params.database}}"`), de modo que el
  campo que el proveedor expone a las plantillas de los consumidores refleja la configuración real
  de la instancia.

Así un mismo componente `postgres` instanciado dos veces puede exponer distintos `database` a sus
consumidores sin duplicar el componente. La validación de params obligatorios es responsabilidad del
hook `validate` en v0 (un `params_schema` declarativo se pospone); un param ausente referenciado en
una plantilla o fragmento se detecta como campo ausente en resolución.

---

## 2. Bindings: tres capas con precedencia

Un binding identifica **qué instancia y qué oferta** satisfacen un `requires` concreto, y (para
red) en qué red. Tres capas, gana la de arriba:

1. **Binding explícito del inventario.**
2. **Binding por defecto de la receta** (si el componente está en una receta que liga este
   requisito).
3. **Autoselección**: exactamente un candidato válido en el ámbito → ligar; cero → error; más de
   uno → **error de ambigüedad** (nunca elegir el primero en silencio).

Candidato válido = instancia colocada que `provides` la `capability` con `interface_major` igual y
cuyos `fields` cumplen el esquema. El ámbito es el despliegue (mismo `deployment_id`).

Forma del binding en el inventario:

```yaml
bindings:
  api.database:                  # <instancia>.<nombre-de-requisito>
    provider: database-b.sql      # <instancia-proveedora>.<oferta>
    network: backend              # solo para kind=network
  api.schema:                     # kind=artifact-env-file: sin network
    provider: migrate.output
```

- Para `kind=network`: `provider` + `network`. El resolver comprueba que el proveedor expone un
  endpoint en esa red y que el consumidor la comparte o tiene ruta declarada.
- Para `kind=artifact-env-file`: `provider` solamente. No hay endpoint ni red.

**Recetas** (`catalog.json`):

```json
"recipes": {
  "minter-stack": {
    "members": ["minter-api", "minter-postgres", "minter-worker-metadata"],
    "bindings": {
      "minter-api.database": {"provider": "minter-postgres.sql"}
    },
    "constraints": {"colocate": "all"}
  }
}
```

La receta aporta bindings por defecto entre sus miembros. El inventario puede *override* un binding
que la receta fija, pero no puede crear bindings que la receta prohíba (v0: las recetas no
prohíben, solo aportan defaults; la prohibición se pospone).

---

## 3. Resolver: traza esperada

Para cada instancia colocada, por cada `requires`:

1. Buscar binding en orden §2. Si no hay y la cardinalidad es `one`, error.
2. Si hay binding explícito: validar que el `provider` existe, está colocado, y su `provides`
   matches `capability` + `interface_major` + campos. Si no, error con nombres.
3. Si no hay binding explícito ni de receta: autoselección. Recoger candidatos válidos. Si ≠1,
   error (ambigüedad o ausencia) nombrando los candidatos.
4. Según `kind` de la interfaz:
   - `network`: resolver endpoint con `network.endpoint_for` (existente) en la red del binding;
     exponer campos `host`, `port`, `url` + los `fields` del proveedor a las plantillas de env.
   - `artifact-env-file`: registrar la referencia al output del one-shot (productor + oferta +
     run); no hay endpoint.
5. Validar las plantillas de env del `requires` contra los campos disponibles; campo ausente →
   error en resolución (no en runtime).
6. Validar `condition` (§8) para ordenar el plan.

El resolver **no** ejecuta nada ni lee secretos. Su salida es el grafo de bindings + endpoints +
referencias de artefactos, auditable.

---

## 4. Perfil Compose soportado

Cada fragmento es **un servicio**. Tabla de propiedad de campos (gana el núcleo salvo donde se
indica; conflicto = error temprano):

| Campo Compose | Propietario | Nota |
| --- | --- | --- |
| `image` / `build` | fragmento | el fragmento usa `{{catalog.images.x}}` para imágenes del catálogo |
| `container_name` | **rechazado** | impide múltiples instancias del componente |
| `networks` | núcleo | desde el plan; el fragmento no lo define |
| `ports` | núcleo | desde `provides` + exposure; el fragmento no lo define |
| `environment` (estático) | fragmento | valores estáticos como `IPFS_TELEMETRY` |
| `environment` (generado) | núcleo | desde `requires.env` plantillado |
| colisión misma clave en estático+generado | **error** | fuerza explicitación |
| `env_file` | núcleo | orden: público generado → `artifact-env-file` → secretos `env-file` |
| `volumes` (persistente/config) | fragmento | |
| `volumes` (secretos `file-mount`) | núcleo | desde `secrets` |
| colisión mismo destino de montaje | **error** | |
| `labels` | núcleo | `org.dark.deployment.id`, `org.dark.service.id` + etiquetas neutrales |
| `depends_on` | núcleo | desde el plan (condiciones §8) |
| `command` / `entrypoint` / `restart` / `profiles` | fragmento | |

Campos fuera del perfil (p. ej. `extends`, `networks` no vacío en el fragmento) → error con el
campo y una explicación, antes de cualquier efecto remoto.

---

## 5. Secretos

Cada `secrets[*]` declara `{id, consumption, path}`:

- `consumption: "file-mount"` → el núcleo monta el fichero en el contenedor; `mount` required.
- `consumption: "env-file"` → el núcleo añade la ruta a `env_file` (después del env generado y de
  los `artifact-env-file`, antes de secretos runtime si los hubiera).

El núcleo **respeta** el modo declarado; nunca convierte `file-mount` en `env-file` ni viceversa.
Eso corrige el caso real: `minter-postgres` consume `minter-runtime-env` como `env-file`
(`services.py:110-111`), no como montaje. En el nuevo formato:

```json
"secrets": [
  {"id": "minter-runtime-env", "consumption": "env-file", "path": "minter-runtime-env.env"}
]
```

Los valores de secretos no se interpolan en plantillas de env ni aparecen en el bundle público.

---

## 6. Sondas

```json
{"type": "http", "perspective": "consumer-host",
 "port": 8080, "path": "/actuator/health", "status": 200,
 "timeout_seconds": 30, "retries": 3, "interval_seconds": 2}
```

- `perspective`: `controller` | `provider-host` | `consumer-host` | `provider-container` |
  `consumer-container`. Determina desde dónde ejecuta el executor la sonda (SSH/local/docker-exec).
  Un puerto accesible por Docker DNS puede no serlo desde el controlador.
- `type`: `tcp` | `http` | `jsonrpc` | `command` | `compose-status`. Parámetros por tipo; `http`
  admite `method`, `headers` (referenciando secretos), `body_contains`; `jsonrpc` admite `method`,
  `params`, `expect_field`, `expect_value`.
- `status` numérico (200) o gramática restringida `"<500"` (a validar en implementación).
- Las sondas de `readiness` son **re-ejecutables** (idempotentes): deben poder correr contra una
  instancia ya arrancada, no solo post-create.
- `health` es un concepto separado: declarar una sonda de health **no** implica que el instalador la
  ejecute periódicamente; eso es tarea del reconciliador (fuera de v0).

Predicados derivados (quórum, lag, convergencia) → hook `readiness` (§7), no sonda.

---

## 7. Hooks

Cinco etapas, cada una opcional, con contrato independiente:

| Etapa | Entrada (HookContext) | Salida | Efectos permitidos |
| --- | --- | --- | --- |
| `validate` | parámetros + bindings resueltos | diagnósticos | ninguno |
| `render_config` | contexto de render + inputs declarados | archivos públicos + valores | ninguno fuera de la salida declarada |
| `prepare` | contexto de ejecución | referencias de artefactos + resultado | operaciones **declaradas** y registradas |
| `readiness` | instancia + bindings + runtime | `{ok, detail}` | consultas de comprobación |
| `verify` | ámbito del componente + runtime | evidencia + `{ok, detail}` | consultas según contrato |

**HookContext** es versionado y acotado: parámetros del componente, identidad de instancia,
bindings resueltos, referencias de artefactos, metadatos del ámbito. **No** se pasa `plan.raw`
completo. Los adaptadores dARK traducen el inventario legado a este contexto.

Propiedades por etapa:

- `validate`, `render_config`, `readiness`, `verify`: **idempotentes** (declarado, se prueba).
- `prepare`: declara su idempotencia y sus efectos; si no es idempotente, el reintentar está
  prohibido salvo política explícita.
- `runtime` es un handle acotado (consultas HTTP/JSON-RPC, `compose ps`, exec en contenedores),
  no acceso libre al proceso.

Ejecución y errores:

- v0: **solo catálogos locales de confianza**. No se promete sandbox para plugins de terceros.
- Cada invocación: identidad, timeout, resultado estructurado, clasificación de error
  (`transient` | `invalid-config` | `plugin-failed`).
- Un timeout por parámetro **no** garantiza interrumpir una función Python; si se necesita
  interrupción real, el hook debe ejecutarse como contenedor one-shot (fuera de v0 para hooks
  generales; `prepare` con efectos puede optar por ello).
- `api_version` cubre contexto + firma + formato de resultado.

`render_config` es la sede de la generación de `nginx.conf`, `storage-endpoints.json`, etc.: el
hook devuelve archivos; el núcleo los escribe en el bundle, calcula su hash y los monta. El núcleo
no conoce nginx.

---

## 8. Lifecycle y plan

- El plan es **topológico sobre `requires` + `condition`**, no sobre categorías.
- `condition`: `created` (proveedor aplicado) | `healthy` (readiness del proveedor OK) |
  `completed` (one-shot con éxito). Por defecto `created`. Para un `requires` de kind
  `artifact-env-file`, `completed` significa que el one-shot terminó con éxito **y** su output ha
  sido distribuido al host del consumidor y verificado por digest; el consumidor no arranca hasta
  entonces.
- `category` es **agrupación/prioridad visual** entre pasos igualmente habilitados; no impone la
  secuencia dARK `validators→rpc→…` a otros catálogos.
- **Dependencia de arranque vs relación operativa**: dos miembros de un cluster pueden necesitar
  comunicarse mutuamente sin que cada uno esté *healthy* antes de iniciar al otro. Se modela como
  `condition: created` para arranque + una comprobación de convergencia a nivel de grupo (hook
  `readiness` del grupo) tras levantar el grupo. Un ciclo operativo **no** se convierte en ciclo
  imposible del plan.
- v0 no soporta ciclos operativos generales; el corte vertical no los necesita.

---

## 9. One-shots / jobs

```json
"lifecycle": {
  "one_shot": {
    "timeout_seconds": 1800,
    "success": {"exit_code": 0, "outputs_present": ["schema.env"]},
    "retry": {"retriable": true, "max_attempts": 3, "invalidates_outputs": ["schema.env"]},
    "outputs": [
      {"name": "output", "kind": "artifact-env-file", "path": "schema.env",
       "schema": {"SCHEMA_VERSION": "string"}, "sensitivity": "public"}
    ]
  }
}
```

- **Completion** = `exit_code` + `outputs_present` + validación de esquema. No basta el código.
- **Identidad del output**: productor (instancia + oferta + run), tipo, esquema, digest. Se
  registra en estado. `schema.env` es un nombre físico; la identidad distingue dos productores o
  despliegues.
- **Retry**: política declarada. Reintentar el job invalida los outputs listados.
- **Distribución**: el núcleo entrega el output a los consumidores (cross-host si hace falta) y
  verifica integridad (digest). Si la distribución falla tras un job exitoso, **se reintenta la
  distribución sin re-ejecutar el job**.
- El fingerprint del consumidor incluye el digest del artefacto consumido.

---

## 10. Fingerprints

Entradas del fingerprint de un componente (para resume/idempotencia):

- digest del manifiesto + digest del fragmento + digest de `config_files` + digest de `hooks`.
- bindings resueltos + referencias de artefactos (con sus digests).
- declaración de recursos.

Política de imagen: digest preferido; si no, tag **inmutable**; tag mutable → warning y **no**
cuenta como identidad. La versión del catálogo no sustituye al digest de su contenido: un cambio de
hook o de un archivo montado invalida la reutilización aunque el nombre/versión no cambien.

Reutilizar por fingerprint es una **condición de recuperación**, no prueba de corrección del estado
real; las comprobaciones previas a omitir un paso las define lifecycle + tipo de recurso.

---

## 11. Corte vertical: ejemplo completo

Stack: una API Spring Boot que usa Postgres y consume el output de un job de migración de esquema.
Dos instancias de Postgres en **hosts distintos** para forzar selección explícita y endpoint
cross-host. El job corre en un host y su output se consume en otro para forzar distribución
cross-host de artefacto.

### 11.1 Componente `postgres`

`component.json`:

```json
{
  "schema_version": 0, "id": "postgres", "kind": "service", "category": "data",
  "runtime": {"fragment": "compose.yml", "resources": {"disk_min_gb": 1}},
  "provides": [
    {"offer": "sql", "capability": "database.postgresql", "interface_major": 1,
     "endpoint": "sql", "port": 5432, "transport": "tcp", "app_protocol": "postgresql",
     "exposure": "private",
     "fields": {"database": "{{params.database}}", "scheme": "postgresql"}}
  ],
  "requires": [],
  "secrets": [
    {"id": "postgres-credentials", "consumption": "env-file", "path": "postgres-credentials.env"}
  ],
  "probes": {
    "readiness": [{"type": "tcp", "perspective": "provider-host", "port": 5432, "timeout_seconds": 30}],
    "health":    [{"type": "tcp", "perspective": "provider-host", "port": 5432}]
  },
  "constraints": {},
  "hooks": {"api_version": 0, "validate": null, "render_config": null, "prepare": null, "readiness": null, "verify": null}
}
```

`compose.yml`:

```yaml
services:
  postgres:
    image: "{{catalog.images.postgres}}"
    environment: {"POSTGRES_DB": "{{params.database}}"}
    volumes:
      - "{{data}}/{{service.id}}:/var/lib/postgresql/data"
    restart: unless-stopped
```

Nótese: `POSTGRES_DB=app` es estático en el fragmento; la contraseña va en el secreto
`postgres-credentials` consumido como `env-file` (el núcleo lo añade a `env_file`, no lo monta).
`{{database}}` se expone a las plantillas de los consumidores desde `provides.fields.database`.

### 11.2 Componente `schema-migrate` (one-shot)

`component.json`:

```json
{
  "schema_version": 0, "id": "schema-migrate", "kind": "one-shot", "category": "data",
  "runtime": {"fragment": "compose.yml"},
  "provides": [
    {"offer": "output", "capability": "schema-migration.output", "interface_major": 1,
     "kind": "artifact-env-file", "output": "schema.env",
     "fields": {"SCHEMA_VERSION": {}}}
  ],
  "requires": [
    {"name": "database", "capability": "database.postgresql", "interface_major": 1,
     "cardinality": "one", "condition": "healthy",
     "env": {"JDBC_URL": "jdbc:postgresql://{{host}}:{{port}}/{{database}}"}}
  ],
  "secrets": [
    {"id": "postgres-credentials", "consumption": "env-file", "path": "postgres-credentials.env"}
  ],
  "probes": {},
  "constraints": {},
  "hooks": {"api_version": 0, "validate": null, "render_config": null, "prepare": null, "readiness": null, "verify": null},
  "lifecycle": {
    "one_shot": {
      "timeout_seconds": 600,
      "success": {"exit_code": 0, "outputs_present": ["schema.env"]},
      "retry": {"retriable": true, "max_attempts": 3, "invalidates_outputs": ["schema.env"]},
      "outputs": [
        {"name": "output", "kind": "artifact-env-file", "path": "schema.env",
         "schema": {"SCHEMA_VERSION": "string"}, "sensitivity": "public"}
      ]
    }
  }
}
```

`compose.yml`:

```yaml
services:
  schema-migrate:
    profiles: ["setup"]
    build: {"context": "{{workspace_root}}/components/schema-migrate", "dockerfile": "Dockerfile"}
    volumes:
      - "{{data}}/{{service.id}}:/runtime"
    restart: "no"
```

El job escribe `/runtime/schema.env` con `SCHEMA_VERSION=...`. El núcleo lo recoge, lo hashea, lo
registra con identidad (instancia `migrate` + oferta `output` + run) y lo distribuye a los
consumidores.

### 11.3 Componente `spring-boot-api`

`component.json`:

```json
{
  "schema_version": 0, "id": "spring-boot-api", "kind": "service", "category": "applications",
  "runtime": {"fragment": "compose.yml", "resources": {"memory_mb": 512}},
  "provides": [
    {"offer": "http", "capability": "http-api", "interface_major": 1,
     "endpoint": "http", "port": 8080, "transport": "tcp", "app_protocol": "http",
     "exposure": "private", "fields": {"base_path": "/"}}
  ],
  "requires": [
    {"name": "database", "capability": "database.postgresql", "interface_major": 1,
     "cardinality": "one", "condition": "healthy",
     "env": {"SPRING_DATASOURCE_URL": "jdbc:postgresql://{{host}}:{{port}}/{{database}}"}},
    {"name": "schema", "capability": "schema-migration.output", "interface_major": 1,
     "cardinality": "one", "condition": "completed",
     "env_file": "schema.env"}
  ],
  "secrets": [
    {"id": "postgres-credentials", "consumption": "env-file", "path": "postgres-credentials.env"}
  ],
  "probes": {
    "readiness": [{"type": "http", "perspective": "consumer-host", "port": 8080,
                   "path": "/actuator/health", "status": 200, "timeout_seconds": 30}],
    "health":    [{"type": "http", "perspective": "provider-container", "port": 8080,
                   "path": "/actuator/health", "status": 200}]
  },
  "constraints": {},
  "hooks": {"api_version": 0, "validate": null, "render_config": null, "prepare": null, "readiness": null, "verify": null}
}
```

`compose.yml`:

```yaml
services:
  spring-boot-api:
    build: {"context": "{{workspace_root}}/components/api", "dockerfile": "Dockerfile"}
    volumes:
      - "{{data}}/{{service.id}}:/app/data"
    restart: unless-stopped
```

### 11.4 Inventario (corte vertical)

```json
{
  "format": "dark-operator-inventory", "format_version": 4,
  "catalog": "vertical-cut-v0",
  "deployment": {"id": "vertical-cut"},
  "networks": {"backend": {"kind": "lan", "cidr": "10.40.0.0/24"}},
  "machines": {
    "host-a": {"execution": "local", "addresses": {"backend": "10.40.0.10"}},
    "host-b": {"execution": "local", "addresses": {"backend": "10.40.0.20"}}
  },
  "placement": {
    "api": "host-a",
    "database-a": "host-a",
    "database-b": "host-b",
    "migrate": "host-b"
  },
  "instances": {
    "api":            {"component": "spring-boot-api"},
    "database-a":     {"component": "postgres", "params": {"database": "app"}},
    "database-b":     {"component": "postgres", "params": {"database": "app"}},
    "migrate":        {"component": "schema-migrate"}
  },
  "bindings": {
    "api.database":      {"provider": "database-b.sql", "network": "backend"},
    "api.schema":        {"provider": "migrate.output"},
    "migrate.database":  {"provider": "database-a.sql", "network": "backend"}
  },
  "secrets": {
    "source_root": "REPLACE/secrets"
  }
}
```

Lo que ejercita este inventario:

- **Dos proveedores compatibles** (`database-a`, `database-b`) con la misma capacidad; el inventario
  elige explícitamente `database-b` para `api` (cross-host) y `database-a` para `migrate`
  (cross-host desde host-b). Sin el binding explícito, el resolver daría error de ambigüedad.
- **Binding de red cross-host**: `api` (host-a) → `database-b` (host-b) por `backend`; el resolver
  deriva `host=10.40.0.20`, `port=5432`, `database=app` y plantilla
  `jdbc:postgresql://10.40.0.20:5432/app`.
- **Binding de artefacto cross-host**: `migrate` (host-b) produce `schema.env`; `api` (host-a) lo
  consume. El núcleo distribuye el fichero host-b → host-a y verifica digest.
- **Secreto `env-file`**: `postgres-credentials` consumido como `env-file` por `postgres`,
  `schema-migrate` y `spring-boot-api` (cada uno lo declara en su `secrets`).

### 11.5 Traza del resolver

```
api.database:    binding explícito → database-b.sql, red backend
                 database-b provides database.postgresql v1, fields {database: app, scheme: postgresql}
                 endpoint_for(api@host-a, database-b@host-b, backend) → host=10.40.0.20, port=5432
                 env: SPRING_DATASOURCE_URL=jdbc:postgresql://10.40.0.20:5432/app   ✓
api.schema:      binding explícito → migrate.output (kind=artifact-env-file)
                 sin endpoint; referencia al output del run de migrate
                 env_file: schema.env (a distribuir tras completed)   ✓
migrate.database: binding explícito → database-a.sql, red backend
                  endpoint_for(migrate@host-b, database-a@host-a, backend) → host=10.40.0.10, port=5432
                  env: JDBC_URL=jdbc:postgresql://10.40.0.10:5432/app   ✓
```

### 11.6 Plan esperado (orden topológico por condition)

```
preflight:host-a, preflight:host-b
apply:database-a (host-a)          condition para migrate.database = healthy
apply:database-b (host-b)          condition para api.database = healthy
readiness:database-a (tcp 5432, provider-host)
readiness:database-b (tcp 5432, provider-host)
apply:migrate (host-b, one-shot)   condition: migrate.database healthy
verify:migrate (outputs_present schema.env, schema ok) → registra artefacto con digest
distribute:migrate.output → host-a (consumido por api)
apply:api (host-a)                 conditions: api.database healthy AND api.schema completed
readiness:api (http /actuator/health, perspective consumer-host = host-a)
verify:deployment
```

`category` (`data`, `applications`) solo agrupa; el orden real lo imponen las `condition`.

### 11.7 Modos de fallo que el contrato debe rechazar antes de aplicar

- **Ambigüedad**: inventario sin `api.database` y sin receta → dos candidatos (`database-a`,
  `database-b`) → error nombrando ambos.
- **Kind incompatible**: `api.schema` ligado a `database-a.sql` → la interfaz de `database-a` es
  `network`, no `artifact-env-file` → error.
- **Campo ausente**: una plantilla `{{database}}` donde el proveedor no declara `database` y el
  esquema de la interfaz no lo trae → error en resolución.
- **Perfil Compose**: fragmento con `container_name` o con `networks` definido → error con el campo.
- **Conflicto de env**: misma clave `environment` en fragmento estático y en generado → error.
- **Job sin output**: `migrate` termina con exit 0 pero `schema.env` no presente → no se marca
  `completed`, se reintenta según política; si se agotan intentos, fallo.
- **Distribución interrumpida**: job `completed` pero la distribución a host-a falla → se reintenta
  la distribución sin re-ejecutar el job (el artefacto ya está registrado con digest).

---

## 12. Lo que este contrato NO cubre (v0)

- Composición de catálogos (base + extensiones); un catálogo resuelto por despliegue, fijado por
  digest.
- Reconciliador continuo y actualización explícita general; solo instalación + recuperación.
- Familias P2P y convergencia de cluster; el corte vertical no las necesita.
- Sandbox para hooks de terceros; solo catálogos locales de confianza.
- Selección automática por preferencias/optimizador; solo candidato único o error.
- Rangos de compatibilidad y negociación dinámica; solo major + campos obligatorios.

Estos límites son deliberados y están alineados con la sección 24 de `deployer-generalization.md`.