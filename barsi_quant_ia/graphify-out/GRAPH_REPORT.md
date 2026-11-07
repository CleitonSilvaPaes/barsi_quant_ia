# Graph Report - barsi_quant_ia  (2026-11-07)

## Corpus Check
- 47 files · ~9,172 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 2 file(s) not represented in the graph (top: .example 1, (none) 1)

## Summary
- 344 nodes · 817 edges · 17 communities (11 shown, 6 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 121 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `65ea171d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Position
- models.py
- factory.py
- logging.py
- __main__.py
- SQLiteHistoryAdapter
- CircuitBreaker
- barsi-worker
- http.py
- 🏦 Sistema Previdenciário Barsi - Agente Quantitativo Resiliente
- GSpreadAdapter
- events.py
- cloudflare_worker.js
- install_tvbox.sh
- barsi-quant-ia

## God Nodes (most connected - your core abstractions)
1. `Position` - 47 edges
2. `AdapterFactory` - 28 edges
3. `InsightResult` - 25 edges
4. `HistoryPort` - 23 edges
5. `AIPort` - 19 edges
6. `MarketDataPort` - 19 edges
7. `SyncService` - 19 edges
8. `SheetsPort` - 17 edges
9. `SQLiteHistoryAdapter` - 16 edges
10. `MarketQuote` - 16 edges

## Surprising Connections (you probably didn't know these)
- `run()` --uses--> `AdapterFactory`  [INFERRED]
  barsi_quant_ia/src/barsi/__main__.py → barsi_quant_ia/src/barsi/adapters/factory.py
- `run()` --uses--> `SyncService`  [INFERRED]
  barsi_quant_ia/src/barsi/__main__.py → barsi_quant_ia/src/barsi/services/sync_service.py
- `extratos()` --uses--> `AdapterFactory`  [INFERRED]
  barsi_quant_ia/src/barsi/__main__.py → barsi_quant_ia/src/barsi/adapters/factory.py
- `extratos()` --uses--> `Settings`  [INFERRED]
  barsi_quant_ia/src/barsi/__main__.py → barsi_quant_ia/src/barsi/config.py
- `OllamaRemoteAdapter` --uses--> `AcaoBarsi`  [INFERRED]
  barsi_quant_ia/src/barsi/adapters/ai/ollama_adapter.py → barsi_quant_ia/src/barsi/domain/models.py

## Import Cycles
- None detected.

## Communities (17 total, 6 thin omitted)

### Community 0 - "Position"
Cohesion: 0.08
Nodes (15): OllamaRemoteAdapter, RuleEngineAdapter, R2MirrorHistoryAdapter, _load(), InsightResult, Position, RunSummary, AIPort (+7 more)

### Community 1 - "models.py"
Cohesion: 0.07
Nodes (11): AcaoBarsi, atualizar_posicao_quant(), calcular_margem_seguranca(), calcular_preco_teto(), determinar_acao_barsi(), gerar_insight_deterministico(), test_atualizar_posicao_quant(), test_calcular_margem_seguranca() (+3 more)

### Community 2 - "factory.py"
Cohesion: 0.11
Nodes (13): AdapterFactory, BrapiAdapter, CachedMarketAdapter, YFinanceAdapter, XLSXAdapter, get_sync_service(), Settings, MarketQuote (+5 more)

### Community 3 - "logging.py"
Cohesion: 0.10
Nodes (6): main(), run_remote(), fix_all_sheets(), migrar(), main(), setup_logging()

### Community 4 - "__main__.py"
Cohesion: 0.11
Nodes (9): api(), extratos(), run(), tvbox(), OFXParserService, ResumoExtratoMes, Transacao, test_ofx_estorno_e_cancelamento_fii() (+1 more)

### Community 5 - "SQLiteHistoryAdapter"
Cohesion: 0.10
Nodes (8): SQLiteHistoryAdapter, GoogleNewsRSSAdapter, StubNewsAdapter, NewsSummary, test_rss_adapter_with_respx(), _test(), test_sync_service_pipeline_end_to_end(), _test()

### Community 6 - "CircuitBreaker"
Cohesion: 0.13
Nodes (4): CircuitBreaker, CircuitBreakerOpenException, CircuitState, test_circuit_breaker_flow()

### Community 7 - "barsi-worker"
Cohesion: 0.11
Nodes (18): build, container_name, deploy, environment, healthcheck, ports, restart, volumes (+10 more)

### Community 8 - "http.py"
Cohesion: 0.13
Nodes (4): health(), metrics(), trigger_run(), verify_token()

### Community 9 - "🏦 Sistema Previdenciário Barsi - Agente Quantitativo Resiliente"
Cohesion: 0.14
Nodes (13): 1. Conecte na TV Box via SSH, 1. Criar o Túnel na TV Box, 2. Clone o repositório e execute o script idempotente, 2. Configurar o arquivo `~/.cloudflared/config.yml`, 3. Deploy do Worker Cron Trigger, 📑 ADR - Tabela de Decisões de Arquitetura, ☁️ Configuração Cloudflare (Tunnel + Worker Cron), 🏛️ Diagrama da Arquitetura Hexagonal (+5 more)

### Community 10 - "GSpreadAdapter"
Cohesion: 0.29
Nodes (3): GSpreadAdapter, _load(), _update()

## Knowledge Gaps
- **1 isolated node(s):** `barsi-quant-ia`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 135 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Position` connect `Position` to `models.py`, `GSpreadAdapter`, `factory.py`, `SQLiteHistoryAdapter`?**
  _High betweenness centrality (0.113) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `Position` (e.g. with `OllamaRemoteAdapter` and `RuleEngineAdapter`) actually correct?**
  _`Position` has 16 INFERRED edges - model-reasoned connections that need verification._
- **What connects `barsi-quant-ia` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Position` be split into smaller, more focused modules?**
  _Cohesion score 0.07629288274449565 - nodes in this community are weakly interconnected._
- **Why does `AdapterFactory` connect `factory.py` to `Position`, `__main__.py`, `SQLiteHistoryAdapter`, `http.py`, `GSpreadAdapter`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `AdapterFactory` (e.g. with `OllamaRemoteAdapter` and `RuleEngineAdapter`) actually correct?**
  _`AdapterFactory` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Should `models.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07184325108853411 - nodes in this community are weakly interconnected._