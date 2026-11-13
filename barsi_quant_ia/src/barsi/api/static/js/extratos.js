/**
 * Barsi Quant IA - Conciliação e Upload de Extratos Bancários (OFX)
 */

function getValidTargetMonth() {
  const input = document.getElementById("target-month-input");
  const val = (input?.value || "").trim();
  if (!val) return "";
  if (!/^\d{4}-\d{2}$/.test(val)) {
    showToast("Mês alvo inválido. Use o formato YYYY-MM (ex: 2026-11).", "error");
    throw new Error("Formato inválido de mês alvo (esperado: YYYY-MM)");
  }
  const parts = val.split("-");
  const ano = parseInt(parts[0], 10);
  const mes = parseInt(parts[1], 10);
  if (ano < 2020 || ano > 2100 || mes < 1 || mes > 12) {
    showToast("Mês alvo fora da faixa permitida (2020 a 2100).", "error");
    throw new Error("Mês alvo fora da faixa permitida");
  }
  return val;
}

async function uploadExtrato(file) {
  if (!file) return;
  let targetMonth = "";
  try {
    targetMonth = getValidTargetMonth();
  } catch (e) {
    return;
  }
  const log = document.getElementById("extrato-log");
  log.style.display = "block";
  log.innerText = `[${new Date().toLocaleTimeString()}] Enviando '${file.name}' para processamento...`;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const url = targetMonth ? `/api/upload-extrato?target_month=${encodeURIComponent(targetMonth)}` : "/api/upload-extrato";
    const res = await fetch(url, { method: "POST", body: formData });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Erro HTTP ${res.status}`);
    }
    const out = await res.json();
    log.innerText = `[${new Date().toLocaleTimeString()}] ✅ Arquivo '${out.filename}' recebido e processado com sucesso! Meses conciliados: ${out.meses.join(', ')}.`;
    showToast("Extrato importado com sucesso!");
    carregarExtratos();
  } catch (err) {
    log.innerText = `[${new Date().toLocaleTimeString()}] ❌ Erro ao enviar extrato: ` + err;
    showToast("Falha no upload do extrato: " + err.message, "error");
  }
}

async function conciliarExtratos() {
  let targetMonth = "";
  try {
    targetMonth = getValidTargetMonth();
  } catch (e) {
    return;
  }
  const log = document.getElementById("extrato-log");
  log.style.display = "block";
  log.innerText = `[${new Date().toLocaleTimeString()}] Conciliando extratos OFX locais com o orçamento...`;
  try {
    const url = targetMonth ? `/api/extratos?target_month=${encodeURIComponent(targetMonth)}` : "/api/extratos";
    const res = await fetch(url, { method: "POST" });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Erro HTTP ${res.status}`);
    }
    const out = await res.json();
    log.innerText = `[${new Date().toLocaleTimeString()}] ✅ Conciliação concluída! Meses atualizados: ${out.meses.join(', ')}. Ativos conciliados: ${out.reconciled_positions || 0}.`;
    showToast(out.message || "Extratos conciliados com sucesso!");
    carregarExtratos();
  } catch (err) {
    log.innerText = `[${new Date().toLocaleTimeString()}] ❌ Erro: ` + err;
    showToast("Erro na conciliação: " + err.message, "error");
  }
}

async function arquivarExtratos() {
  const log = document.getElementById("extrato-log");
  log.style.display = "block";
  log.innerText = `[${new Date().toLocaleTimeString()}] Arquivando extratos processados...`;
  try {
    const res = await fetch("/api/extratos/arquivar", { method: "POST" });
    const out = await res.json();
    log.innerText = `[${new Date().toLocaleTimeString()}] 📦 ${out.message}`;
    showToast(out.message);
  } catch (err) {
    log.innerText = `[${new Date().toLocaleTimeString()}] ❌ Erro ao arquivar: ` + err;
    showToast("Erro ao arquivar extratos", "error");
  }
}

async function sincronizarTVBox() {
  const log = document.getElementById("extrato-log");
  log.style.display = "block";
  log.innerText = `[${new Date().toLocaleTimeString()}] Conectando na TV Box via SSH...`;
  try {
    const res = await fetch("/api/sync-tvbox", { method: "POST" });
    const out = await res.json();
    log.innerText = `[${new Date().toLocaleTimeString()}] ✅ TV Box atualizada com sucesso! (${out.tvbox_ip})`;
    showToast("TV Box sincronizada!");
  } catch (err) {
    log.innerText = `[${new Date().toLocaleTimeString()}] ❌ Erro: ` + err;
    showToast("Erro na sincronização", "error");
  }
}

async function carregarExtratos() {
  try {
    const res = await fetch("/api/extratos/transacoes?limit=50");
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.transacoes || data.transacoes.length === 0) return;

    const tbody = document.getElementById("transacoes-body");
    tbody.innerHTML = "";
    data.transacoes.forEach(t => {
      const isCredit = t.tipo === "CREDIT";
      const valColor = isCredit ? "var(--green)" : "var(--red)";
      const sinal = isCredit ? "+" : "-";
      const valFormatted = (t.valor || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
      tbody.innerHTML += `
        <tr>
          <td>${esc(t.data || '')}</td>
          <td><strong>${esc(t.descricao || '')}</strong></td>
          <td>${esc(t.categoria || 'Geral')}</td>
          <td>${esc(t.origem || 'CONTA')}</td>
          <td style="text-align: right; color: ${valColor}; font-weight:600;">${sinal} ${valFormatted}</td>
          <td style="text-align: center;"><span class="badge-acumular">CONCILIADO</span></td>
        </tr>
      `;
    });
    showToast(`${data.transacoes.length} transações carregadas.`);
  } catch (e) {
    console.warn("Erro ao buscar transações:", e);
  }
}

document.addEventListener("DOMContentLoaded", carregarExtratos);

