# 🏛️ Regras Arquiteturais — Barsi Quant IA (Path B)

1. **SQLite como Fonte Canônica**:
   - Toda posição de carteira, histórico de proventos, categorização de despesas e credenciais residem exclusivamente no SQLite local.
   - Planilhas Google Sheets e arquivos Excel são exclusivamente canais secundários de exportação.

2. **Zero Magic Numbers**:
   - Valores monetários mensais e despesas recorrentes nunca devem ser hardcodados em código Python.
   - Use tabelas de parâmetros ou `recurring_expenses` com `valid_from` e `valid_to`.

3. **Paridade com a TV Box**:
   - Todo código desenvolvido localmente no Windows deve ser compatível com Linux ARM64 (Ubuntu / Armbian na TV Box).
   - Não use caminhos absolutos com separadores invertidos fixos (`\`). Sempre use `os.path.join()` ou caminhos POSIX.
