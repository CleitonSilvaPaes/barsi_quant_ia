/**
 * Barsi Quant IA - Lógica da Carteira Previdenciária
 */

let carteiraCompleta = [];
let ordenacaoAtual = { coluna: 'margem', asc: false };

async function carregarCarteira() {
  try {
    const res = await fetch("/api/carteira");
    carteiraCompleta = await res.json();
    const hasZeroCusto = carteiraCompleta.some(p => (!p.custo_medio || p.custo_medio <= 0) && p.qtd_total > 0);
    const avisoEl = document.getElementById("aviso-custo-medio-zero");
    if (avisoEl) {
      avisoEl.style.display = hasZeroCusto ? "flex" : "none";
    }
    aplicarFiltros();
  } catch (err) {
    document.getElementById("tbl-body").innerHTML = `<tr><td colspan="13" style="color:var(--red); text-align:center;">Erro ao carregar carteira: ${err}</td></tr>`;
  }
}

function aplicarFiltros() {
  const busca = (document.getElementById("filtro-busca").value || "").toLowerCase();
  const setor = document.getElementById("filtro-setor").value;
  const status = document.getElementById("filtro-status").value;

  let filtrados = carteiraCompleta.filter(p => {
    const matchBusca = !busca || p.ticker.toLowerCase().includes(busca) || (p.empresa || "").toLowerCase().includes(busca);
    const matchSetor = !setor || (p.setor || "").includes(setor);
    const matchStatus = !status || p.status === status;
    return matchBusca && matchSetor && matchStatus;
  });

  filtrados.sort((a, b) => {
    let vA = a[ordenacaoAtual.coluna];
    let vB = b[ordenacaoAtual.coluna];
    if (typeof vA === 'string') return ordenacaoAtual.asc ? vA.localeCompare(vB) : vB.localeCompare(a);
    return ordenacaoAtual.asc ? (vA - vB) : (vB - vA);
  });

  renderizarTabela(filtrados);
}

function ordenarPor(col) {
  if (ordenacaoAtual.coluna === col) {
    ordenacaoAtual.asc = !ordenacaoAtual.asc;
  } else {
    ordenacaoAtual.coluna = col;
    ordenacaoAtual.asc = false;
  }
  aplicarFiltros();
}

function renderizarTabela(data) {
  const tbody = document.getElementById("tbl-body");
  tbody.innerHTML = "";

  let somaPatrimonioAtual = 0;
  let somaAporte = 0;
  let somaCotasAtuais = 0;
  let somaQtdAporte = 0;
  let abaixoDoTeto = 0;

  data.forEach(p => {
    const badgeClass = p.status === "ACUMULAR" ? "badge-acumular" : "badge-aguardar";
    const margemColor = p.margem >= 0 ? "var(--green)" : "var(--red)";
    const readOnlyAttr = p.is_readonly ? "disabled" : "";

    const qtdAtual = (p.qtd_antes !== undefined && p.qtd_antes !== null) ? p.qtd_antes : 0;
    const qtdAporte = p.qtd_aporte || 0;
    const qtdTotal = qtdAtual + qtdAporte;
    const preco = p.preco || 0;

    const valAtual = qtdAtual * preco;
    const valAporte = qtdAporte * preco;

    somaPatrimonioAtual += valAtual;
    somaAporte += valAporte;
    somaCotasAtuais += qtdAtual;
    somaQtdAporte += qtdAporte;
    if (p.status === "ACUMULAR") abaixoDoTeto++;

    tbody.innerHTML += `
      <tr data-ticker="${esc(p.ticker)}" data-row="${p.row_index}" data-preco="${preco}">
        <td>
          <strong>${esc(p.ticker)}</strong>
          <div style="font-size:0.75rem; color:var(--text-muted);">${esc(p.setor || 'B.E.S.T.')} • ${esc(p.empresa || '')}</div>
        </td>
        <td style="text-align: right;">
          <input type="number" class="tbl-input input-qtd" value="${qtdAtual}" min="0" ${readOnlyAttr} oninput="recalcularTotais()" style="width:75px; text-align:right;" />
        </td>
        <td style="text-align: right;">
          <input type="number" class="tbl-input input-aporte" value="${qtdAporte}" min="0" ${readOnlyAttr} oninput="recalcularTotais()" style="width:75px; text-align:right;" />
        </td>
        <td style="text-align: right; font-weight:600; color:#94a3b8;" class="cell-qtd-total">${qtdTotal}</td>
        <td style="text-align: right;">R$ ${preco.toFixed(2)}</td>
        <td style="text-align: right;">
          <input type="number" step="0.01" class="tbl-input input-teto" value="${p.teto.toFixed(2)}" ${readOnlyAttr} oninput="recalcularPorTeto(this)" style="width:75px; text-align:right;" title="Editar Preço Teto" />
        </td>
        <td style="text-align: right;">
          <input type="number" step="0.1" class="tbl-input input-margem" value="${p.margem.toFixed(1)}" ${readOnlyAttr} oninput="recalcularPorMargem(this)" style="width:65px; text-align:right; color:${margemColor}; font-weight:700;" title="Editar Margem (%)" />
        </td>
        <td style="text-align: right;">
          <input type="number" step="0.01" class="tbl-input input-cmedio" value="${(p.custo_medio || 0).toFixed(2)}" ${readOnlyAttr} style="width:75px; text-align:right;" title="Preço Médio de Aquisição" />
        </td>
        <td style="text-align: right; color: var(--green); font-weight: 600;" title="Yield on Cost: Proventos / Custo Médio">
          ${p.yield_on_cost > 0 ? p.yield_on_cost.toFixed(2) + '%' : '-'}
        </td>
        <td style="text-align: right;">
          <input type="number" step="0.01" class="tbl-input input-dpa" value="${p.div_proj.toFixed(2)}" ${readOnlyAttr} style="width:75px; text-align:right;" />
        </td>
        <td style="text-align: right;">
          <input type="number" step="0.01" class="tbl-input input-patrimonio" value="${valAtual.toFixed(2)}" ${readOnlyAttr} oninput="recalcularPorPatrimonio(this)" style="width:90px; text-align:right; font-weight:600;" title="Editar Patrimônio Atual" />
        </td>
        <td style="text-align: right; color: var(--green); font-weight: 600;" class="cell-total-aporte">R$ ${valAporte.toFixed(2)}</td>
        <td style="text-align: center;"><span class="${esc(badgeClass)}">${esc(p.status)}</span></td>
        <td><input type="text" class="tbl-input input-fb" value="${esc(p.feedback || '')}" placeholder="Ex: Priorizar compras em margem > 15%" ${readOnlyAttr} /></td>
        <td style="text-align: center; white-space:nowrap;">
          <button onclick="abrirDrawer('${esc(p.ticker)}')" class="btn btn-outline" style="padding:0.2rem 0.45rem; font-size:0.8rem;" title="Ver detalhes">👁️</button>
          <button onclick="excluirPosicao('${esc(p.ticker)}')" class="btn btn-outline" style="padding:0.2rem 0.45rem; font-size:0.8rem; color:var(--red);" title="Excluir ativo">🗑️</button>
        </td>
      </tr>
    `;
  });

  document.getElementById("card-patrimonio").innerText = formatBRL(somaPatrimonioAtual);
  document.getElementById("card-cotas").innerText = `${somaCotasAtuais} cotas em carteira`;
  document.getElementById("card-aporte").innerText = formatBRL(somaAporte);
  document.getElementById("card-qtd-aporte").innerText = `${somaQtdAporte} novas ações programadas`;
  document.getElementById("card-patrimonio-pos").innerText = formatBRL(somaPatrimonioAtual + somaAporte);
  document.getElementById("card-radar").innerText = `${abaixoDoTeto} de ${data.length}`;
}

function recalcularTotais() {
  const rows = document.querySelectorAll("#tbl-body tr");
  let somaAtual = 0;
  let somaAporte = 0;
  let somaCotas = 0;
  let somaQtdAp = 0;

  rows.forEach(r => {
    const preco = parseFloat(r.getAttribute("data-preco")) || 0;
    const inAtual = r.querySelector(".input-qtd");
    const inAporte = r.querySelector(".input-aporte");
    if (!inAtual || !inAporte) return;

    const qAtual = parseInt(inAtual.value) || 0;
    const qAp = parseInt(inAporte.value) || 0;
    const qTot = qAtual + qAp;

    const vAtual = qAtual * preco;
    const vAp = qAp * preco;

    somaAtual += vAtual;
    somaAporte += vAp;
    somaCotas += qAtual;
    somaQtdAp += qAp;

    const cellTot = r.querySelector(".cell-qtd-total");
    if (cellTot) cellTot.innerText = qTot;
    const cellPat = r.querySelector(".cell-patrimonio-atual");
    if (cellPat) cellPat.innerText = `R$ ${vAtual.toFixed(2)}`;
    const cellAp = r.querySelector(".cell-total-aporte");
    if (cellAp) cellAp.innerText = `R$ ${vAp.toFixed(2)}`;
  });

  document.getElementById("card-patrimonio").innerText = formatBRL(somaAtual);
  document.getElementById("card-cotas").innerText = `${somaCotas} cotas em carteira`;
  document.getElementById("card-aporte").innerText = formatBRL(somaAporte);
  document.getElementById("card-qtd-aporte").innerText = `${somaQtdAp} novas ações programadas`;
  document.getElementById("card-patrimonio-pos").innerText = formatBRL(somaAtual + somaAporte);
}

function recalcularPorTeto(el) {
  const row = el.closest("tr");
  if (!row) return;
  const preco = parseFloat(row.getAttribute("data-preco")) || 0;
  const teto = parseFloat(el.value) || 0;
  if (preco > 0) {
    const margem = ((teto - preco) / preco) * 100;
    const inMargem = row.querySelector(".input-margem");
    if (inMargem) {
      inMargem.value = margem.toFixed(1);
      inMargem.style.color = margem >= 0 ? "var(--green)" : "var(--red)";
    }
  }
}

function recalcularPorMargem(el) {
  const row = el.closest("tr");
  if (!row) return;
  const preco = parseFloat(row.getAttribute("data-preco")) || 0;
  const margem = parseFloat(el.value) || 0;
  if (preco > 0) {
    const teto = preco * (1 + margem / 100);
    const inTeto = row.querySelector(".input-teto");
    if (inTeto) inTeto.value = teto.toFixed(2);
    el.style.color = margem >= 0 ? "var(--green)" : "var(--red)";
  }
}

function recalcularPorPatrimonio(el) {
  const row = el.closest("tr");
  if (!row) return;
  const preco = parseFloat(row.getAttribute("data-preco")) || 0;
  const pat = parseFloat(el.value) || 0;
  if (preco > 0) {
    const qtd = Math.max(0, Math.round(pat / preco));
    const inQtd = row.querySelector(".input-qtd");
    if (inQtd) inQtd.value = qtd;
  }
  recalcularTotais();
}

async function excluirPosicao(ticker) {
  if (!confirm(`Deseja realmente excluir o ativo ${ticker} da carteira?`)) return;
  try {
    const res = await fetch(`/api/carteira/${ticker}`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || `Ativo ${ticker} excluído com sucesso!`);
      carregarCarteira();
    } else {
      showToast(data.detail || "Erro ao excluir ativo", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

function abrirModalNovoAtivo() {
  const el = document.getElementById("modal-novo-ativo");
  if (el) el.style.display = "flex";
}

function fecharModalNovoAtivo() {
  const el = document.getElementById("modal-novo-ativo");
  if (el) el.style.display = "none";
}

async function adicionarAtivo(e) {
  e.preventDefault();
  const ticker = document.getElementById("novo-ticker").value.trim().toUpperCase();
  const qtd_antes = parseInt(document.getElementById("novo-qtd").value) || 0;
  const preco_atual = parseFloat(document.getElementById("novo-preco").value) || 0.0;
  const preco_teto = parseFloat(document.getElementById("novo-teto").value) || 0.0;
  const div_proj = parseFloat(document.getElementById("novo-dpa").value) || 0.0;
  const custo_medio = parseFloat(document.getElementById("novo-cmedio").value) || 0.0;
  const setor = document.getElementById("novo-setor").value;

  try {
    const res = await fetch(`/api/carteira/${ticker}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticker,
        preco_atual,
        preco_teto,
        div_proj,
        custo_medio,
        qtd_antes,
        teto_manual: preco_teto > 0
      })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Ativo ${ticker} adicionado à carteira!`);
      fecharModalNovoAtivo();
      carregarCarteira();
    } else {
      showToast(data.detail || "Erro ao adicionar ativo", "error");
    }
  } catch (err) {
    showToast("Erro na requisição: " + err, "error");
  }
}

async function salvarAlteracoes() {
  const rows = document.querySelectorAll("#tbl-body tr");
  const items = [];
  rows.forEach(r => {
    const ticker = r.getAttribute("data-ticker");
    const row_index = parseInt(r.getAttribute("data-row"));
    if (!ticker || isNaN(row_index)) return;
    const div_proj = parseFloat(r.querySelector(".input-dpa") ? r.querySelector(".input-dpa").value : 0) || 0.0;
    const custo_medio = parseFloat(r.querySelector(".input-cmedio") ? r.querySelector(".input-cmedio").value : 0) || 0.0;
    const qtd_antes = parseInt(r.querySelector(".input-qtd") ? r.querySelector(".input-qtd").value : 0) || 0;
    const qtd_aporte = parseInt(r.querySelector(".input-aporte") ? r.querySelector(".input-aporte").value : 0) || 0;
    const preco_teto = parseFloat(r.querySelector(".input-teto") ? r.querySelector(".input-teto").value : 0) || 0.0;
    const margem = parseFloat(r.querySelector(".input-margem") ? r.querySelector(".input-margem").value : 0);
    const feedback = r.querySelector(".input-fb") ? r.querySelector(".input-fb").value : "";
    items.push({
      ticker,
      row_index,
      div_proj,
      custo_medio,
      qtd_antes,
      qtd_aporte,
      preco_teto,
      margem: isNaN(margem) ? null : margem,
      teto_manual: preco_teto > 0,
      feedback
    });
  });

  try {
    const res = await fetch("/api/carteira", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items })
    });
    const out = await res.json();
    if (res.ok) {
      showToast("Edições salvas com sucesso na base de dados!");
      carregarCarteira();
    } else {
      showToast("Erro ao salvar: " + (out.detail || res.statusText), "error");
    }
  } catch (err) {
    showToast("Erro ao salvar alterações: " + err, "error");
  }
}

async function exportarSheets() {
  try {
    showToast("Exportando dados para o Google Sheets...");
    const res = await fetch("/api/export/sheets", { method: "POST" });
    const out = await res.json();
    if (res.ok) {
      showToast(out.message || "Exportação concluída com sucesso!");
    } else {
      showToast("Falha na exportação: " + (out.detail || res.statusText), "error");
    }
  } catch (err) {
    showToast("Erro na exportação para o Sheets: " + err, "error");
  }
}

function exportarXlsx() {
  showToast("Baixando planilha Excel...");
  window.location.href = "/api/export/xlsx";
}

function abrirDrawer(ticker) {
  const p = carteiraCompleta.find(item => item.ticker === ticker);
  if (!p) return;

  document.getElementById("drawer-ticker").innerText = p.ticker;
  document.getElementById("drawer-empresa").innerText = `${p.empresa || ''} • Setor: ${p.setor || 'B.E.S.T.'}`;
  document.getElementById("drawer-margem").innerText = `${p.margem.toFixed(1)}%`;
  document.getElementById("drawer-precos").innerText = `Cotação Atual: R$ ${p.preco.toFixed(2)} | Preço Teto Barsi: R$ ${p.teto.toFixed(2)}`;
  document.getElementById("drawer-custodia").innerText = `${p.qtd_antes || 0} cotas (R$ ${((p.qtd_antes || 0) * p.preco).toFixed(2)})`;
  document.getElementById("drawer-aporte").innerText = `Aporte programado: ${p.qtd_aporte || 0} cotas (R$ ${((p.qtd_aporte || 0) * p.preco).toFixed(2)})`;
  document.getElementById("drawer-insight").innerText = p.insight_atual || 'Margem de segurança atrativa para acúmulo de longo prazo com base nas regras quantitativas do Método Barsi.';

  document.getElementById("asset-drawer").style.display = "block";
}

function fecharDrawer() {
  document.getElementById("asset-drawer").style.display = "none";
}

async function executarSync() {
  try {
    showToast("Sincronizando cotações B3 e IA...");
    const res = await fetch("/api/run", { method: "POST" });
    const out = await res.json();
    showToast(`Sincronização concluída! ${out.tickers_analyzed} ativos atualizados.`);
    carregarCarteira();
  } catch (err) {
    showToast("Erro na sincronização: " + err, "error");
  }
}

async function conciliarOFXCarteira() {
  try {
    showToast("Conciliando aportes do extrato OFX com as posições em carteira...");
    const res = await fetch("/api/extratos", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || "Aportes conciliados com sucesso!");
      carregarCarteira();
    } else {
      showToast(data.detail || "Erro ao conciliar extratos", "error");
    }
  } catch (err) {
    showToast("Erro ao conectar na API: " + err, "error");
  }
}

let carteiraArtifactCharts = {};

async function carregarArtefatosCarteira() {
  const sec = document.getElementById("carteira-artifacts-section");
  const container = document.getElementById("carteira-artifacts-container");
  if (!sec || !container) return;

  Object.values(carteiraArtifactCharts).forEach(c => {
    try { c.destroy(); } catch (e) {}
  });
  carteiraArtifactCharts = {};

  try {
    const res = await fetch("/api/artifacts?visible_in=carteira");
    if (!res.ok) return;
    const data = await res.json();
    const items = data.items || [];
    if (!items.length) {
      sec.style.display = "none";
      return;
    }
    sec.style.display = "block";
    container.innerHTML = "";

    for (const art of items) {
      const card = document.createElement("div");
      card.className = "card";
      card.style.margin = "0";
      card.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;">
          <div>
            <h4 style="color: var(--accent); font-size: 0.95rem; margin: 0;">${esc(art.name)}</h4>
            <span style="font-size: 0.72rem; color: var(--text-muted);">${esc(art.description || art.data_source)}</span>
          </div>
          <a href="/api/artifacts/${art.id}/export" class="btn btn-outline" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;">📥 Excel</a>
        </div>
        <div class="chart-container" style="height: 240px;">
          <canvas id="carteira-art-canvas-${art.id}"></canvas>
        </div>
      `;
      container.appendChild(card);

      const dRes = await fetch(`/api/artifacts/${art.id}/data`);
      if (dRes.ok) {
        const dData = await dRes.json();
        renderCarteiraArtifactChart(art, dData);
      }
    }
  } catch (e) {
    console.warn("Falha ao carregar artefatos na Carteira:", e);
  }
}

function renderCarteiraArtifactChart(art, data) {
  renderDynamicArtifactChart(`carteira-art-canvas-${art.id}`, art, data, carteiraArtifactCharts);
}

window.addEventListener("DOMContentLoaded", () => {
  carregarCarteira();
  carregarArtefatosCarteira();
});

