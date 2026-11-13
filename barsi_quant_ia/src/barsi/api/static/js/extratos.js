/**
 * Barsi Quant IA - Conciliação e Upload de Extratos Bancários (OFX)
 */

let paginaAtual = 1;
let limitePorPagina = 10;
let totalTransacoes = 0;
let buscaAtual = "";
let categoriaAtual = "";
let todasCategorias = [];
let transacoesCarregadas = [];

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
    paginaAtual = 1;
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

// ------------------- GESTÃO DE CATEGORIAS (CRUD) -------------------
async function carregarCategorias() {
  try {
    const res = await fetch("/api/categories");
    if (!res.ok) return;
    const data = await res.json();
    todasCategorias = data.categories || [];

    // Popula filtro de categorias na barra de busca
    const selectFiltro = document.getElementById("filtro-cat-transacoes");
    if (selectFiltro) {
      const valorAtual = selectFiltro.value;
      selectFiltro.innerHTML = `<option value="">Todas as Categorias</option>`;
      todasCategorias.forEach(c => {
        selectFiltro.innerHTML += `<option value="${esc(c.name)}">${esc(c.name)}</option>`;
      });
      selectFiltro.value = valorAtual;
    }

    // Popula select do modal de edição de transação
    const selectEdit = document.getElementById("edit-t-cat");
    if (selectEdit) {
      selectEdit.innerHTML = "";
      todasCategorias.forEach(c => {
        selectEdit.innerHTML += `<option value="${esc(c.name)}">${esc(c.name)}</option>`;
      });
    }

    renderizarListaCategorias();
  } catch (e) {
    console.warn("Erro ao buscar categorias:", e);
  }
}

function renderizarListaCategorias() {
  const tbody = document.getElementById("categorias-lista-body");
  if (!tbody) return;

  if (todasCategorias.length === 0) {
    tbody.innerHTML = `<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:1rem;">Nenhuma categoria encontrada.</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  todasCategorias.forEach(c => {
    tbody.innerHTML += `
      <tr>
        <td style="padding:0.5rem 0.8rem; font-weight:600;">${esc(c.name)}</td>
        <td style="padding:0.5rem 0.8rem;"><span class="badge-aguardar" style="font-size:0.75rem; text-transform:uppercase;">${esc(c.kind)}</span></td>
        <td style="padding:0.5rem 0.8rem; text-align:center; white-space:nowrap;">
          <button class="btn btn-outline" onclick="editarCategoria(${c.id}, '${esc(c.name)}', '${esc(c.kind)}')" style="padding:0.15rem 0.4rem; font-size:0.75rem;" title="Editar">✏️</button>
          <button class="btn btn-outline" onclick="excluirCategoria(${c.id}, '${esc(c.name)}')" style="padding:0.15rem 0.4rem; font-size:0.75rem; color:var(--red);" title="Excluir">🗑️</button>
        </td>
      </tr>
    `;
  });
}

function abrirModalCategorias() {
  const m = document.getElementById("modal-categorias");
  if (m) {
    m.style.display = "flex";
    carregarCategorias();
  }
}

function fecharModalCategorias() {
  const m = document.getElementById("modal-categorias");
  if (m) m.style.display = "none";
}

async function adicionarCategoria(e) {
  e.preventDefault();
  const nomeInput = document.getElementById("nova-cat-nome");
  const tipoInput = document.getElementById("nova-cat-tipo");
  const name = nomeInput.value.trim();
  const kind = tipoInput.value;
  if (!name) return;

  try {
    const res = await fetch("/api/categories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, kind })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Categoria '${name}' adicionada com sucesso!`);
      nomeInput.value = "";
      await carregarCategorias();
    } else {
      showToast(data.detail || "Erro ao adicionar categoria", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

async function editarCategoria(id, nomeAtual, tipoAtual) {
  const novoNome = prompt("Novo nome para a categoria:", nomeAtual);
  if (novoNome === null) return;
  const nomeFinal = novoNome.trim();
  if (!nomeFinal) {
    showToast("O nome da categoria não pode ser vazio.", "error");
    return;
  }
  const novoTipo = prompt("Tipo da categoria (fixa, variavel, tributo, reserva, investimento, entrada):", tipoAtual);
  if (novoTipo === null) return;

  try {
    const res = await fetch(`/api/categories/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: nomeFinal, kind: novoTipo.trim().toLowerCase(), active: true })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("Categoria atualizada com sucesso!");
      await carregarCategorias();
      carregarExtratos();
    } else {
      showToast(data.detail || "Erro ao atualizar categoria", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

async function excluirCategoria(id, nome) {
  if (!confirm(`Deseja realmente excluir a categoria '${nome}'?`)) return;
  try {
    const res = await fetch(`/api/categories/${id}`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      showToast("Categoria excluída com sucesso!");
      await carregarCategorias();
    } else {
      showToast(data.detail || "Erro ao excluir categoria", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

// ------------------- LISTAGEM E PAGINAÇÃO DE TRANSAÇÕES -------------------
let debounceTimer = null;
function onSearchInput() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    buscaAtual = (document.getElementById("filtro-busca-transacoes")?.value || "").trim();
    paginaAtual = 1;
    carregarExtratos();
  }, 250);
}

function onCategoryChange() {
  categoriaAtual = document.getElementById("filtro-cat-transacoes")?.value || "";
  paginaAtual = 1;
  carregarExtratos();
}

function onLimitChange(val) {
  limitePorPagina = parseInt(val) || 10;
  paginaAtual = 1;
  carregarExtratos();
}

function paginaAnterior() {
  if (paginaAtual > 1) {
    paginaAtual--;
    carregarExtratos();
  }
}

function proximaPagina() {
  const totalPaginas = Math.ceil(totalTransacoes / limitePorPagina) || 1;
  if (paginaAtual < totalPaginas) {
    paginaAtual++;
    carregarExtratos();
  }
}

async function carregarExtratos() {
  try {
    const offset = (paginaAtual - 1) * limitePorPagina;
    let url = `/api/extratos/transacoes?limit=${limitePorPagina}&offset=${offset}`;
    if (buscaAtual) url += `&search=${encodeURIComponent(buscaAtual)}`;
    if (categoriaAtual) url += `&categoria=${encodeURIComponent(categoriaAtual)}`;

    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.getElementById("transacoes-body");
    if (!tbody) return;

    transacoesCarregadas = data.transacoes || [];
    totalTransacoes = data.total_count || transacoesCarregadas.length;

    // Atualiza controles de paginação
    const totalPaginas = Math.max(1, Math.ceil(totalTransacoes / limitePorPagina));
    const infoEl = document.getElementById("paginacao-info");
    const pagAtualEl = document.getElementById("pag-atual");
    const pagTotalEl = document.getElementById("pag-total");
    const btnPrev = document.getElementById("btn-pag-prev");
    const btnNext = document.getElementById("btn-pag-next");

    if (infoEl) {
      const inicio = totalTransacoes === 0 ? 0 : offset + 1;
      const fim = Math.min(offset + transacoesCarregadas.length, totalTransacoes);
      infoEl.innerText = `Mostrando ${inicio} a ${fim} de ${totalTransacoes} transações`;
    }
    if (pagAtualEl) pagAtualEl.innerText = paginaAtual;
    if (pagTotalEl) pagTotalEl.innerText = totalPaginas;
    if (btnPrev) btnPrev.disabled = (paginaAtual <= 1);
    if (btnNext) btnNext.disabled = (paginaAtual >= totalPaginas);

    if (transacoesCarregadas.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">Nenhuma transação encontrada com os filtros informados.</td></tr>`;
      return;
    }

    tbody.innerHTML = "";
    transacoesCarregadas.forEach(t => {
      const isCredit = t.tipo === "CREDIT";
      const valColor = isCredit ? "var(--green)" : "var(--red)";
      const sinal = isCredit ? "+" : "-";
      const valFormatted = (t.valor || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
      tbody.innerHTML += `
        <tr>
          <td>${esc(t.data || '')}</td>
          <td><strong>${esc(t.descricao || '')}</strong></td>
          <td><span class="badge-aguardar" style="font-size:0.75rem;">${esc(t.categoria || 'Geral')}</span></td>
          <td>${esc(t.tipo || 'DEBIT')}</td>
          <td style="text-align: right; color: ${valColor}; font-weight:600;">${sinal} ${valFormatted}</td>
          <td style="text-align: center;"><span class="badge-acumular">CONCILIADO</span></td>
          <td style="text-align: center; white-space:nowrap;">
            <button class="btn btn-outline" onclick="abrirModalEditarTransacao(${t.id})" style="padding:0.2rem 0.45rem; font-size:0.75rem;" title="Editar">✏️</button>
            <button class="btn btn-outline" onclick="excluirTransacao(${t.id}, '${esc(t.descricao || '')}')" style="padding:0.2rem 0.45rem; font-size:0.75rem; color:var(--red);" title="Excluir">🗑️</button>
          </td>
        </tr>
      `;
    });
  } catch (e) {
    console.warn("Erro ao buscar transações:", e);
  }
}

// ------------------- EDIÇÃO E EXCLUSÃO DE TRANSAÇÃO -------------------
function abrirModalEditarTransacao(id) {
  const t = transacoesCarregadas.find(item => item.id === id);
  if (!t) return;

  document.getElementById("edit-t-id").value = t.id;
  document.getElementById("edit-t-desc").value = t.descricao || "";
  document.getElementById("edit-t-data").value = t.data || "";
  document.getElementById("edit-t-valor").value = (t.valor || 0).toFixed(2);
  document.getElementById("edit-t-cat").value = t.categoria || "";
  document.getElementById("edit-t-tipo").value = t.tipo || "DEBIT";

  const modal = document.getElementById("modal-editar-transacao");
  if (modal) modal.style.display = "flex";
}

function fecharModalEditarTransacao() {
  const modal = document.getElementById("modal-editar-transacao");
  if (modal) modal.style.display = "none";
}

async function salvarEdicaoTransacao(e) {
  e.preventDefault();
  const id = document.getElementById("edit-t-id").value;
  const descricao = document.getElementById("edit-t-desc").value.trim();
  const data = document.getElementById("edit-t-data").value;
  const valor = parseFloat(document.getElementById("edit-t-valor").value) || 0;
  const categoria = document.getElementById("edit-t-cat").value;
  const tipo = document.getElementById("edit-t-tipo").value;

  try {
    const res = await fetch(`/api/extratos/transacoes/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ descricao, data, valor, categoria, tipo })
    });
    const out = await res.json();
    if (res.ok) {
      showToast("Transação atualizada com sucesso!");
      fecharModalEditarTransacao();
      carregarExtratos();
    } else {
      showToast(out.detail || "Erro ao atualizar transação", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

async function excluirTransacao(id, descricao) {
  if (!confirm(`Deseja realmente excluir a transação "${descricao}"?`)) return;
  try {
    const res = await fetch(`/api/extratos/transacoes/${id}`, { method: "DELETE" });
    const out = await res.json();
    if (res.ok) {
      showToast("Transação excluída com sucesso!");
      carregarExtratos();
    } else {
      showToast(out.detail || "Erro ao excluir transação", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  carregarCategorias();
  carregarExtratos();
});
