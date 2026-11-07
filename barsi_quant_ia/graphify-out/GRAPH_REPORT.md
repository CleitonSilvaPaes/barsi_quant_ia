# Graph Report - barsi_quant_ia  (2026-11-07)

## Corpus Check
- 52 files · ~12,845 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 2 file(s) not represented in the graph (top: .example 1, (none) 1)

## Summary
- 399 nodes · 926 edges · 19 communities (11 shown, 8 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 129 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ffe04744`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Position
- models.py
- logging.py
- http.py
- factory.py
- CircuitBreaker
- barsi-worker
- 🏦 Sistema Previdenciário Barsi - Agente Quantitativo Resiliente
- GSpreadAdapter
- events.py
- cloudflare_worker.js
- install_tvbox.sh
- barsi-quant-ia
- AdapterFactory
- quant_ai.py

## God Nodes (most connected - your core abstractions)
1. `Position` - 49 edges
2. `AdapterFactory` - 26 edges
3. `InsightResult` - 25 edges
4. `HistoryPort` - 23 edges
5. `MarketDataPort` - 19 edges
6. `AIPort` - 19 edges
7. `SyncService` - 18 edges
8. `SQLiteHistoryAdapter` - 16 edges
9. `SheetsPort` - 16 edges
10. `MarketQuote` - 16 edges

## Surprising Connections (you probably didn't know these)
- `test_statistical_barsi_engine()` --calls--> `Position`  [INFERRED]
  barsi_quant_ia/tests/test_quant_ai.py → barsi_quant_ia/src/barsi/domain/models.py
- `AdapterFactory` --uses--> `R2MirrorHistoryAdapter`  [INFERRED]
  barsi_quant_ia/src/barsi/adapters/factory.py → barsi_quant_ia/src/barsi/adapters/history/r2_mirror_adapter.py
- `AdapterFactory` --uses--> `SQLiteHistoryAdapter`  [INFERRED]
  barsi_quant_ia/src/barsi/adapters/factory.py → barsi_quant_ia/src/barsi/adapters/history/sqlite_adapter.py
- `XLSXAdapter` --uses--> `Position`  [INFERRED]
  barsi_quant_ia/src/barsi/adapters/sheets/xlsx_adapter.py → barsi_quant_ia/src/barsi/domain/models.py
- `test_atualizar_posicao_quant()` --uses--> `Position`  [INFERRED]
  barsi_quant_ia/tests/test_domain_rules.py → barsi_quant_ia/src/barsi/domain/models.py

## Import Cycles
- None detected.

## Communities (19 total, 8 thin omitted)

### Community 0 - "Position"
Cohesion: 0.07
Nodes (21): OllamaRemoteAdapter, RuleEngineAdapter, R2MirrorHistoryAdapter, SQLiteHistoryAdapter, _load(), InsightResult, Position, RunSummary (+13 more)

### Community 1 - "models.py"
Cohesion: 0.07
Nodes (9): AcaoBarsi, atualizar_posicao_quant(), calcular_margem_seguranca(), calcular_preco_teto(), determinar_acao_barsi(), test_atualizar_posicao_quant(), test_calcular_margem_seguranca(), test_calcular_preco_teto() (+1 more)

### Community 3 - "logging.py"
Cohesion: 0.08
Nodes (6): main(), run_remote(), fix_all_sheets(), migrar(), main(), setup_logging()

### Community 4 - "http.py"
Cohesion: 0.08
Nodes (18): dashboard(), do_login(), do_logout(), EditItem, get_carteira(), health(), is_authenticated(), login_page() (+10 more)

### Community 5 - "factory.py"
Cohesion: 0.16
Nodes (6): GoogleNewsRSSAdapter, StubNewsAdapter, NewsSummary, NewsPort, test_rss_adapter_with_respx(), _test()

### Community 6 - "CircuitBreaker"
Cohesion: 0.07
Nodes (12): BrapiAdapter, CachedMarketAdapter, YFinanceAdapter, MarketQuote, CircuitBreaker, CircuitBreakerOpenException, CircuitState, MarketDataPort (+4 more)

### Community 7 - "barsi-worker"
Cohesion: 0.11
Nodes (18): build, container_name, deploy, environment, healthcheck, ports, restart, volumes (+10 more)

### Community 9 - "🏦 Sistema Previdenciário Barsi - Agente Quantitativo Resiliente"
Cohesion: 0.14
Nodes (13): 1. Conecte na TV Box via SSH, 1. Criar o Túnel na TV Box, 2. Clone o repositório e execute o script idempotente, 2. Configurar o arquivo `~/.cloudflared/config.yml`, 3. Deploy do Worker Cron Trigger, 📑 ADR - Tabela de Decisões de Arquitetura, ☁️ Configuração Cloudflare (Tunnel + Worker Cron), 🏛️ Diagrama da Arquitetura Hexagonal (+5 more)

### Community 10 - "GSpreadAdapter"
Cohesion: 0.21
Nodes (3): GSpreadAdapter, _load(), _update()

### Community 17 - "AdapterFactory"
Cohesion: 0.15
Nodes (9): AdapterFactory, XLSXAdapter, get_sync_service(), Settings, api(), extratos(), run(), _main() (+1 more)

### Community 18 - "quant_ai.py"
Cohesion: 0.09
Nodes (6): QLearningBarsiAgent, SentimentNLPNeuralNet, StatisticalBarsiEngine, test_nlp_neural_net_sentiment(), test_qlearning_agent_decision(), test_statistical_barsi_engine()

## Knowledge Gaps
- **1 isolated node(s):** `barsi-quant-ia`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 151 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Position` connect `Position` to `models.py`, `http.py`, `GSpreadAdapter`, `AdapterFactory`, `quant_ai.py`?**
  _High betweenness centrality (0.130) - this node is a cross-community bridge._
- **Are the 15 inferred relationships involving `Position` (e.g. with `OllamaRemoteAdapter` and `RuleEngineAdapter`) actually correct?**
  _`Position` has 15 INFERRED edges - model-reasoned connections that need verification._
- **What connects `barsi-quant-ia` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Position` be split into smaller, more focused modules?**
  _Cohesion score 0.06915113871635611 - nodes in this community are weakly interconnected._
- **Why does `AdapterFactory` connect `AdapterFactory` to `Position`, `GSpreadAdapter`, `factory.py`, `CircuitBreaker`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Are the 19 inferred relationships involving `AdapterFactory` (e.g. with `OllamaRemoteAdapter` and `RuleEngineAdapter`) actually correct?**
  _`AdapterFactory` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Should `models.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07111756168359942 - nodes in this community are weakly interconnected._