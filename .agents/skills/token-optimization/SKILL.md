---
name: token-optimization
description: "Protocolo de economia agressiva de tokens no ecossistema Barsi Quant IA utilizando Graphify (AST Knowledge Graph) e Repomix (Packed Context)."
---

# 🧠 Token Optimization — Graphify & Repomix Protocol

Este skill guia qualquer agente ou desenvolvedor a trabalhar no código consumindo o menor número possível de tokens de contexto, aumentando a velocidade de resposta e a precisão das modificações.

---

## 🗺️ 1. Graphify — Navegação Estrutural sem Ler Arquivos

O **Graphify** mantém um grafo de conhecimento AST do projeto em [graphify-out/graph.json](file:///c:/Users/cleiton%20paes/Documents/Projeto/Investimento/graphify-out/graph.json).

### Quando Usar:
- Descobrir onde uma função, classe ou rota está declarada.
- Descobrir quem chama uma função ou quais rotas utilizam um método.
- Traçar o fluxo de dados entre camadas (ex: API -> Service -> Repository).

### Comandos Rápidos:
```bash
# Consulta semântica com busca em largura (BFS)
python -m graphify query "Onde o CSRF é validado?"

# Traça o menor caminho entre dois componentes
python -m graphify path "csrf_protection_middleware" "verify_csrf_token"

# Explicação sintética de um nó
python -m graphify explain "BudgetRepository"

# Atualiza o grafo apenas com arquivos modificados recentemente
python -m graphify --update barsi_quant_ia
```

> [!TIP]
> **Economia de Tokens**: Consultar `graphify query` consome ~500 a 2.000 tokens e retorna exatamente as linhas e nós envolvidos, enquanto ler 5 arquivos inteiros de código consome mais de 50.000 tokens!

---

## 📦 2. Repomix — Empacotamento Cirúrgico de Contexto

O **Repomix** comprime os arquivos essenciais do repositório em um formato XML unificado e estruturado, ignorando ruídos (binários, imagens, `.venv`, `extratos/`, bancos de dados e segredos).

### Quando Usar:
- Quando um subagente ou tarefa de análise arquitetural profunda precisa ter visão ampla do repositório em um único bloco.
- Ao inicializar uma sessão fria de revisão de código.

### Configuração e Execução:
O arquivo [repomix.config.json](file:///c:/Users/cleiton%20paes/Documents/Projeto/Investimento/repomix.config.json) já define os filtros de exclusão seguros.
```bash
# Gera o arquivo repomix-output.xml compacto
npx repomix --config repomix.config.json
```

---

## 🎯 3. Boas Práticas Gerais de Economia
1. **Leitura Cirúrgica**: Nunca use `read_file` em arquivos inteiros de mais de 300 linhas quando você só precisa de uma função. Use `StartLine` e `EndLine`.
2. **Uso de Grep e Estatísticas**: Use `git grep -n "termo"` ou `Select-String` para localizar a linha antes de ler.
3. **Não Polling**: O Antigravity utiliza Reactive Wakeup. Nunca faça loops de polling aguardando processos.
