# 🧪 Regras de Isolamento de Testes & Banco de Dados

1. **Intocabilidade do Banco de Produção**:
   - O arquivo `memory/barsi_memory.db` NUNCA deve ser aberto ou modificado durante os testes.
   - Qualquer teste que toque em `memory/barsi_memory.db` deve ser considerado falha crítica de segurança.

2. **Fixtures Automáticas**:
   - O arquivo [conftest.py](file:///c:/Users/cleiton%20paes/Documents/Projeto/Investimento/barsi_quant_ia/tests/conftest.py) gerencia a fixture `isolate_database` (autouse=True).
   - O caminho do banco é sempre redirecionado para `tmp_path / "test_isolated.db"`.

3. **Gate de Cobertura de Código**:
   - Todo teste deve passar pelo flag `--cov-fail-under=40`.
   - A cobertura do projeto deve permanecer sempre acima de 40% (atualmente em ~69.46%).
