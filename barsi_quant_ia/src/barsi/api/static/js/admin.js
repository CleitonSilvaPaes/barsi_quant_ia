/**
 * Barsi Quant IA - Painel de Administração & Usuários (RBAC)
 */

async function carregarUsuarios() {
  try {
    const res = await fetch("/api/admin/users");
    const users = await res.json();
    document.getElementById("adm-users-count").innerText = users.length;
    const tbody = document.getElementById("tbl-users");
    tbody.innerHTML = "";

    let temAdminSem2FA = false;

    users.forEach(u => {
      let badge2fa = "";
      if (u.totp_enabled) {
        badge2fa = `<span style="color:var(--green); font-weight:600; font-size:0.8rem;">🔒 Ativo</span>`;
      } else if (u.role === "admin") {
        temAdminSem2FA = true;
        badge2fa = `<span style="color:#ef4444; font-weight:600; font-size:0.8rem; background:rgba(239,68,68,0.12); padding:2px 8px; border-radius:4px; border:1px solid rgba(239,68,68,0.3);">⚠️ Sem 2FA (<a href="/settings" style="color:#fca5a5; text-decoration:underline;">ativar</a>)</span>`;
      } else {
        badge2fa = `<span style="color:var(--text-muted); font-size:0.8rem;">Opcional</span>`;
      }

      tbody.innerHTML += `
        <tr>
          <td>#${u.id}</td>
          <td><strong>${esc(u.username)}</strong></td>
          <td><span class="badge-acumular" style="text-transform:uppercase;">${esc(u.role)}</span></td>
          <td>${badge2fa}</td>
          <td style="color:var(--text-muted); font-size:0.8rem;">${esc(u.created_at || '—')}</td>
        </tr>
      `;
    });

    const warnEl = document.getElementById("admin-2fa-warning");
    if (warnEl) {
      warnEl.style.display = temAdminSem2FA ? "block" : "none";
    }
  } catch (err) {
    document.getElementById("tbl-users").innerHTML = `<tr><td colspan="5" style="color:var(--red);">Erro ao carregar usuários: ${err}</td></tr>`;
  }
}

async function criarUsuario(e) {
  e.preventDefault();
  const username = document.getElementById("new-user-name").value.trim();
  const password = document.getElementById("new-user-pass").value.trim();
  const role = document.getElementById("new-user-role").value;

  try {
    const res = await fetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, role })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("Novo usuário cadastrado com sucesso!");
      document.getElementById("new-user-name").value = "";
      document.getElementById("new-user-pass").value = "";
      carregarUsuarios();
    } else {
      showToast(data.detail || "Erro ao criar usuário", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

window.addEventListener("DOMContentLoaded", carregarUsuarios);
