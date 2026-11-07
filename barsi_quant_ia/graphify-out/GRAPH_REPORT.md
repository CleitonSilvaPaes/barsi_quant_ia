# Graph Report - barsi_quant_ia  (2026-11-07)

## Corpus Check
- 50 files · ~11,139 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 2 file(s) not represented in the graph (top: .example 1, (none) 1)

## Summary
- 379 nodes · 872 edges · 20 communities (13 shown, 7 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 126 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c70b435a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Position
- models.py
- MarketDataPort
- logging.py
- __main__.py
- factory.py
- CircuitBreaker
- barsi-worker
- http.py
- 🏦 Sistema Previdenciário Barsi - Agente Quantitativo Resiliente
- GSpreadAdapter
- events.py
- cloudflare_worker.js
- install_tvbox.sh
- barsi-quant-ia
- logging.py
- quant_ai.py

## God Nodes (most connected - your core abstractions)
1. `Position` - 49 edges
2. `AdapterFactory` - 28 edges
3. `InsightResult` - 25 edges
4. `HistoryPort` - 23 edges
5. `AIPort` - 19 edges
6. `SyncService` - 19 edges
7. `MarketDataPort` - 19 edges
8. `SheetsPort` - 16 edges
9. `MarketQuote` - 16 edges
10. `SQLiteHistoryAdapter` - 16 edges

## Surprising Connections (you probably didn't know these)
- `AdapterFactory` --uses--> `GSpreadAdapter`  [INFERRED]
  barsi_quant_ia/src/barsi/adapters/factory.py → barsi_quant_ia/src/barsi/adapters/sheets/gspread_adapter.py
- `extratos()` --uses--> `OFXParserService`  [INFERRED]
  barsi_quant_ia/src/barsi/__main__.py → barsi_quant_ia/src/barsi/services/ofx_service.py
- `test_statistical_barsi_engine()` --calls--> `Position`  [INFERRED]
  barsi_quant_ia/tests/test_quant_ai.py → barsi_quant_ia/src/barsi/domain/models.py
- `OllamaRemoteAdapter` --uses--> `AcaoBarsi`  [INFERRED]
  barsi_quant_ia/src/barsi/adapters/ai/ollama_adapter.py → barsi_quant_ia/src/barsi/domain/models.py
- `OllamaRemoteAdapter` --uses--> `InsightResult`  [INFERRED]
  barsi_quant_ia/src/barsi/adapters/ai/ollama_adapter.py → barsi_quant_ia/src/barsi/domain/models.py

## Import Cycles
- None detected.

## Communities (20 total, 7 thin omitted)

### Community 0 - "Position"
Cohesion: 0.06
Nodes (15): R2MirrorHistoryAdapter, SQLiteHistoryAdapter, XLSXAdapter, _load(), InsightResult, Position, RunSummary, gerar_insight_quant_avancado() (+7 more)

### Community 1 - "models.py"
Cohesion: 0.12
Nodes (9): AcaoBarsi, atualizar_posicao_quant(), calcular_margem_seguranca(), calcular_preco_teto(), determinar_acao_barsi(), test_atualizar_posicao_quant(), test_calcular_margem_seguranca(), test_calcular_preco_teto() (+1 more)

### Community 2 - "MarketDataPort"
Cohesion: 0.11
Nodes (9): CachedMarketAdapter, YFinanceAdapter, MarketQuote, MarketDataPort, MockMarketAdapter, MockSheetsAdapter, test_protocol_conformance(), test_sync_service_pipeline_end_to_end() (+1 more)

### Community 3 - "logging.py"
Cohesion: 0.13
Nodes (3): main(), run_remote(), main()

### Community 4 - "__main__.py"
Cohesion: 0.19
Nodes (6): OFXParserService, ResumoExtratoMes, Transacao, test_ofx_estorno_e_cancelamento_fii(), test_ofx_idempotencia_duplicacao(), test_ofx_parser_transactions()

### Community 5 - "factory.py"
Cohesion: 0.10
Nodes (13): OllamaRemoteAdapter, RuleEngineAdapter, AdapterFactory, GoogleNewsRSSAdapter, StubNewsAdapter, get_sync_service(), Settings, NewsSummary (+5 more)

### Community 6 - "CircuitBreaker"
Cohesion: 0.10
Nodes (7): BrapiAdapter, CircuitBreaker, CircuitBreakerOpenException, CircuitState, test_brapi_adapter_with_respx(), _test(), test_circuit_breaker_flow()

### Community 7 - "barsi-worker"
Cohesion: 0.11
Nodes (18): build, container_name, deploy, environment, healthcheck, ports, restart, volumes (+10 more)

### Community 8 - "http.py"
Cohesion: 0.18
Nodes (4): health(), metrics(), trigger_run(), verify_token()

### Community 9 - "🏦 Sistema Previdenciário Barsi - Agente Quantitativo Resiliente"
Cohesion: 0.14
Nodes (13): 1. Conecte na TV Box via SSH, 1. Criar o Túnel na TV Box, 2. Clone o repositório e execute o script idempotente, 2. Configurar o arquivo `~/.cloudflared/config.yml`, 3. Deploy do Worker Cron Trigger, 📑 ADR - Tabela de Decisões de Arquitetura, ☁️ Configuração Cloudflare (Tunnel + Worker Cron), 🏛️ Diagrama da Arquitetura Hexagonal (+5 more)

### Community 10 - "GSpreadAdapter"
Cohesion: 0.21
Nodes (3): GSpreadAdapter, _load(), _update()

### Community 17 - "logging.py"
Cohesion: 0.09
Nodes (7): fix_all_sheets(), migrar(), setup_logging(), api(), extratos(), run(), tvbox()

### Community 18 - "quant_ai.py"
Cohesion: 0.12
Nodes (6): QLearningBarsiAgent, SentimentNLPNeuralNet, StatisticalBarsiEngine, test_nlp_neural_net_sentiment(), test_qlearning_agent_decision(), test_statistical_barsi_engine()

## Knowledge Gaps
- **1 isolated node(s):** `barsi-quant-ia`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 149 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Position` connect `Position` to `models.py`, `MarketDataPort`, `factory.py`, `GSpreadAdapter`, `quant_ai.py`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Are the 15 inferred relationships involving `Position` (e.g. with `OllamaRemoteAdapter` and `RuleEngineAdapter`) actually correct?**
  _`Position` has 15 INFERRED edges - model-reasoned connections that need verification._
- **What connects `barsi-quant-ia` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Position` be split into smaller, more focused modules?**
  _Cohesion score 0.06277665995975855 - nodes in this community are weakly interconnected._
- **Why does `AdapterFactory` connect `factory.py` to `Position`, `MarketDataPort`, `CircuitBreaker`, `http.py`, `GSpreadAdapter`, `logging.py`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `AdapterFactory` (e.g. with `OllamaRemoteAdapter` and `RuleEngineAdapter`) actually correct?**
  _`AdapterFactory` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Should `models.py` be split into smaller, more focused modules?**
  _Cohesion score 0.1164021164021164 - nodes in this community are weakly interconnected._