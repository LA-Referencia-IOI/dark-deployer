# Plan: editor terminal amigable para inventarios de despliegue

## Objetivo

Incorporar un editor interactivo de terminal para crear y modificar los inventarios
de inventario de despliegue de `deployment_v2`, cubriendo todas las opciones que
acepta el instalador. El editor debe ayudar a una persona a configurar un
despliegue local, developer HA o producción sin editar JSON manualmente.

La primera implementación está disponible. No cambia el formato, no ejecuta
despliegues por sí misma y no toca Docker. El editor abre las secciones JSON de
un inventario en una TUI, valida cada cambio con `load_inventory()` y solo
guarda después de una validación completa.

Estado de la primera entrega:

- `inventory-create` crea archivos desde las plantillas mantenidas.
- `inventory-edit` abre la TUI Textual en una terminal interactiva.
- El modelo `InventoryDocument` es independiente de Textual y concentra carga,
  validación, comparación, backup y guardado atómico.
- La edición se organiza por secciones completas. Formularios por campo,
  edición tabular de colecciones y ayuda derivada del schema siguen siendo la
  siguiente iteración de experiencia de usuario.

## Comandos propuestos

```text
venv/bin/python deploy.py inventory-create --template local-simple --output deployment-inventory.json
venv/bin/python deploy.py inventory-create --template local-ha --output deployment-inventory.json
venv/bin/python deploy.py inventory-create --template production-five-host --output deployment-inventory.json
venv/bin/python deploy.py inventory-edit --inventory deployment-inventory.json
```

El editor debe permitir seleccionar un archivo existente o crear uno desde una
plantilla. Antes de salir mostrará un resumen de cambios y pedirá confirmación.
La salida seguirá siendo un JSON válido que funciona con `validate`, `plan`,
`render` y `install`.

## Interfaz

Usar una interfaz de pantalla completa para terminal (Textual u otra biblioteca
equivalente ya aceptada por el proyecto), con navegación por secciones y ayuda
contextual. Debe funcionar también en una terminal pequeña y detectar cuando no
hay TTY para mostrar un mensaje claro y conservar los comandos no interactivos.

## Biblioteca y arquitectura recomendadas

### Decisión propuesta: Textual

Incorporar **Textual** como dependencia opcional del deployer, fijada en un
intervalo compatible para Python 3.10+ (el proyecto recomienda Python 3.12).
Textual ya integra Rich y ofrece los elementos que este caso necesita: campos de
entrada, selección, interruptores, áreas de texto, árbol de navegación, tablas
con cursor y eventos de teclado. Su `DataTable` permite actualizar tablas y
responder a la selección de filas, lo que encaja con máquinas, redes, nodos y
componentes del inventario. [Textual DataTable](https://github.com/Textualize/textual/blob/main/docs/widgets/data_table.md)

La dependencia debe instalarse de forma explícita, por ejemplo en un grupo
`requirements-tui.txt` o un extra de proyecto (`.[tui]`), para que los comandos
no interactivos y los despliegues remotos no carguen una dependencia innecesaria.
El comando `inventory-edit` detectará que Textual no está disponible e indicará
el comando exacto para instalar el extra. No debe intentar instalar paquetes por
su cuenta.

Arquitectura interna propuesta:

```text
deployment_v2/
  inventory.py             carga y validación semántica canónica
  schema.json              estructura, campos, ayudas, defaults y enums
  inventory_editor/
    app.py                 aplicación Textual y ciclo de vida
    screens.py             menú y pantallas por sección
    forms.py               widgets reutilizables de campos/validación
    tables.py              edición de colecciones y referencias
    document.py            modelo editable, diff, backup y guardado atómico
    adapters.py            adapta schema + inventario a formularios
```

La TUI trabaja sobre una copia en memoria del JSON. `inventory.py` sigue siendo
la única fuente de validación operacional y `schema.json` describe la
presentación. `document.py` es el único módulo autorizado a escribir el
inventario y crear backups.

### Alternativas consideradas

| Opción | Uso apropiado | Decisión |
| --- | --- | --- |
| **Textual** | Editor multipantalla, tablas, validación continua y vista de diferencias. | Elegida. Es la opción más directa para el alcance completo. |
| `prompt_toolkit` | TUI de pantalla completa muy personalizada. | Viable, pero requeriría implementar manualmente más layout, tablas y formularios. [Documentación](https://python-prompt-toolkit.readthedocs.io/en/3.0.26/pages/full_screen_apps.html) |
| `Questionary` | Asistente lineal de preguntas y confirmaciones. | Útil solo como alternativa liviana para un futuro `inventory-wizard`; no cubre bien edición de tablas ni navegación por secciones. [Documentación](https://questionary.readthedocs.io/en/stable/pages/quickstart.html) |
| `argparse` actual | Operaciones repetibles, automatización CI y scripts. | Se mantiene. No debe ser sustituido por la TUI. |

No usar una interfaz web, base de datos, Redis ni un servicio residente: el
inventario continúa siendo un archivo local versionable y el editor es una
herramienta puntual de operador.

### Dependencias concretas

Agregar únicamente:

```text
textual>=X,<Y
```

No declarar `rich` de forma independiente, salvo que se necesite también fuera
de la TUI; Textual lo aporta como dependencia. No se requiere `prompt_toolkit`,
`Questionary`, `Typer`, `Click`, ORM ni framework web. La CLI existente puede
seguir usando `argparse`, evitando una migración sin beneficio funcional.

Secciones editables:

1. **Deployment**: nombre, entorno, modo local/remoto, `chain_id` y versión de
   Besu.
2. **Defaults**: usuario/puerto SSH, referencia a clave, directorio remoto,
   estrategia de aplicación y opciones Docker.
3. **Networks**: redes Docker locales, red troncal de developer, CIDR, gateway,
   exposición y correspondencia con redes reales/VPN.
4. **Machines**: hosts, rol, dirección de gestión, IP VPN, usuario, puerto,
   clave y directorio de datos.
5. **Groups**: grupos `apps`, `blockchain-a`, `blockchain-b` y storage; miembros,
   proyecto Compose y orden de aplicación.
6. **Blockchain**: RPC, validadores, asignación de nodos, puertos P2P/RPC/WS,
   static nodes, artefactos y política de exposición.
7. **Storage**: nodos IPFS/Cluster, rutas de datos, endpoints locales y política
   `publish_after_replicas`/`target_replicas`.
8. **Components**: repositorio, rama, destino, grupo y versión de cada uno de
   los componentes requeridos.
9. **Minter y Store API**: shoulder, tamaños de página, concurrencia, límites,
   tiempos y URLs derivadas; mostrar valores heredados de la política cuando no
   se editen explícitamente.
10. **Secrets**: únicamente rutas o referencias a secretos. Nunca mostrar ni
    guardar contenidos en el inventario.
11. **Exposure**: endpoints internos, endpoints VPN y reglas de acceso.
12. **Review**: validación completa, diferencias respecto al archivo original,
    advertencias y resumen por host.

Para listas (hosts, nodos y componentes) usar tablas editables con agregar,
duplicar, eliminar y reordenar. Las opciones enumeradas se deben presentar como
selectores; booleanos como interruptores; números con límites y unidades
visibles.

## Fuente de verdad y validación

- Ampliar `deployment_v2/schema.json` para describir la estructura completa,
  tipos, enums, valores por defecto, títulos y descripciones que consumirá la
  interfaz.
- Mantener las reglas de coherencia operacional en `deployment_v2/inventory.py`
  (`load_inventory()`); el editor debe invocarlas después de cada sección y antes
  de guardar, sin duplicar reglas en la UI.
- Marcar referencias heredadas y valores derivados como solo lectura. Permitir
  editar únicamente su origen.
- Impedir eliminar una red, máquina, nodo o componente que tenga referencias.
- Validar roles, asignaciones Besu, puertos sin duplicados, CIDR, ramas, secretos,
  política de réplicas y componentes requeridos.
- Mostrar errores junto al campo y una lista final de advertencias; no permitir
  guardar si la validación tiene errores.

## Guardado seguro

El editor no debe iniciar Docker, clonar repositorios ni alterar hosts. Guardará
de forma atómica mediante archivo temporal y `rename`, conservará una copia de
seguridad con formato `archivo.json.bak-YYYYMMDD-HHMMSS` y solo escribirá tras
confirmación explícita. Un cancelado no debe modificar el archivo original.

## Plantillas

Implementar plantillas completas y válidas para:

- `local-simple`: todos los servicios en una máquina y redes locales mínimas.
- `local-ha`: simulación de apps, blockchain-a, blockchain-b y dos storage en
  una máquina, con red troncal compartida.
- `production-five-host`: apps, dos hosts blockchain y dos hosts storage, con
  IPs/SSH parametrizados y sin secretos.

Las plantillas deben pasar `validate` y servir como base para `plan` y `render`.

## Integración con la CLI y documentación

- Registrar `inventory-create` e `inventory-edit` en `deploy.py` y en la ayuda.
- Reutilizar el cargador y el renderer existentes; el editor no debe crear un
  segundo formato de inventario.
- Documentar flujo interactivo, campos obligatorios, referencias de secretos,
  copias de seguridad y equivalentes no interactivos en el manual operativo.
- Mantener mensajes de la herramienta en inglés, con explicaciones de uso en la
  documentación del repositorio.

## Pruebas de aceptación

- Cada propiedad aceptada por `load_inventory()` aparece en alguna sección del
  editor o queda explícitamente derivada/solo lectura.
- Las tres plantillas validan y generan planes/renderizados reproducibles.
- Se rechazan referencias inexistentes, roles duplicados, CIDR o puertos
  inválidos, componentes faltantes y réplicas fuera de rango.
- Editar, guardar y volver a cargar conserva todos los valores y pasa
  `validate`, `plan` y `render`.
- Cancelar no modifica el archivo; un guardado crea backup y es atómico.
- Se cubren terminales pequeñas, ausencia de TTY, navegación por teclado y
  campos sensibles sin filtración de secretos.
- Pruebas de regresión confirman que `apply`, `push`, `verify` y `install` siguen
  funcionando con inventarios producidos por el editor.

## Límites y decisiones

- No se modifica código de aplicación ni se controla Docker desde la UI.
- No se conserva compatibilidad con formatos previos a `deployment_v2`.
- El inventario contiene topología y referencias; claves privadas, wallets y
  artefactos sensibles permanecen fuera de Git y fuera de la pantalla.
- El editor debe reducir duplicación: toda configuración compartida se edita en
  un único origen y los valores derivados se recalculan automáticamente.
