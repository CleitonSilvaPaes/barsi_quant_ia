# Catálogo de Scripts — Barsi Quant IA

Organização em 3 camadas de portabilidade e infraestrutura:

```
scripts/
├── migrations/          # TIER 2 — Agnóstico (SQLite / Sheets)
├── deploy/
│   ├── local/           # TIER 2 — Setup no PC local (Windows / Linux / macOS)
│   ├── tvbox/           # TIER 3 — Específico para hardware TV Box ARM64
│   └── windows/         # TIER 3 — Utilitários desktop Windows (Ollama GPU)
├── cloudflare_worker.js # Serverless edge gateway
└── test_remote_api.py   # Teste funcional de login e API (aceita qualquer URL)
```
