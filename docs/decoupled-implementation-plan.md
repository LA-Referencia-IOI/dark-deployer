# Plano de Implementação — Infraestrutura Desacoplada (Sandbox / Production)

> **Estado: histórico.** Este plan describe la transición que dio lugar a la
> implementación actual. No es una guía de ejecución; consulte
> [`deployer-operations.md`](deployer-operations.md) y
> [`decoupled-infrastructure.md`](decoupled-infrastructure.md).

> **Status: histórico / implementado.** Este documento preserva o plano que
> orientou a implementação original. Não deve ser usado como runbook atual.
> As instruções de storage também foram substituídas pelo cluster global em
> [IPFS Architecture](ipfs-architecture.md).
> Para operação vigente, consulte
> [deployer-operations.md](deployer-operations.md) e
> [decoupled-infrastructure.md](decoupled-infrastructure.md).

Referência de arquitetura: [decoupled-infrastructure.md](decoupled-infrastructure.md)

---

## Contexto

O `install.py` atual assume que **tudo roda no mesmo servidor**. Para suportar os perfis sandbox e production com blockchain e IPFS em servidores separados, é necessário:

1. Ensinar o instalador a pular tiers remotos.
2. Permitir que os endereços de contrato venham do `.env` (não só do `deployed_contracts.ini` local).
3. Corrigir os URLs de IPFS nos arquivos `.env.integration` dos serviços — devem ser aliases derivados da topologia (`dark-ipfs-<site>-storage-<n>`), não nomes legados fixos.
4. Atualizar `.env.example` com as novas variáveis.
5. Atualizar `stop.py` e `clean.py` para não tentarem parar containers de tiers remotos.

---

## Etapas

---

### Etapa 1 — Novas variáveis em `.env.example`

**Arquivo:** `.env.example`

Adicionar nos blocos SANDBOX e PRODUCTION:

```ini
## Decoupled infrastructure
# Set BLOCKCHAIN_HOST to skip local blockchain install (remote tier).
SANDBOX_BLOCKCHAIN_HOST=
SANDBOX_BLOCKCHAIN_EXTRA_NODES=

# Set IPFS_HOST to skip local IPFS install (remote tier).
SANDBOX_IPFS_HOST=
SANDBOX_IPFS_EXTRA_NODES=
SANDBOX_IPFS_API_URL=
SANDBOX_IPFS_CLUSTER_URL=

# Contract addresses — required when blockchain tier is remote.
# On local installs these are read from deployed_contracts.ini automatically.
SANDBOX_DARK_CONTRACT_ADDRESS=
SANDBOX_AUTHORITY_CONTRACT_ADDRESS=
```

Repetir o mesmo bloco com prefixo `PRODUCTION_`.

**Por que `DARK_CONTRACT_ADDRESS` e `AUTHORITY_CONTRACT_ADDRESS` precisam de prefixo:**
atualmente eles são variáveis globais no `.env`, escritas por `update_env_file()` após o deploy.
No modo desacoplado o blockchain roda em outro servidor — o operador copia os endereços
manualmente. Usar prefixo evita conflito com a instalação developer que ainda escreve globalmente.

---

### Etapa 2 — Guard logic em `install_profile()`

**Arquivo:** `install.py`  
**Função:** `install_profile()` — linha 2064

Substituir:

```python
install_blockchain(prefix=prefix, env=env)
...
install_dark_ipfs(prefix=prefix, env=env)
```

Por:

```python
blockchain_host = env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip()
ipfs_host       = env.get(f"{prefix}_IPFS_HOST", "").strip()

if blockchain_host:
    print(f"[INFO] Blockchain tier is remote ({blockchain_host}) — skipping local install.")
else:
    install_blockchain(prefix=prefix, env=env)

install_core_lib(prefix=prefix, env=env)
install_core_admin_api(prefix=prefix, env=env)

if ipfs_host:
    print(f"[INFO] IPFS tier is remote ({ipfs_host}) — skipping local install.")
else:
    install_dark_ipfs(prefix=prefix, env=env)
```

Nenhuma outra função `install_*` precisa mudar — o mecanismo de skip por URL vazia
já funciona para quem não configura serviços opcionais.

---

### Etapa 3 — Leitura de endereços de contrato quando blockchain é remoto

**Arquivo:** `install.py`  
**Funções afetadas:** `generate_admin_api_env_integration()` (linha 1225), `generate_resolver_api_env_integration()` (linha 1274), `generate_minter_env_integration()` (linha 1017), `generate_core_lib_env_integration()` (linha 980)

Todas essas funções chamam `read_deployed_contract_addresses()`, que lê
`components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini`.
No modo desacoplado esse arquivo não existe.

**Solução:** criar uma função helper `resolve_contract_addresses(prefix, env)`:

```python
def resolve_contract_addresses(prefix: str, env: dict) -> tuple[str, str]:
    """
    Returns (dark_address, authority_address).
    Prefers prefixed .env vars (set by operator for remote blockchain),
    falls back to deployed_contracts.ini for local installs.
    """
    dark_addr      = env.get(f"{prefix}_DARK_CONTRACT_ADDRESS", "").strip()
    authority_addr = env.get(f"{prefix}_AUTHORITY_CONTRACT_ADDRESS", "").strip()

    if dark_addr and authority_addr:
        return dark_addr, authority_addr

    ini_path = Path("components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini")
    return read_deployed_contract_addresses(ini_path)
```

Substituir todas as chamadas diretas a `read_deployed_contract_addresses(ini_path)` nas
quatro funções de geração de `.env.integration` por `resolve_contract_addresses(prefix, env)`.

> **Atenção:** `prefix` precisa ser passado como argumento extra nessas funções.
> Verificar a assinatura atual de cada uma e propagar o parâmetro desde `install_profile()`.

---

### Etapa 4 — Corrigir URLs de IPFS em `generate_store_api_env_integration()`

**Arquivo:** `install.py`  
**Função:** `generate_store_api_env_integration()` — linha 1191

**Problema crítico:** os valores hardcodados abaixo só funcionam quando store-api e IPFS estão na mesma Docker network:

```python
"IPFS_API_URL": "http://dark-ipfs-site-a-storage-1:5001",
"IPFS_CLUSTER_API_URL": "http://dark-ipfs-cluster-site-a-storage-1:9094",
"IPFS_CLUSTER_PROXY_API_URL": "http://dark-ipfs-cluster-site-a-storage-1:9095",
```

No modo desacoplado, o store-api está no servidor de aplicação e o IPFS em outro servidor —
os aliases são gerados pela topologia e publicados no ambiente de cada site.

**Solução:**

```python
ipfs_host = env.get(f"{prefix}_IPFS_HOST", "").strip()

if ipfs_host:
    ipfs_api_url         = env.get(f"{prefix}_IPFS_API_URL",     f"http://{ipfs_host}:5001").strip()
    ipfs_cluster_url     = env.get(f"{prefix}_IPFS_CLUSTER_URL", f"http://{ipfs_host}:9094").strip()
    ipfs_cluster_proxy   = ipfs_cluster_url.replace(":9094", ":9095")
else:
    # Local install: use Docker service names (same network as store-api)
    ipfs_api_url       = "http://dark-ipfs-site-a-storage-1:5001"
    ipfs_cluster_url   = "http://dark-ipfs-cluster-site-a-storage-1:9094"
    ipfs_cluster_proxy = "http://dark-ipfs-cluster-site-a-storage-1:9095"

integration_env = {
    ...
    "IPFS_API_URL":               ipfs_api_url,
    "IPFS_CLUSTER_API_URL":       ipfs_cluster_url,
    "IPFS_CLUSTER_PROXY_API_URL": ipfs_cluster_proxy,
    ...
}
```

> **Atenção:** `prefix` precisa ser passado para esta função. Atualmente a assinatura
> é `generate_store_api_env_integration(store_api_path, env)` — adicionar `prefix` como
> terceiro argumento e atualizar o caller em `install_dark_store_api()` (linha 1904).

---

### Etapa 5 — Atualizar `stop.py` e `clean.py`

**Arquivos:** `stop.py`, `clean.py`

Ambos os scripts listam diretórios locais. No modo desacoplado,
`components/blockchain/dark-*` e `components/blockchain/dark-ipfs` não existem
no servidor de aplicação — a linha `[SKIP] ... not found` já trata isso sem erro.

**Não é necessária alteração imediata** — o check `if target.exists()` em ambos os
scripts já protege contra diretórios ausentes. Porém, para melhorar a experiência,
pode-se carregar o `.env` e emitir uma mensagem mais clara:

```python
# Exemplo para stop.py
blockchain_host = env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip()
if blockchain_host:
    print(f"[INFO] Blockchain tier is remote ({blockchain_host}) — nothing to stop locally.")
```

Isso é **opcional** e pode ser feito em iteração futura.

---

### Etapa 6 — Atualizar `print_service_summary()`

**Arquivo:** `install.py`  
**Função:** `print_service_summary()` — linha ~380

A função já usa `env.get("RPC_URL", ...)` para o endpoint blockchain — correto.
Para IPFS, verifica `{prefix}_IPFS_REPOSITORY_URL` (linha 416) para decidir se
exibe o status. No modo desacoplado o repositório IPFS não está configurado localmente,
mas o status ainda deve ser exibido usando a URL remota.

**Ajuste:**

```python
ipfs_host = env.get(f"{prefix}_IPFS_HOST", "").strip()
ipfs_api_url     = env.get(f"{prefix}_IPFS_API_URL",     "http://localhost:5001").strip()
ipfs_cluster_url = env.get(f"{prefix}_IPFS_CLUSTER_URL", "http://localhost:9094").strip()

ipfs_configured = bool(get_url(env, f"{prefix}_IPFS_REPOSITORY_URL")) or bool(ipfs_host)
if ipfs_configured:
    print(f"- IPFS API:     {ipfs_api_url} [{probe_ipfs_api_status(ipfs_api_url)}]")
    print(f"- IPFS Cluster: {ipfs_cluster_url} [{probe_http_status(f'{ipfs_cluster_url}/id')}]")
```

---

## Ordem de execução

| # | Etapa | Impacto | Dependência |
|---|-------|---------|-------------|
| 1 | Novas variáveis `.env.example` | Baixo — apenas documentação | Nenhuma |
| 2 | Guard logic `install_profile()` | Médio — muda fluxo de instalação | Etapa 1 |
| 3 | `resolve_contract_addresses()` | Alto — serviços não iniciam sem endereços | Etapa 2 |
| 4 | Corrigir IPFS URLs no store-api | Alto — store-api não conecta ao IPFS remoto | Etapa 2 |
| 5 | `stop.py` / `clean.py` mensagens | Baixo — cosmético | Etapas 2-4 |
| 6 | `print_service_summary()` IPFS | Baixo — apenas exibição | Etapa 4 |

Etapas 3 e 4 podem ser implementadas em paralelo após a Etapa 2.

---

## O que NÃO muda

- Perfil `developer`: comportamento idêntico ao atual — nenhuma das novas variáveis é obrigatória.
- Funções `install_blockchain()` e `install_dark_ipfs()`: sem alteração interna.
- Repositórios dos serviços (`dark-core-admin-api`, etc.): sem alteração.
- Lógica de `deployed_contracts.ini` para instalações locais: mantida como fallback.
