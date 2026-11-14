# 🔐 Política de Segurança — Barsi Quant IA

Este documento descreve como reportar vulnerabilidades e como agir em caso de vazamento acidental de dados sensíveis no repositório.

---

## 1. Dados classificados como sensíveis

| Classe | Exemplos | Onde deve viver |
|---|---|---|
| **PII de terceiros** | Nome completo, CPF, número de conta, endereço | `.env` local, `extratos/`, `memory/*.db` — todos `.gitignore` |
| **Credenciais** | Senha SSH, token HMAC, API keys, `credentials.json` | `.env` local, `chmod 600` |
| **Infra interna** | IP da TV Box, hostname doméstico, MAC address | `.env` local, nunca em código |
| **Financeiro** | Saldos, carteira, histórico de compras | SQLite local + Google Sheets privado |

---

## 2. Vazamento de credenciais (procedimento)

Se você commitou acidentalmente um dos itens da seção 1:

### Passo 1 — Rotacione imediatamente
```bash
# Senha SSH da TV Box
ssh SEU_USUARIO@SEU_IP 'passwd'   # nova senha

# Variáveis do .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # novo token HMAC
```

### Passo 2 — Verifique o histórico do Git
```bash
# Encontra todos os commits que tiveram o arquivo/dado
git log --all -S "NOME_TITULAR" --oneline
git log --all -S "192.168.0." --oneline
# Se retornar algum commit, o dado está no histórico
```

### Passo 3 — Purge o histórico (caso tenha ido para o remoto)
```bash
# Instale git-filter-repo
pip install git-filter-repo

# Remove um arquivo específico do histórico inteiro
git filter-repo --path .env --path credentials.json --invert-paths

# Remove por padrão de texto em qualquer arquivo
git filter-repo --replace-text <(echo "NOME_COMPLETO_TITULAR==>TITULAR")
git filter-repo --replace-text <(echo "IP_PRIVADO_REAL==>192.0.2.1")
git filter-repo --replace-text <(echo "usuario_pessoal@==>user@")

# Force-push e avise todos os colaboradores
git push --force --all
```

> ⚠️ **Aviso:** `git filter-repo` reescreve o histórico. Todos os colaboradores precisam re-clonar. Se o repositório é público (GitHub), considere deletar e recriar para garantir que forks antigos não tenham cache.

### Passo 4 — Revogue credenciais antigas
1. Google Cloud Console → revogar Service Account `credentials.json`
2. Cloudflare → revogar tokens R2
3. SSH da TV Box → `~/.ssh/authorized_keys` (remover chave antiga, se houver)

### Passo 5 — Documente no CHANGELOG
```markdown
## [Unreleased] - Security
- **BREAKING:** Purgado histórico do Git para remover PII (ver SECURITY.md §2)
```

---

## 3. Reportar uma vulnerabilidade

Abra um GitHub Security Advisory privado (não uma issue pública) com:
1. Versão afetada
2. Passos para reproduzir
3. Impacto estimado (confidencialidade, integridade, disponibilidade)
4. Sugestão de fix (se tiver)

Resposta esperada em até 72h.

---

## 4. Como o projeto protege os dados por padrão

- `.env` está no `.gitignore` e tem `chmod 600` recomendado
- `.env.example` contém apenas placeholders vazios
- Logs estruturados não incluem `<MEMO>` completo nem nome de titular
- `memory/*.db` fica fora do Git
- `extratos/*.ofx` fica fora do Git
- `credentials.json` fica fora do Git
- Backup no R2 exige `BARSI_R2_ENABLED=true` + credenciais explícitas

---

## 5. Modelo de Autenticação, 2FA e Revogação de Sessões

### A. Política de 2FA para Administrador (Nudge vs. Enforcement)
- **Uso Pessoal em Edge Hardware (Tailscale / TV Box)**: O 2FA opera em regime de **soft nudge** (redirecionamento orientativo para `/settings#2fa` no login e banner visual persistente). Não há hard lockout de navegação para evitar interrupções catastróficas em caso de perda temporária de autenticador.
- **Ambiente Multi-Usuário / Exposição Pública**: Caso novos administradores sejam adicionados ou a aplicação seja exposta sem VPN, recomenda-se ativar middleware de bloqueio estrito em rotas de mutação financeira.

### B. Defesa em Profundidade na Desativação do 2FA
- A desativação de 2FA via Web (`/api/auth/2fa/disable`) exige **prova de posse** via código TOTP ou código de recuperação de emergência (backup code).
- O método `UserRepo.disable_totp` possui `bypass_verification=False` como padrão seguro; apenas rotinas administrativas de linha de comando com acesso root/SSH (`barsi user-disable-2fa`) podem informar explicitamente `bypass_verification=True`.

### C. Revogação Instantânea de Sessões Antigas (`session_version` + JTI)
- Trocas de senha (`/api/user/change-password`, `user-pass-reset`) ou desativação de segundo fator incrementam o campo `session_version` no banco de dados.
- Tokens emitidos em sessões concorrentes ou dispositivos secundários com versões antigas de sessão são rejeitados de imediato por `get_current_user`, garantindo encerramento forçado de acessos antigos (session invalidation).
- O logout explícito (`/logout`) revoga o identificador exclusivo `jti` na tabela `session_revocation`.

### D. Invalidação Imediata de Tokens em Exclusão de Usuário
- Tokens de usuários excluídos do banco de dados são rejeitados de imediato em `get_current_user` (verificação explícita `if not user_db: return None`), impedindo qualquer persistência de acesso após desativação ou exclusão de conta.
- Troca de senha (`/api/user/change-password`) e desativação de 2FA (`/api/auth/2fa/disable`) reemitem o token atualizado para o dispositivo que realizou a operação, mantendo a sessão atual ativa enquanto encerram imediatamente todas as demais sessões em outros dispositivos.
