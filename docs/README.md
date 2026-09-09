# Documentación del despliegue dARK

Este índice distingue la documentación operativa vigente de los análisis y
propuestas históricas. La documentación de los componentes se revisará en una
fase posterior; este índice cubre primero el repositorio padre y su instalador.

## Referencias canónicas

| Tema | Documento vigente |
| --- | --- |
| Entrada general y comandos | [`README.md`](../README.md) |
| Arquitectura completa | [`dark-technical-reference.md`](dark-technical-reference.md) y [`DARK_2.0_ARCHITECTURE.md`](../DARK_2.0_ARCHITECTURE.md) |
| APIs y contratos | [`DARK_2.0_API_REFERENCE.md`](../DARK_2.0_API_REFERENCE.md) |
| Operación del instalador | [`deployer-operations.md`](deployer-operations.md) |
| Análisis del instalador local/remoto y unificación propuesta | [`installer-local-remote-unification-analysis.md`](installer-local-remote-unification-analysis.md) |
| Producción y bundles por host | [`production-single-site-four-server-installation.md`](production-single-site-four-server-installation.md) |
| Topología física y redes | [`production-single-site-four-server-installation.md`](production-single-site-four-server-installation.md) y [`dark-technical-reference.md`](dark-technical-reference.md) |
| IPFS, Cluster y Store API | [`ipfs-architecture.md`](ipfs-architecture.md) y [`ipfs-concepts-and-dark-store-api.md`](ipfs-concepts-and-dark-store-api.md) |
| Ciclo Metadata → Replication → Chain | [`worker-cycle-evidence.md`](worker-cycle-evidence.md) |
| Control de latencia y programación | [`publication-latency-control.md`](publication-latency-control.md) |
| Importación directa y NAAN | [`minter-direct-ark-import.md`](minter-direct-ark-import.md) |
| Política de shoulders | [`minter-shoulder-policy.md`](minter-shoulder-policy.md) |

Cuando dos documentos canónicos parezcan diferir, el código de `main`,
`deployment-topology.example.json` y `.env.example` tienen precedencia sobre
el texto. Los valores generados (`.env.integration`, runtime JSON y bundles)
son derivados y no fuentes de configuración.

## Estado actual que debe reflejar la documentación

- `dark-deployer` contiene el runtime blockchain y la orquestación Compose.
- `deployment-topology.json` es la única fuente de infraestructura e inventario
  del despliegue; no existen archivos auxiliares de inventario o de storage.
- La instalación productiva se divide en `apps`, `blockchain-a`,
  `blockchain-b` y dos `storage-node`.
- El minter ejecuta API, Metadata Worker, Replication Worker y Chain Worker.
  No existe un Recovery Worker automático.
- La publicación requiere `publish_after_replicas`; la durabilidad posterior
  intenta alcanzar `target_replicas`, que puede ser mayor que dos.
- Las esperas `queued`, `pinning` e `initial_visibility` son estados normales
  de Cluster, no fallos permanentes.

## Documentos históricos o de trabajo

Plan de implementación pendiente: [despliegue unificado v2](unified-deployment-v2-implementation-plan.md).
El [mapa de variables v2](deployment-v2-variable-map.md) registra la migración
desde la configuración heredada hacia el inventario único.
Define el inventario v2 y la ejecución local/SSH propuesta; aún no es una guía
operativa de la versión instalada.

Los siguientes documentos conservan contexto útil, pero no deben usarse como
instrucciones actuales sin contrastarlos con las referencias canónicas:

- `implementation-history.md`: historial consolidado.
- `chain-throughput-analysis.md`, `throughput-diagnosis-live-2026-09.md` y
  `diagnostico-full-reconciliacion-2026-09.md`: diagnósticos fechados.
- `worker-workflow-simplification-proposal.md`,
  `worker-workflow-simplification-implementation.md` y
  `worker-workflow-simplification-verification.md`: evolución del workflow.
- `worker-queues-and-ipfs-recovery-follow-up.md`,
  `ipfs-metadata-worker-throughput-proposal.md` y
  `modular-installation-redesign-proposal.md`: propuestas o análisis de
  decisiones ya incorporadas parcialmente.
- `decoupled-infrastructure.md`, `decoupled-implementation-plan.md` y
  `network-topology-analysis.md`:
  análisis de transición; contienen referencias que deben actualizarse antes
  de considerarse guías operativas.

La documentación histórica no debe enlazarse desde los pasos de instalación
como si describiera el estado actual. Cuando una decisión histórica sea útil,
debe resumirse en `implementation-history.md` y enlazarse desde allí.

## Reorganización pendiente

La siguiente fase revisará los README y arquitecturas dentro de cada
componente. En particular, se eliminarán referencias operativas a `dark-env`,
se alinearán los ejemplos con `deployment-topology.json` y se corregirán los
diagramas que todavía muestran Compose o redes anteriores.
