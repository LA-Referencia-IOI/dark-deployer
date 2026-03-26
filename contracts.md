# dARK Stack REST Contracts

Documento de referencia de las APIs REST presentes en este workspace, basado en el código fuente activo el 2026-03-24.

## Alcance

Este documento cubre:

- `dark-core-admin-api`
- `dark-core-minter-api`
- `dark-core-resolver-api`
- `dark-store-api`

No cubre:

- JSON-RPC de blockchain en `:8545`
- IPFS API nativa en `:5001`
- IPFS Cluster API nativa en `:9094`
- frontend del explorador en `:25000`

## URLs locales actuales

| Servicio | Base URL local | Rol |
| --- | --- | --- |
| Admin API | `http://localhost:8000` | Alta y gestión administrativa de authorities |
| Minter API | `http://localhost:8001` | Reserva de ARKs, staging de metadata y estado del worker |
| Resolver API | `http://localhost:8002` | Resolución pública de ARKs |
| Store API | `http://localhost:8003` | Almacenamiento raw por CID |

## Autenticación y seguridad

### Admin API

- Todas las rutas administrativas usan `require_mtls`.
- En el estado actual del workspace, `MTLS_ENABLED=false`, así que en local esas rutas quedan efectivamente abiertas.
- En producción la intención es operar con mTLS habilitado.

### Minter API

- Las rutas mutantes de ARKs usan identidad de authority.
- Si `MTLS_ENABLED=true`, la identity puede venir del certificado o headers confiables.
- Si `MTLS_ENABLED=false`, hay que enviar uno de estos headers:
  - `X-Authority-Id`
  - `X-Authority-UUID`
- El `authority_id` del body debe coincidir con el authority autenticado.

### Resolver API

- No requiere autenticación.
- Está pensada como API pública de lectura.

### Store API

- No requiere autenticación.
- Está pensada como servicio interno/simple de storage raw.

## Convenciones generales

- Todas las APIs FastAPI exponen `/docs` y `/redoc`.
- Todas exponen `/health`.
- El prefijo funcional es:
  - Admin: `/api/v1/admin`
  - Minter: `/api/v1`
  - Resolver: `/api/v1`
  - Store: `/v1`

---

## 1. Admin API

Base URL: `http://localhost:8000`

### Propósito

Gestiona el ciclo de vida administrativo de authorities:

- registrar authorities
- autorizar o revocar NAANs
- desactivar authorities
- consultar balances
- fondear wallets
- inspeccionar el estado administrativo del nodo

### Endpoints

| Método | Ruta | Auth | Body | Respuesta | Para qué sirve |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/health` | No | No | JSON de salud | Verifica conectividad blockchain y balance del admin |
| `GET` | `/api/v1/admin/status` | mTLS o abierto en local | No | `AdminStatusResponse` | Devuelve dirección admin, balance, bloque actual y `chain_id` |
| `POST` | `/api/v1/admin/authority` | mTLS o abierto en local | `uuid`, `naans`, `fund_amount_eth?` | `AuthorityResponse` | Registra una nueva authority, crea wallet, autoriza NAANs y puede fondearla |
| `GET` | `/api/v1/admin/authority/{uuid}` | mTLS o abierto en local | No | `AuthorityResponse` | Consulta una authority por UUID |
| `POST` | `/api/v1/admin/authority/{uuid}/authorize-naan` | mTLS o abierto en local | `naan` | `OperationResponse` | Autoriza un NAAN adicional para una authority existente |
| `POST` | `/api/v1/admin/authority/{uuid}/revoke-naan` | mTLS o abierto en local | `naan` | `OperationResponse` | Revoca un NAAN ya autorizado |
| `POST` | `/api/v1/admin/authority/{uuid}/deactivate` | mTLS o abierto en local | No | `OperationResponse` | Desactiva una authority para impedir nuevas operaciones de mint |
| `GET` | `/api/v1/admin/authority/{uuid}/balance` | mTLS o abierto en local | No | `BalanceResponse` | Consulta el balance ETH de la wallet de una authority |
| `POST` | `/api/v1/admin/authority/{uuid}/fund` | mTLS o abierto en local | `amount_eth` | `OperationResponse` | Envía ETH desde la cuenta admin a la wallet de la authority |

### Payloads importantes

#### `POST /api/v1/admin/authority`

```json
{
  "uuid": "org-uuid-12345",
  "naans": ["12345", "67890"],
  "fund_amount_eth": 0.01
}
```

#### `POST /api/v1/admin/authority/{uuid}/authorize-naan`

```json
{
  "naan": "12345"
}
```

#### `POST /api/v1/admin/authority/{uuid}/fund`

```json
{
  "amount_eth": 0.01
}
```

### Notas funcionales

- La API admin es fina: delega la lógica real en `dark-core-lib`.
- No tiene base de datos propia.
- El source of truth es blockchain.

---

## 2. Minter API

Base URL: `http://localhost:8001`

### Propósito

Gestiona el write-side del ciclo de vida de un ARK:

- reservar ARKs localmente
- recibir metadata L1/L2
- mover estados a `DRAFT` o `UPDATE`
- exponer información de authorities
- exponer estado del worker

### Estados principales del ARK

- `RESERVED`: el ARK existe solo en la base local
- `DRAFT`: metadata staged, pendiente de publicación inicial
- `UPDATE`: metadata staged, pendiente de actualización on-chain
- `PUBLISHED`: publicado on-chain
- `TOMBSTONE`: borrado lógico local

### Endpoints de salud y operación

| Método | Ruta | Auth | Body | Respuesta | Para qué sirve |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/health` | No | No | JSON de salud | Verifica blockchain, Postgres y backend de metadata |
| `GET` | `/api/v1/worker/status` | No | No | JSON libre | Informa si el worker standalone está vivo según heartbeat en DB |

### Endpoints de authority

| Método | Ruta | Auth | Body | Respuesta | Para qué sirve |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/authority/{uuid}` | mTLS o abierto en local | No | `AuthorityResponse` | Consulta una authority por UUID |
| `GET` | `/api/v1/authority/{uuid}/naans` | mTLS o abierto en local | No | `AuthorityNAANsResponse` | Lista los NAANs autorizados para esa authority |
| `GET` | `/api/v1/authority/{uuid}/authorized/{naan}` | mTLS o abierto en local | No | `AuthorityAuthorizedResponse` | Indica si una authority está autorizada para un NAAN |

### Endpoints de ARKs

| Método | Ruta | Auth | Body | Respuesta | Para qué sirve |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/api/v1/arks` | Header/cert de authority | `authority_id`, `naan` | `ARKResponse` | Reserva un ARK nuevo con nombre determinístico |
| `POST` | `/api/v1/arks/batch` | Header/cert de authority | `authority_id`, `naan`, `items[]` | `ARKBatchResponse` | Reserva múltiples ARKs en lote |
| `GET` | `/api/v1/arks/{ark}` | mTLS o abierto en local | No | `ARKResponse` | Consulta un ARK desde DB local o blockchain |
| `PUT` | `/api/v1/arks/{ark}` | Header/cert de authority | `authority_id`, `target`, `minimal_metadata`, `original_metadata`, `metadata_schema`, `metadata_media_type` | `ARKResponse` | Stagea metadata y mueve el ARK a `DRAFT` o `UPDATE` |
| `DELETE` | `/api/v1/arks/{ark}` | Header/cert de authority | No | `204`/vacío | Marca el ARK como `TOMBSTONE` en la base local |

### Payloads importantes

#### `POST /api/v1/arks`

```json
{
  "authority_id": "org-uuid-12345",
  "naan": "12345"
}
```

Header requerido en local si `MTLS_ENABLED=false`:

```http
X-Authority-Id: org-uuid-12345
```

#### `POST /api/v1/arks/batch`

```json
{
  "authority_id": "org-uuid-12345",
  "naan": "12345",
  "items": [
    { "client_item_id": "item-1" },
    { "client_item_id": "item-2" }
  ]
}
```

#### `PUT /api/v1/arks/{ark}`

```json
{
  "authority_id": "org-uuid-12345",
  "target": "https://example.org/resource/1",
  "minimal_metadata": {
    "title": "Documento demo",
    "authors": ["Ada Lovelace"],
    "year": 2026
  },
  "original_metadata": "<record><title>Documento demo</title></record>",
  "metadata_schema": "dublin_core",
  "metadata_media_type": "application/xml"
}
```

### Qué hace realmente cada operación

#### `POST /api/v1/arks`

- valida que la authority esté autorizada para el NAAN
- incrementa el contador NOID
- genera el identificador `ark:/NAAN/name`
- inserta el registro en DB con estado `RESERVED`

#### `PUT /api/v1/arks/{ark}`

- valida autoridad y checkdigit si está activo
- valida el Level-1 JSON
- guarda L1 y L2 en Postgres
- no publica on-chain en ese momento
- deja el ARK listo para que el worker haga:
  - storage de Level-2
  - storage de Level-1 con referencia al Level-2
  - `create_ark` o `update_ark` on-chain

#### `DELETE /api/v1/arks/{ark}`

- solo hace tombstone local
- hoy no existe propagación on-chain del borrado

### Notas funcionales

- El minter es el servicio más stateful del stack.
- Usa PostgreSQL como source of truth local.
- El worker corre aparte y publica de forma asíncrona.

---

## 3. Resolver API

Base URL: `http://localhost:8002`

### Propósito

Resolver públicamente un ARK a:

- redirect al target
- proyección pública de metadata L1
- metadata L2 raw

### Endpoint principal

La resolver API usa un único route handler con tres comportamientos.

| Método | Ruta | Auth | Query | Respuesta | Para qué sirve |
| --- | --- | --- | --- | --- | --- |
| `GET` / `HEAD` | `/api/v1/arks/{ark}` | No | sin query | Redirect HTTP | Resuelve el ARK a su `target` on-chain |
| `GET` / `HEAD` | `/api/v1/arks/{ark}?info` | No | `?info` | `ArkInfoResponse` | Devuelve una vista pública de Level-1 |
| `GET` / `HEAD` | `/api/v1/arks/{ark}?metadata` | No | `?metadata` | bytes raw con `media_type` restaurado | Devuelve el Level-2 original |
| `GET` | `/health` | No | No | JSON de salud | Verifica blockchain y backend de metadata |

### Semántica por modo

#### Modo default

`GET /api/v1/arks/{ark}`

- lee el ARK desde blockchain
- toma el `url` on-chain
- responde con redirect HTTP

#### Modo `?info`

`GET /api/v1/arks/{ark}?info`

- lee `cid` desde blockchain
- descarga Level-1 desde storage compartido
- devuelve JSON público sin exponer CIDs internos

Campos típicos:

- `ark`
- `target`
- `created_at`
- `updated_at`
- `metadata_schema`
- `title`
- `authors`
- `year`
- `publisher`
- `resource_type`
- `language`
- `abstract`
- `subjects`
- `rights`
- `alternate_identifiers`
- `alternate_urls`

#### Modo `?metadata`

`GET /api/v1/arks/{ark}?metadata`

- lee Level-1 desde storage
- extrae `original_metadata.cid`
- descarga el documento Level-2
- responde los bytes originales con el media type indicado por Level-1

### Notas funcionales

- Es completamente read-only.
- No tiene DB propia.
- No tiene worker.
- Blockchain es la fuente canónica del target y del CID Level-1.

---

## 4. Store API

Base URL: `http://localhost:8003`

### Propósito

Almacenar y recuperar contenido raw por CID sin entender semántica dARK.

### Endpoints

| Método | Ruta | Auth | Body | Respuesta | Para qué sirve |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/v1/store` | No | bytes raw | `StoreResponse` | Guarda contenido y devuelve el CID |
| `GET` | `/v1/retrieve/{cid}` | No | No | bytes raw | Recupera contenido previamente guardado |
| `GET` | `/v1/status/{cid}` | No | No | `StatusResponse` | Consulta estado de pin/replicación del CID |
| `GET` | `/health` | No | No | `HealthResponse` | Verifica salud del backend de storage |

### Payloads y respuestas

#### `POST /v1/store`

Acepta cualquier body no vacío:

- XML
- JSON
- texto
- binario

Ejemplo:

```bash
curl -X POST http://localhost:8003/v1/store \
  -H "Content-Type: application/xml" \
  -d '<record><title>Demo</title></record>'
```

Respuesta:

```json
{
  "cid": "bafy...",
  "size": 42
}
```

#### `GET /v1/retrieve/{cid}`

- responde bytes raw
- usa `application/octet-stream` como media type de transporte

#### `GET /v1/status/{cid}`

Campos típicos:

- `cid`
- `pinned`
- `replicas`
- `status`

### Notas funcionales

- En este workspace usa `ipfs_cluster` como backend por defecto.
- No sabe qué es Level-1 o Level-2.
- Solo almacena bytes y expone CIDs.

---

## 5. Flujo entre APIs

### Flujo completo más importante

1. Admin API registra una authority y autoriza su NAAN.
2. Minter API reserva un ARK.
3. Minter API recibe `minimal_metadata` y `original_metadata`.
4. El worker del minter guarda Level-2 y luego Level-1.
5. El worker publica el CID Level-1 on-chain.
6. Resolver API usa blockchain + storage para servir:
   - redirect
   - `?info`
   - `?metadata`

### Reparto de responsabilidades

| Servicio | Dueño funcional |
| --- | --- |
| Admin API | Authorities, NAANs y fondos |
| Minter API | Ciclo de vida write-side del ARK |
| Resolver API | Read-side público del ARK |
| Store API | Storage raw por CID |

---

## 6. Observaciones prácticas

- En local, Admin y Minter están configurados en modo de desarrollo sin mTLS activo.
- El `DELETE` del minter no borra en blockchain; solo hace tombstone local.
- El resolver tiene un solo endpoint funcional y cambia de comportamiento según query params.
- El store API es deliberadamente genérico y no modela metadata dARK.
