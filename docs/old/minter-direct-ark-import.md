# Importación directa de ARKs y auditoría de NAAN

Esta guía describe dos comandos operativos incluidos en
`dark-core-minter-api`:

- `python -m app.cli.check_naans`: extrae los NAAN de un CSV y, opcionalmente,
  comprueba que estén autorizados para una autoridad.
- `python -m app.cli.import_arks`: crea ARKs directamente en el contrato a
  partir de un CSV, sin metadatos ni paso por la API HTTP del Minter.

Ambos comandos están destinados a ejecuciones controladas por operadores en el
servidor de aplicaciones. No exponen una ruta HTTP nueva.

## 1. Disponibilidad en una instalación del deployer

Los comandos están disponibles en la rama actual `main` del Minter. Antes de
usar estos procedimientos en una instalación gestionada por el deployer,
verifique `*_MINTER_REPOSITORY_BRANCH=main` dentro de `.env` y reconstruya el
componente:

```bash
python3 install.py rebuild minter --pull
```

No use esta guía con una imagen de Minter anterior: los módulos
`app.cli.import_arks` y `app.cli.check_naans` no estarán presentes.

La imagen de producción copia el código de `app/`, pero no instala los
entrypoints de `pyproject.toml`. Por ello, dentro de Docker use la forma
`python -m app.cli...` que aparece en los ejemplos. En un entorno virtual con
el paquete Minter instalado, también se pueden usar los entrypoints
`dark-import-arks` y `dark-check-naans`.

Los ejemplos con `docker compose` se ejecutan desde el directorio del
componente instalado:

```bash
cd components/dark-core-minter-api
```

## 2. CSV de entrada

El archivo debe ser UTF-8 y tener una fila por registro. Los campos que se usan
son:

| Comando | Columnas obligatorias |
| --- | --- |
| Auditoría de NAAN | `darkidentifier` |
| Importación directa | `darkidentifier`, `itemurl` |

Las columnas OAI restantes (`oaiidentifier`, `datestamp`, `lastmodified`) se
permiten pero no participan en el proceso.

```csv
darkidentifier,oaiidentifier,datestamp,itemurl,lastmodified
ark:/41046/001300001kq89,oai:repositorio.example:123/1,2025-07-31 12:28:05,https://repositorio.example/handle/123/1,2025-09-27 00:18:26
```

Se aceptan las formas `ark:/NAAN/nombre` y `ark:NAAN/nombre`. Las URLs deben ser
absolutas y usar `http` o `https`.

## 3. Auditoría previa de NAAN

Primero liste los NAAN únicos sin conectarse a blockchain:

```bash
docker compose run --rm \
  -v /ruta/absoluta/registros.csv:/input/registros.csv:ro \
  minter-api python -m app.cli.check_naans /input/registros.csv
```

Para verificar que todos pertenecen a una autoridad registrada y activa, el
contenedor recibe su configuración blockchain de `.env.integration`:

```bash
docker compose run --rm \
  -v /ruta/absoluta/registros.csv:/input/registros.csv:ro \
  minter-api python -m app.cli.check_naans /input/registros.csv \
    --authority-id UUID-DE-LA-AUTORIDAD
```

Para producir solo los NAAN que faltan, aptos para revisión o automatización:

```bash
docker compose run --rm \
  -v /ruta/absoluta/registros.csv:/input/registros.csv:ro \
  minter-api python -m app.cli.check_naans /input/registros.csv \
    --authority-id UUID-DE-LA-AUTORIDAD \
    --only-missing \
    --format csv
```

El comando no crea autoridades ni autoriza NAAN. Devuelve código `1` si la
autoridad está inactiva, falta una autorización o hay identificadores inválidos.

## 4. Importación sin metadatos

La importación directa crea un ARK con el `itemurl` del CSV y un CID vacío. No
escribe metadatos en IPFS/Store API ni crea registros en PostgreSQL del Minter.
Es apropiada para migrar identificadores históricos que solo necesitan
resolución URL. Si después se incorporan metadatos, se gestionan mediante el
flujo normal del Minter.

Antes de ejecutar, cree un directorio local para los resultados; debe ser
escribible por el contenedor:

```bash
mkdir -p /ruta/absoluta/import-results
```

Ejecute primero el preflight, que no envía transacciones:

```bash
docker compose run --rm \
  -v /ruta/absoluta/registros.csv:/input/registros.csv:ro \
  -v /ruta/absoluta/import-results:/results \
  minter-api python -m app.cli.import_arks /input/registros.csv \
    --authority-id UUID-DE-LA-AUTORIDAD \
    --results /results/registros.import-results.csv
```

El preflight comprueba que la autoridad existe, está activa y está autorizada
para todos los NAAN de las filas válidas. Revise el CSV de resultados antes de
ejecutar las transacciones:

```bash
docker compose run --rm \
  -v /ruta/absoluta/registros.csv:/input/registros.csv:ro \
  -v /ruta/absoluta/import-results:/results \
  minter-api python -m app.cli.import_arks /input/registros.csv \
    --authority-id UUID-DE-LA-AUTORIDAD \
    --results /results/registros.import-results.csv \
    --execute
```

La ejecución es reanudable: el CSV de resultados se actualiza después de cada
fila. En una nueva ejecución, el comando omite los ARKs ya creados o verificados
con la misma URL y wallet de autoridad. También consulta la cadena para manejar
un ARK cuya transacción se hubiera confirmado antes de que se escribiera el
checkpoint.

## 5. Conflictos, errores y seguridad

- Un ARK existente con la misma URL y el mismo propietario se marca como
  `skipped_same_url`.
- Un ARK existente de otra wallet es `conflict_owner`; uno con otra URL es
  `conflict_url`. El comando nunca actualiza esos ARKs automáticamente.
- Las filas sin URL, con URL inválida, ARK inválido o duplicado se anotan como
  `invalid`. Las filas válidas restantes pueden continuar; la salida final será
  no cero para requerir revisión.
- La importación requiere las credenciales configuradas para el Minter, incluida
  la clave que permite recuperar la credencial de firma de la autoridad. No
  copie `DARK_ADMIN_PRIVATE_KEY` a la línea de comandos, CSV, logs o resultados.
- Ejecute la importación desde el host/control operativo autorizado, no desde un
  cliente externo ni a través de la API pública del Minter.

No existe `--update-existing` en esta versión. Resolver manualmente los
conflictos preserva el CID y la propiedad existentes.
