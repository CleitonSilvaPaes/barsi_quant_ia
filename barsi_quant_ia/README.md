# 🏦 Sistema Previdenciário Barsi Quant IA — Agente Resiliente

Sistema de investimento previdenciário e geração de renda passiva baseado no **Método Barsi (Setores B.E.S.T.)**, construído sob **Arquitetura Hexagonal (Ports & Adapters)** com **persistência canônica em SQLite (Path B)**, interface web interativa completa (**Web Suite 2.0**) e otimizado para execução autônoma em hardware **Edge / SBC (TV Box Amlogic S905W · ARM64)** integrado a **Cloudflare Tunnel/Workers**.

---

## 🏛️ Diretrizes Arquiteturais Centrais (Path B)

1. **Fonte Canônica da Verdade (SQLite)**: O banco de dados local `barsi.db` (ou `memory/barsi_memory.db`) via `BudgetRepository` é a **única fonte primária da verdade**. Todas as leituras e edições de carteira, transações OFX, categorias e usuários operam diretamente no SQLite com latência ultrabaixa e zero dependência de APIs externas de planilhas em runtime.
2. **Exportação Sob Demanda (Unidirecional)**:
   - **Google Sheets**: Sincronização disparada manualmente via interface web (`/api/export/sheets`) ou CLI (`python -m barsi export-sheets`).
   - **Excel (.xlsx)**: Exportação gerada dinamicamente sob demanda para download (`/api/export/xlsx` ou `python -m barsi export-xlsx`), garantindo arquivos limpos sem manter planilhas obsoletas no disco.
3. **Isolamento e Segurança**:
   - Autenticação e RBAC (Controle de Acesso Baseado em Perfis: `admin`, `investidor`, `leitor`).
   - Criptografia de senhas com PBKDF2 e tokens de sessão JWT assinados.
   - 100% dos testes rodam sobre bancos temporários (`tmp_path`) sem tocar na base de produção.

---

## 🖥️ Web Suite 2.0 — Recursos do Painel Web

A aplicação conta com uma interface moderna em HTML5 + CSS responsivo + Vanilla JS (sem overhead de frameworks pesados de frontend):

- 💼 **Carteira Inteligente (`/carteira`)**: Tabela inline-editable com cotas atuais, aportes sugeridos, preço atual em tempo real, Preço Teto Barsi, margem de segurança e status de convicção.
- 📈 **Analytics & Benchmarks (`/analytics`)**:
  - Gráfico dinâmico de evolução patrimonial com base em snapshots reais.
  - Benchmarks comparativos em tempo real: **Carteira vs IBOVESPA (`^BVSP`) vs CDI Acumulado 12M (via API oficial do Banco Central do Brasil - BCB)** com fallback offline resiliente.
  - Histórico mensal de dividendos e distribuição setorial (Bancos, Energia, Saneamento, Telecomunicações, Seguros e FIIs).
- 💡 **Central de Insights & RLHF (`/insights`)**: Recomendações determinísticas de aporte com motor quantitativo e feedback do investidor (*Concordo / Discordo*) sincronizado em lote.
- 📑 **Conciliação Orçamentária OFX (`/extratos`)**: Upload e leitura de extratos bancários OFX, deduplicação idempotente por `fitid`/hash, visualização de receitas/despesas e gerenciador de palavras-chave para autoclassificação.
- 🎯 **Simulador de Aportes (`/aportes`)**: Distribuição interativa de aportes mensais via slider com respeito estrito à margem de segurança e priorização de yield.
- 👥 **Gestão de Usuários e RBAC (`/admin/users`)**: Criação, troca de perfil e exclusão de contas com isolamento de privilégios.
- 💾 **Backup do SQLite (`/api/backup/download`)**: Download seguro e direto do banco de dados completo (exclusivo para perfil `admin`).
- 📸 **Snapshots Diários Automáticos (`/api/snapshot/today`)**: Executados automaticamente a cada término de `/api/run`, com proteção preventiva contra sobrescrita por carteiras vazias.

---

## 🏗️ Arquitetura Hexagonal & Diagrama de Componentes

```text
               +-------------------------------------------------------------+
               |                    Nuvem / Interfaces                      |
               |                                                             |
               |  [Cloudflare Tunnel]    [Web Suite 2.0]     [Telegram / WPP] |
               +-------+---------------------+---------------------+---------+
                       |                     |                     |
                       v                     v                     v
+----------------------+---------------------+---------------------+----------------------+
| ADAPTERS (INBOUND)   |                     |                     |                      |
|                      |  FastAPI Router     |  RBAC Middleware    |  CLI Typer           |
|                      |  /carteira /run ... |  (Session Tokens)   |  (barsi CLI)         |
+----------------------+---------------------+---------------------+----------------------+
| PORTS                |                     |                     |                      |
|                      |  HTTP Controller    |  HistoryPort        |  SheetsPort (Export) |
+----------------------+---------------------+---------------------+----------------------+
|                                                                                         |
| DOMÍNIO PURO & REGRAS B.E.S.T. (Sem I/O, 100% Testável)                                 |
|                                                                                         |
|       * Preço Teto Barsi: Teto = DivProj / 0.06                                         |
|       * Margem de Segurança: ((Teto - Preço) / Preço) * 100                             |
|       * Status Barsi: ACUMULAR (Margem >= 0) / AGUARDAR (Margem < 0)                    |
|       * Engine Determinístico de Regras & Quant AI (Score B.E.S.T.)                     |
|                                                                                         |
+----------------------+---------------------+---------------------+----------------------+
| PORTS                |                     |                     |                      |
|                      |  MarketDataPort     |  NewsPort           |  NotificationPort    |
+----------------------+---------------------+---------------------+----------------------+
| ADAPTERS (OUTBOUND)  |                     |                     |                      |
|  [CachedMarket]      |  [GoogleNewsRSS]    |  [Telegram / WPP]   |  [BudgetRepository]  |
|   ├── YFinance       |   └── XML Parser    |   └── CallMeBot     |   └── SQLite Canonical
|   └── Brapi (token)  |                     |                     |                      |
+----------------------+---------------------+---------------------+----------------------+
                       |                     |                     |
                       v                     v                     v
               +-------------------------------------------------------------+
               |                  Hardware Alvo (TV Box)                     |
               |   Amlogic S905W (ARM64) · RAM < 250MB RSS · Sem GPU          |
               +-------------------------------------------------------------+
```

---

## 🤖 Ecossistema de Subagentes Antigravity

Conforme documentado no arquivo [`AGENTS.md`](../AGENTS.md), o sistema conta com subagentes especializados:

1. `barsi-quant-analyst`:
   - Análise quantitativa, validação de Preço Teto, Margem de Segurança e sustentabilidade de dividendos.
   - Filtros de setores perenes B.E.S.T. e governança dos modelos em `barsi.domain.rules` e `barsi.domain.quant_ai`.
2. `barsi-budget-conciliator`:
   - Processamento idempotente de extratos OFX, categorização orçamentária via palavras-chave e cálculo da capacidade de aporte mensal via `BudgetProjectionService`.

---

## 🛠️ Comandos da CLI (`barsi`)

O sistema conta com interface de linha de comando completa:

```bash
# Executar ciclo quantitativo diário de análise e cotações
python -m barsi run

# Sincronizar código, banco SQLite e reiniciar serviço na TV Box
python -m barsi sync-tvbox

# Exportar dados canônicos do SQLite sob demanda para Google Sheets
python -m barsi export-sheets

# Exportar carteira sob demanda para planilha Excel (.xlsx)
python -m barsi export-xlsx

# Conciliar arquivos OFX da pasta extratos/
python -m barsi parse-ofx

# Gerenciar usuários e acessos
python -m barsi user-add admin_user senha123 --role admin
python -m barsi user-list
python -m barsi user-del antigo_user
```

---

## 🔒 Dados Sensíveis e Proteção Contra Vazamento (Zero PII)

Este projeto processa extratos financeiros e credenciais de infraestrutura. Diretrizes obrigatórias:

### ✅ O que PODE ser commitado
- Código-fonte (`.py`, `.html`, `.js`, `.sh`, `.css`)
- Documentação técnica e arquitetural
- Templates de ambiente (`.env.example`, `pyproject.toml`)
- Testes com mocks e dados sintéticos

### ❌ O que NUNCA deve ser commitado
- `.env` e chaves de API reais
- `credentials.json` (Google Cloud Service Account)
- Extratos bancários reais (`extratos/*.ofx`)
- Banco de dados de produção (`barsi.db`, `memory/*.db`)
- Planilhas `.xlsx` com dados patrimoniais reais
- Nomes de titulares de contas bancárias ou IPs internos privados

---

## 🧪 Testes Automatizados & Qualidade de Código

A suíte de testes utiliza `pytest` com asserções estritas, isolamento via `tmp_path`, mocks para chamadas de rede e validação de cobertura:

```bash
# Executar suíte completa de testes com gate de cobertura mínimo de 40%
pytest tests/ -v --cov=barsi --cov-fail-under=40
```

- **Total de Testes**: 91 testes automatizados (100% passando).
- **Cobertura de Código**: > 61% (superando o gate do CI).
- **Isolamento**: Testes de endpoints, repositórios e serviços rodam de forma independente e determinística.

---

## 🚀 Sincronização e Deploy na TV Box (ARM64)

Para atualizar o servidor em execução na TV Box doméstica (`usuario@barsi-tvbox` ou IP da rede):

```bash
python -m barsi sync-tvbox
```

O comando realiza automaticamente:
1. Sincronização dos extratos bancários `.ofx`;
2. Atualização dos códigos-fonte e templates HTML;
3. Sincronização do banco SQLite `barsi_memory.db`;
4. Reinicialização limpa do serviço `barsi-api.service` via SSH.

---

## 🔮 Roadmap de Evolução Estratégica (Top 5 Prioridades)

1. 🥇 **Rastreamento de Dividendos Reais Recebidos**: Tabela `dividends_received` a partir dos lançamentos de proventos do OFX, comparando DPA Projetado vs DPA Realizado com cálculo de Yield on Cost real.
2. 🥇 **Backtesting Histórico de Aportes**: Simulador de curva de bola de neve (*Snowball Chart*) baseado no histórico de cotações (`price_history`) e reinvestimento dos proventos.
3. 🥇 **Relatório Mensal Automatizado em PDF**: Geração de relatório consolidado de fechamento de mês para arquivo pessoal e prestação familiar.
4. 🥇 **Alertas Preventivos de Mudança de Tese**: Detecção via NLP/notícias de cortes de dividendos, payout abusivo (>100%) ou endividamento acelerado antes do mercado precificar.
5. 🥇 **Observabilidade & Métricas Prometheus (`/metrics`)**: Exposição de métricas operacionais para monitoramento de saúde do sistema e do daemon diário.
