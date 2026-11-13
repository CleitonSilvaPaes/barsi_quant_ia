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

      let emailGoogle = "—";
      if (u.email || u.google_id) {
        const emailTxt = u.email ? esc(u.email) : "Google Conectado";
        const gBadge = u.google_id ? '<span title="Conta Google vinculada" style="margin-left:4px; cursor:help;">🌐</span>' : '';
        emailGoogle = `<span style="font-size:0.8rem; color:#cbd5e1;">${emailTxt}</span>${gBadge}`;
      }

      tbody.innerHTML += `
        <tr>
          <td>#${u.id}</td>
          <td><strong>${esc(u.username)}</strong></td>
          <td>${emailGoogle}</td>
          <td><span class="badge-acumular" style="text-transform:uppercase;">${esc(u.role)}</span></td>
          <td>${badge2fa}</td>
          <td style="color:var(--text-muted); font-size:0.8rem;">${esc(u.created_at || '—')}</td>
          <td style="text-align:center; white-space:nowrap;">
            <button type="button" class="btn btn-secondary" onclick="abrirModalTrocarSenha(${u.id}, '${esc(u.username)}')" style="padding:0.25rem 0.5rem; font-size:0.75rem;" title="Redefinir Senha">🔑 Senha</button>
            <button type="button" class="btn btn-secondary" onclick="vincularEmail(${u.id}, '${esc(u.email || '')}')" style="padding:0.25rem 0.5rem; font-size:0.75rem; margin-left:4px;" title="Vincular Google">📧 Email</button>
          </td>
        </tr>
      `;
    });

    const warnEl = document.getElementById("admin-2fa-warning");
    if (warnEl) {
      warnEl.style.display = temAdminSem2FA ? "block" : "none";
    }
  } catch (err) {
    document.getElementById("tbl-users").innerHTML = `<tr><td colspan="7" style="color:var(--red);">Erro ao carregar usuários: ${err}</td></tr>`;
  }
}

async function criarUsuario(e) {
  e.preventDefault();
  const username = document.getElementById("new-user-name").value.trim();
  const email = document.getElementById("new-user-email").value.trim();
  const password = document.getElementById("new-user-pass").value.trim();
  const role = document.getElementById("new-user-role").value;

  try {
    const res = await fetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, role, email: email || null })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("Novo usuário cadastrado com sucesso!");
      document.getElementById("new-user-name").value = "";
      document.getElementById("new-user-email").value = "";
      document.getElementById("new-user-pass").value = "";
      carregarUsuarios();
    } else {
      showToast(data.detail || "Erro ao criar usuário", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

function abrirModalTrocarSenha(userId, username) {
  document.getElementById("modal-pass-userid").value = userId;
  document.getElementById("modal-pass-username").innerText = username;
  document.getElementById("modal-new-pass").value = "";
  document.getElementById("modal-new-pass-confirm").value = "";
  document.getElementById("modal-trocar-senha").style.display = "flex";
}

function fecharModalTrocarSenha() {
  document.getElementById("modal-trocar-senha").style.display = "none";
}

async function confirmarTrocaSenha(e) {
  e.preventDefault();
  const userId = document.getElementById("modal-pass-userid").value;
  const newPass = document.getElementById("modal-new-pass").value;
  const confirmPass = document.getElementById("modal-new-pass-confirm").value;

  if (newPass !== confirmPass) {
    showToast("As senhas digitadas não coincidem!", "error");
    return;
  }
  if (newPass.length < 6) {
    showToast("A senha deve ter pelo menos 6 caracteres.", "error");
    return;
  }

  try {
    const res = await fetch(`/api/admin/users/${userId}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_password: newPass })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || "Senha atualizada com sucesso!");
      fecharModalTrocarSenha();
    } else {
      showToast(data.detail || "Erro ao atualizar senha", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

async function vincularEmail(userId, currentEmail) {
  const email = prompt("Digite o e-mail do Google para vincular a este usuário:", currentEmail);
  if (email === null) return;

  try {
    const res = await fetch(`/api/admin/users/${userId}/email`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim() })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || "E-mail vinculado com sucesso!");
      carregarUsuarios();
    } else {
      showToast(data.detail || "Erro ao vincular e-mail", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

window.addEventListener("DOMContentLoaded", carregarUsuarios);
