# 🔒 Regras de Segurança — Barsi Quant IA

1. **Proteção CSRF Mandatória**:
   - Toda rota mutante (`POST`, `PUT`, `DELETE`, `PATCH`) em sessão autenticada por cookie DEVE exigir o token CSRF (`X-CSRF-Token`).
   - Requisições sem token legítimo devem receber `HTTP 403 Forbidden`.

2. **Sanitização XSS em Templates**:
   - NUNCA injete variáveis de usuário ou de APIs externas diretamente em `innerHTML` sem passar pelo helper `esc()`.
   - Exemplo seguro: `<td><strong>${esc(p.ticker)}</strong></td>`.

3. **Zero PII & Zero Credenciais em Código**:
   - NUNCA commitar nomes completos de titulares, CPFs, números de conta ou IPs domésticos (como `192.168.x.x`).
   - Toda chave, senha ou segredo vive em `.env` ou `.secrets` (ambos protegidos no `.gitignore`).

4. **Security Headers & CSP**:
   - O servidor FastAPI deve retornar sempre: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` e `Content-Security-Policy`.
