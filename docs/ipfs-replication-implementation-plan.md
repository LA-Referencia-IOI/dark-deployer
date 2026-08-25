# Plano de implementação — persistência IPFS com um pin e reconciliação

**Status:** implementação funcional concluída e validada localmente em 25/08/2026.
**Referência de comportamento:** [Implementação de persistência e reconciliação IPFS](ipfs-replication-implementation.md).
**Escopo:** `dark-store-api`, `dark-core-minter-api`, `dark-core-lib`, este
deployer e o notebook E2E. Não altera a topologia: cada sede continua com dois
peers Kubo + IPFS Cluster.

## Estado da execução

| Entrega | Estado |
| --- | --- |
| Confirmação da escrita após o primeiro `PINNED` | implementada e coberta por teste unitário |
| Contagem de réplicas e política automática de purge | implementada e coberta por teste unitário |
| Retenção, reconciliação, reparo e purge no Minter | implementados e cobertos por teste unitário |
| Pools de endpoints, cooldown e fechamento de clientes | implementados e cobertos por teste unitário |
| Configuração compatível com developer, sandbox e production | implementada e coberta pelos testes do deployer |
| Registro E2E simples no notebook | implementado e executado com sucesso no ambiente local |
| Percentis de desempenho e validação multisede | pendentes para o rollout operacional |

## Resultado esperado

O caminho de escrita confirma sucesso depois que um peer informa `PINNED`.
O Minter só remove os payloads L1 e L2 mantidos no PostgreSQL quando ambos os
CIDs satisfazem a política de purge derivada da topologia:

| Topologia | Critério de purge, por CID |
| --- | --- |
| developer, um peer | uma cópia local `PINNED` |
| uma sede, dois peers | duas cópias locais `PINNED` |
| duas ou mais sedes | duas cópias locais e uma remota `PINNED` |

Durante a janela entre o primeiro pin e esse critério, um ARK pode ser
publicado, mas seus dois payloads permanecem recuperáveis no PostgreSQL.

## Decisões e limites

- Por compatibilidade, `strict_single_site: false` em topologias antigas é
  aceito e ignorado. `strict_single_site: true` deve falhar, pois sua garantia
  não pode ser preservada pela confirmação de um pin. Arquivos novos omitem o
  campo.
- A política é calculada no Store API usando a topologia e
  `IPFS_CLUSTER_LOCAL_SITE_ID`; não é persistida por ARK.
- L1 e L2 são verificados e purgados juntos, porém suas réplicas são contadas
  separadamente.
- Apenas o estado `PINNED` conta como cópia. `PINNING`, `PIN_ERROR` e peers
  visíveis não contam.
- Este plano requer banco novo ou uma migração/backfill aprovada antes do
  rollout; não habilitar em uma base existente sem essa decisão.

## Fases de implementação

### 1. Contrato do Store API

No `dark-store-api`:

1. Aceitar pools de Kubo, Cluster REST e Cluster Proxy, o mapa peer→site e o
   site local gerados pelo deployer.
2. Manter clientes HTTP reutilizáveis; aplicar round-robin e cooldown de 30 s
   após timeout, recusa, `429` ou `5xx`.
3. Em `POST /v1/store`, criar/adicionar o conteúdo e fazer polling progressivo
   até observar o primeiro `PINNED`. Retornar `503` ao expirar o timeout.
4. Implementar `GET /v1/status/{cid}` com `total_replicas`,
   `local_replicas`, `remote_replicas`, distribuição por sede,
   `purge_target_met` e `checked_at`.
5. Expor `/health/write` somente quando houver ao menos um caminho local de
   escrita e um peer Cluster conhecido.

**Aceite:** testes unitários cobrem cálculo de política, estados de pin,
round-robin, cooldown/reentrada e timeout sem `PINNED`.

### 2. Persistência e Metadata Worker

No `dark-core-minter-api`:

1. Criar migração para `level1_replica_count`, `level2_replica_count`,
   `replication_checked_at` e `replication_last_error` em `ark_metadata`.
2. Preservar a ordem L2 → L1 dentro de cada ARK e processar ARKs independentes
   com `METADATA_WORKER_CONCURRENCY` (padrão 4).
3. Depois do primeiro pin de cada CID, registrar os CIDs, manter ambos os
   payloads e liberar o Chain Worker.
4. Executar reconciliação quando não houver trabalho novo e, sob carga
   contínua, no máximo a cada cinco minutos; limitar cada lote a 50 ARKs.
5. Atualizar contagens; se um CID tiver zero cópias, reenviar o payload retido
   e rejeitar o resultado se o CID divergir. Purgar apenas após uma releitura
   bloqueada confirmar que L1 e L2 atingiram o alvo.
6. Expor resumo em `/api/v1/worker/status` e detalhe em
   `/api/v1/worker/replication`.

**Aceite:** testes de integração cobrem um pin inicial, retenção, purge apenas
quando os dois CIDs atingem o alvo, reparo com CID idêntico e retenção em erro.

### 3. Biblioteca e ciclo de vida

No `dark-core-lib` e consumidores:

1. Reutilizar os clientes HTTP para chamadas Store API.
2. Garantir `close()` no desligamento de Minter, Resolver e Store API.
3. Medir p50/p95/p99 de armazenamento L1/L2, primeiro pin, alvo de purge,
   reconciliação e throughput do worker.

**Aceite:** teste de shutdown não deixa sessões HTTP abertas; métricas permitem
separar espera de IPFS de processamento do worker.

### 4. Integração do deployer

Neste repositório:

1. Manter a geração de `IPFS_CLUSTER_LOCAL_SITE_ID`, pools de endpoints e o
   mapa peer→site, sem reintroduzir mínimos manuais.
2. Confirmar que `storage audit`/`storage reconcile` continuam reaplicando a
   política do Cluster sem apagar pins.
3. Atualizar as branches correspondentes em `.env` somente depois que os testes
   dos quatro componentes acima passarem.

**Aceite:** `python3 -m unittest discover -s tests -v` passa e uma instalação
renderizada contém apenas a configuração nova.

## Teste E2E implementado no notebook

Atualizar a seção 9 de
[`notebooks/dark_e2e_authority_to_resolver.ipynb`](../notebooks/dark_e2e_authority_to_resolver.ipynb),
**“Registro com persistência IPFS”**. É um teste normal de registro: não
interrompe serviços, não controla peers e não exige permissões operacionais
adicionais.

### Cenário

1. Criar uma autoridade e um ARK de teste com identificador único, reutilizando
   os helpers existentes do notebook.
2. Enviar Level 1 e o payload original Level 2 para o fluxo de metadata.
3. Aguardar a publicação do ARK e registrar `level1_cid` e `level2_cid`.
4. Consultar `GET /v1/status/{cid}` para ambos os CIDs e confirmar que cada um
   possui ao menos uma réplica `PINNED`.
5. Resolver o ARK normalmente, com `?info` e `?metadata`, e comparar as
   respostas com os payloads enviados.
6. Exibir, sem segredos, os CIDs, contagens de réplicas, `purge_target_met` e
   os tempos de publicação e da primeira confirmação de pin.

### Critérios de aprovação do notebook

- O ARK é publicado no blockchain.
- Os CIDs L1 e L2 são retornados e cada um possui pelo menos um peer `PINNED`.
- Resolver retorna corretamente a projeção L1 e o conteúdo L2.
- O notebook apresenta o estado de replicação sem imprimir chaves, tokens ou
  payloads sensíveis.

## Rollout e rollback

1. Executar migrações e testes unitários/integrados em sandbox com dois peers.
2. Rodar o notebook completo, incluindo a seção de registro IPFS, em sandbox.
3. Registrar as branches aprovadas em `.env`, renderizar o bundle e repetir o
   teste em uma janela controlada de produção.
4. Monitorar ARKs com payload retido, idade máxima de réplica, erros de reparo,
   espaço PostgreSQL e tempo até o alvo de purge.

Se houver falha, pausar novas promoções no Minter e manter os payloads; não
executar purge manual. O rollback só é seguro se a migração e os consumidores
anteriores suportarem as colunas novas, ou se houver migração reversa testada.

## Última validação local

Em 25/08/2026, o notebook completo registrou e publicou um ARK, confirmou L1
e L2 com uma réplica local `PINNED`, obteve `purge_target_met=true`, leu ambos
os conteúdos diretamente do Kubo e validou as respostas `?info` e `?metadata`
do Resolver. O tempo para observar os dois primeiros pins foi de 0,06 segundo.
