/**
 * Barsi Quant IA - Analytics & Benchmarks Frontend Controller
 */

let chartEquity = null;
let chartSetores = null;
let chartDividendos = null;
let chartSnowball = null;
let customChartInstances = {};
let artifactSourcesCache = null;

function safeFormatBRL(val) {
  if (typeof formatBRL === "function") {
    return formatBRL(val);
  }
  return (val || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function safeShowToast(msg, type = "success") {
  if (typeof showToast === "function") {
    showToast(msg, type);
  } else {
    console.log(`[Toast ${type}]: ${msg}`);
  }
}

async function carregarAnalytics() {
  const selectEl = document.getElementById("periodo-select");
  const meses = selectEl ? selectEl.value : 12;
  const titleEl = document.getElementById("title-equity-chart");
  if (titleEl) titleEl.innerText = `📈 Curva de Evolução Patrimonial (Últimos ${meses} Meses)`;

  try {
    const res = await fetch(`/api/analytics/data?months=${meses}`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    }
    const data = await res.json();

    const elPatrimonio = document.getElementById("an-patrimonio");
    if (elPatrimonio) elPatrimonio.innerText = safeFormatBRL(data.patrimonio_atual);

    const elProventos = document.getElementById("an-proventos");
    if (elProventos) elProventos.innerText = safeFormatBRL(data.proventos_proj_ano);

    // Yield médio ponderado calculado dinamicamente
    const yieldPond = data.patrimonio_atual > 0 ? ((data.proventos_proj_ano / data.patrimonio_atual) * 100).toFixed(1) : 0;
    const elYield = document.getElementById("an-yield-sub");
    if (elYield) elYield.innerText = `Yield projetado: ~${yieldPond}% a.a.`;

    // Renderização isolada de cada gráfico para tolerância a falhas
    try { renderEquityChart(data.snapshots || []); } catch (e) { console.error("Erro renderEquityChart:", e); }
    try { renderSetoresChart(data.setores || {}); } catch (e) { console.error("Erro renderSetoresChart:", e); }
    try { renderDividendosChart(data.dividendos || []); } catch (e) { console.error("Erro renderDividendosChart:", e); }

    // Carrega benchmarks reais
    await carregarBenchmarks();
  } catch (err) {
    console.error("Falha ao carregar dados de analytics:", err);
    safeShowToast("Erro ao carregar dados de analytics: " + err.message, "error");
  }
}

async function carregarBenchmarks() {
  try {
    const res = await fetch("/api/analytics/benchmarks");
    if (!res.ok) return;
    const bm = await res.json();

    const formatPct = (val) => (val !== null && val !== undefined) ? `${val >= 0 ? '+' : ''}${val.toFixed(1)}%` : '—';
    const formatPP = (val) => (val !== null && val !== undefined) ? `${val >= 0 ? '+' : ''}${val.toFixed(1)} pp` : '—';

    // Atualiza Carteira
    const cartEl = document.getElementById("bm-carteira-ret");
    if (cartEl) cartEl.innerText = formatPct(bm.carteira_12m);

    // Atualiza CDI
    const cdiRetEl = document.getElementById("bm-cdi-ret");
    const cdiSpreadEl = document.getElementById("bm-cdi-spread");
    const cdiStatusEl = document.getElementById("bm-cdi-status");
    if (cdiRetEl) cdiRetEl.innerText = formatPct(bm.cdi_12m);
    if (cdiSpreadEl) {
      cdiSpreadEl.innerText = formatPP(bm.spread_cdi);
      cdiSpreadEl.style.color = (bm.spread_cdi !== null && bm.spread_cdi >= 0) ? "var(--green)" : "var(--red)";
    }
    if (cdiStatusEl) {
      if (bm.spread_cdi === null) {
        cdiStatusEl.innerHTML = `<span style="color:var(--text-muted);">EM FORMAÇÃO</span>`;
      } else if (bm.spread_cdi >= 0) {
        cdiStatusEl.innerHTML = `<span class="badge-acumular">SUPEROU</span>`;
      } else {
        cdiStatusEl.innerHTML = `<span class="badge-aguardar">ABAIXO</span>`;
      }
    }

    // Atualiza IBOV
    const ibovRetEl = document.getElementById("bm-ibov-ret");
    const ibovSpreadEl = document.getElementById("bm-ibov-spread");
    const ibovStatusEl = document.getElementById("bm-ibov-status");
    if (ibovRetEl) ibovRetEl.innerText = formatPct(bm.ibov_12m);
    if (ibovSpreadEl) {
      ibovSpreadEl.innerText = formatPP(bm.spread_ibov);
      ibovSpreadEl.style.color = (bm.spread_ibov !== null && bm.spread_ibov >= 0) ? "var(--green)" : "var(--red)";
    }
    if (ibovStatusEl) {
      if (bm.spread_ibov === null) {
        ibovStatusEl.innerHTML = `<span style="color:var(--text-muted);">EM FORMAÇÃO</span>`;
      } else if (bm.spread_ibov >= 0) {
        ibovStatusEl.innerHTML = `<span class="badge-acumular">SUPEROU</span>`;
      } else {
        ibovStatusEl.innerHTML = `<span class="badge-aguardar">ABAIXO</span>`;
      }
    }

    // Atualiza IFIX
    const ifixRetEl = document.getElementById("bm-ifix-ret");
    const ifixSpreadEl = document.getElementById("bm-ifix-spread");
    const ifixStatusEl = document.getElementById("bm-ifix-status");
    if (ifixRetEl) ifixRetEl.innerText = formatPct(bm.ifix_12m);
    const spreadIfix = (bm.carteira_12m !== null && bm.ifix_12m !== null) ? (bm.carteira_12m - bm.ifix_12m) : null;
    if (ifixSpreadEl) {
      ifixSpreadEl.innerText = formatPP(spreadIfix);
      ifixSpreadEl.style.color = (spreadIfix !== null && spreadIfix >= 0) ? "var(--green)" : "var(--red)";
    }
    if (ifixStatusEl) {
      if (spreadIfix === null) {
        ifixStatusEl.innerHTML = `<span style="color:var(--text-muted);">EM FORMAÇÃO</span>`;
      } else if (spreadIfix >= 0) {
        ifixStatusEl.innerHTML = `<span class="badge-acumular">SUPEROU</span>`;
      } else {
        ifixStatusEl.innerHTML = `<span class="badge-aguardar">ABAIXO</span>`;
      }
    }

    // Atualiza Cards do topo
    const cdiValCard = document.getElementById("an-spread-cdi-val");
    const cdiSubCard = document.getElementById("an-spread-cdi-sub");
    if (cdiValCard) {
      cdiValCard.innerText = formatPP(bm.spread_cdi);
      cdiValCard.style.color = (bm.spread_cdi !== null && bm.spread_cdi >= 0) ? "var(--green)" : (bm.spread_cdi !== null ? "var(--red)" : "var(--text-muted)");
    }
    if (cdiSubCard) {
      cdiSubCard.innerText = `Carteira: ${formatPct(bm.carteira_12m)} | CDI: ${formatPct(bm.cdi_12m)}`;
    }

    const ibovValCard = document.getElementById("an-spread-ibov-val");
    const ibovSubCard = document.getElementById("an-spread-ibov-sub");
    if (ibovValCard) {
      ibovValCard.innerText = formatPP(bm.spread_ibov);
      ibovValCard.style.color = (bm.spread_ibov !== null && bm.spread_ibov >= 0) ? "var(--green)" : (bm.spread_ibov !== null ? "var(--red)" : "var(--text-muted)");
    }
    if (ibovSubCard) {
      ibovSubCard.innerText = `Carteira: ${formatPct(bm.carteira_12m)} | IBOV: ${formatPct(bm.ibov_12m)}`;
    }

    if (bm.fonte && document.getElementById("bm-fonte-caption")) {
      document.getElementById("bm-fonte-caption").innerText = `Fontes: ${bm.fonte}`;
    }
  } catch (e) {
    console.warn("Falha ao obter benchmarks:", e);
  }
}

function renderEquityChart(snapshots) {
  const canvas = document.getElementById('chart-equity');
  if (!canvas || typeof Chart === "undefined") return;
  const ctx = canvas.getContext('2d');
  if (chartEquity) chartEquity.destroy();

  const labels = snapshots.map(s => {
    const parts = (s.data_snapshot || "").split('-');
    return parts.length >= 2 ? `${parts[1]}/${parts[0].slice(2)}` : s.data_snapshot;
  });
  const values = snapshots.map(s => s.patrimonio_total);
  const isMobile = window.innerWidth < 600;

  chartEquity = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'Patrimônio em Ativos',
        data: values,
        borderColor: '#38bdf8',
        backgroundColor: 'rgba(56, 189, 248, 0.12)',
        fill: true,
        tension: 0.35,
        borderWidth: 3,
        pointRadius: isMobile ? 3 : 4,
        pointBackgroundColor: '#38bdf8'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      onClick: (evt, elements) => {
        if (!elements.length) return;
        const idx = elements[0].index;
        const snap = snapshots[idx];
        if (snap) {
          renderSetoresChart(snap.setores || {});
          const caption = document.getElementById('pie-caption');
          if (caption) caption.innerText = `Visualizando posição em: ${snap.data_snapshot}`;
        }
      },
      scales: {
        x: { 
          grid: { color: '#1e293d' }, 
          ticks: { color: '#94a3b8', font: { size: isMobile ? 9 : 11 } } 
        },
        y: { 
          grid: { color: '#1e293d' }, 
          ticks: { 
            color: '#94a3b8', 
            font: { size: isMobile ? 9 : 11 },
            callback: v => 'R$ ' + (v/1000).toFixed(0) + 'k' 
          } 
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: { mode: 'index', intersect: false }
      }
    }
  });
}

function renderSetoresChart(setores) {
  const canvas = document.getElementById('chart-setores');
  if (!canvas || typeof Chart === "undefined") return;
  const ctx = canvas.getContext('2d');
  if (chartSetores) chartSetores.destroy();

  const labels = Object.keys(setores);
  const values = Object.values(setores);
  const isMobile = window.innerWidth < 600;

  const palette = [
    '#2563eb', '#16a34a', '#d97706', '#9333ea', '#06b6d4', '#e11d48',
    '#f59e0b', '#10b981', '#6366f1', '#ec4899', '#8b5cf6', '#14b8a6'
  ];
  const bgColors = labels.map((_, idx) => palette[idx % palette.length]);

  chartSetores = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: bgColors,
        borderColor: '#111827',
        borderWidth: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { 
            color: '#cbd5e1', 
            font: { size: isMobile ? 10 : 11 },
            boxWidth: isMobile ? 10 : 12,
            padding: isMobile ? 6 : 10
          }
        },
        tooltip: { intersect: true }
      }
    }
  });
}

function renderDividendosChart(dividendos) {
  const canvas = document.getElementById('chart-dividendos');
  if (!canvas || typeof Chart === "undefined") return;
  const ctx = canvas.getContext('2d');
  if (chartDividendos) chartDividendos.destroy();

  const labels = dividendos.map(d => d.mes);
  const real = dividendos.map(d => d.realizado);
  const proj = dividendos.map(d => d.projetado);
  const isMobile = window.innerWidth < 600;

  chartDividendos = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        { label: 'Realizado', data: real, backgroundColor: '#10b981', borderRadius: 4 },
        { label: 'Projetado', data: proj, backgroundColor: '#6366f1', borderRadius: 4 }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { 
          grid: { color: '#1e293d' }, 
          ticks: { color: '#94a3b8', font: { size: isMobile ? 9 : 11 } } 
        },
        y: { 
          grid: { color: '#1e293d' }, 
          ticks: { 
            color: '#94a3b8', 
            font: { size: isMobile ? 9 : 11 },
            callback: v => 'R$ ' + v 
          } 
        }
      },
      plugins: {
        legend: { 
          labels: { 
            color: '#cbd5e1', 
            font: { size: isMobile ? 10 : 12 } 
          } 
        },
        tooltip: { mode: 'index', intersect: false }
      }
    }
  });
}

// ==============================================================
// GESTÃO & RENDERIZAÇÃO DOS ARTEFATOS PERSONALIZADOS
// ==============================================================
async function carregarFontesArtefatos() {
  if (artifactSourcesCache) return artifactSourcesCache;
  try {
    const res = await fetch("/api/artifacts/sources");
    if (res.ok) {
      artifactSourcesCache = await res.json();
    }
  } catch (e) {
    console.warn("Falha ao carregar fontes de artefatos:", e);
  }
  return artifactSourcesCache;
}

async function toggleArtifactBuilder() {
  const el = document.getElementById("artifact-builder-card");
  if (!el) return;
  if (el.style.display === "none" || !el.style.display) {
    el.style.display = "block";
    await atualizarCamposFonte();
    verificarTipoGrafico();
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  } else {
    el.style.display = "none";
  }
}

function verificarTipoGrafico() {
  const el = document.getElementById("art-chart");
  const tip = document.getElementById("art-chart-circular-tip");
  if (el && tip) {
    tip.style.display = (el.value === "doughnut" || el.value === "pie") ? "block" : "none";
  }
}

async function atualizarCamposFonte() {
  const src = document.getElementById("art-source").value;
  const sources = await carregarFontesArtefatos();
  const container = document.getElementById("art-fields-container");
  if (!container) return;

  container.innerHTML = "";
  const fields = (sources && sources[src]) ? sources[src].fields : [];
  if (!fields.length) {
    container.innerHTML = `<span style="font-size:0.8rem; color:var(--text-muted);">Nenhum campo disponível para esta fonte.</span>`;
    return;
  }

  fields.forEach((f, idx) => {
    const isDefaultChecked = idx < 2;
    const label = document.createElement("label");
    label.style.display = "inline-flex";
    label.style.alignItems = "center";
    label.style.gap = "0.35rem";
    label.style.fontSize = "0.82rem";
    label.style.color = "var(--text)";
    label.style.cursor = "pointer";
    label.innerHTML = `
      <input type="checkbox" name="art_field" value="${f.id}" ${isDefaultChecked ? "checked" : ""} />
      <span>${f.name}</span>
    `;
    container.appendChild(label);
  });
}

async function salvarArtefato(e) {
  e.preventDefault();
  const name = document.getElementById("art-name").value.trim();
  const chart_type = document.getElementById("art-chart").value;
  const data_source = document.getElementById("art-source").value;
  const description = document.getElementById("art-desc").value.trim();

  const checkedFields = Array.from(document.querySelectorAll("input[name='art_field']:checked")).map(cb => cb.value);
  if (!checkedFields.length) {
    safeShowToast("Selecione ao menos um campo para o relatório", "error");
    return;
  }

  const filters = {};
  const t = document.getElementById("art-filter-ticker").value.trim();
  if (t) filters["ticker"] = t.toUpperCase();
  const m = document.getElementById("art-filter-mes").value.trim();
  if (m) filters["mes_ano"] = m;

  const visible_in = [];
  if (document.getElementById("art-vis-analytics").checked) visible_in.push("analytics");
  if (document.getElementById("art-vis-home").checked) visible_in.push("home");
  if (document.getElementById("art-vis-carteira").checked) visible_in.push("carteira");
  if (!visible_in.length) visible_in.push("analytics");

  try {
    const res = await fetch("/api/artifacts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        chart_type,
        data_source,
        description,
        fields: checkedFields,
        filters,
        visible_in
      })
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Erro ao salvar artefato");
    }
    safeShowToast("Artefato salvo com sucesso!", "success");
    document.getElementById("artifact-form").reset();
    document.getElementById("artifact-builder-card").style.display = "none";
    await carregarArtefatos();
  } catch (err) {
    safeShowToast(err.message, "error");
  }
}

async function carregarArtefatos() {
  const container = document.getElementById("custom-artifacts-container");
  const emptyMsg = document.getElementById("no-artifacts-msg");
  if (!container) return;

  // Destrói instâncias anteriores
  Object.values(customChartInstances).forEach(c => {
    try { c.destroy(); } catch (e) {}
  });
  customChartInstances = {};

  try {
    const res = await fetch("/api/artifacts?visible_in=analytics");
    if (!res.ok) return;
    const json = await res.json();
    const items = json.items || [];

    if (!items.length) {
      container.innerHTML = "";
      if (emptyMsg) emptyMsg.style.display = "block";
      return;
    }

    if (emptyMsg) emptyMsg.style.display = "none";
    container.innerHTML = "";

    for (const art of items) {
      const card = document.createElement("div");
      card.className = "card";
      card.style.margin = "0";
      card.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.75rem; flex-wrap: wrap; gap: 0.5rem;">
          <div>
            <div style="display:flex; align-items:center; gap:0.5rem;">
              <h3 style="color: var(--accent); font-size: 1.05rem; margin: 0;">${art.name}</h3>
              <span class="badge-acumular" style="text-transform:uppercase; font-size:0.68rem;">${art.chart_type}</span>
            </div>
            <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 0.25rem;">
              ${art.description || `Fonte: ${art.data_source} | Campos: ${art.fields.join(', ')}`}
            </div>
          </div>
          <div style="display: flex; align-items: center; gap: 0.5rem;">
            <a href="/api/artifacts/${art.id}/export" class="btn btn-outline" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;" title="Exportar para planilha Excel (.xlsx)">
              📥 Excel
            </a>
            <button class="btn btn-logout" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="excluirArtefato(${art.id}, '${art.name}')" title="Excluir este artefato">
              🗑️
            </button>
          </div>
        </div>
        <div class="chart-container">
          <canvas id="canvas-art-${art.id}"></canvas>
        </div>
      `;
      container.appendChild(card);

      // Busca dados e renderiza o gráfico de cada artefato
      try {
        const dRes = await fetch(`/api/artifacts/${art.id}/data`);
        if (dRes.ok) {
          const dData = await dRes.json();
          renderCustomArtifactChart(art, dData);
        }
      } catch (e) {
        console.warn(`Erro ao carregar dados do artefato ${art.id}:`, e);
      }
    }
  } catch (err) {
    console.warn("Falha ao listar artefatos:", err);
  }
}

function renderCustomArtifactChart(art, data) {
  if (typeof renderDynamicArtifactChart === "function") {
    renderDynamicArtifactChart(`canvas-art-${art.id}`, art, data, customChartInstances);
  }
}

async function excluirArtefato(id, name) {
  if (!confirm(`Deseja realmente excluir o artefato "${name}"?`)) return;
  try {
    const res = await fetch(`/api/artifacts/${id}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erro ao excluir artefato");
    safeShowToast("Artefato excluído com sucesso!", "success");
    await carregarArtefatos();
  } catch (e) {
    safeShowToast(e.message, "error");
  }
}

async function executarSimulacaoBolaDeNeve() {
  try {
    const aporte = parseFloat(document.getElementById("sb-aporte").value) || 1000;
    const anos = parseInt(document.getElementById("sb-anos").value) || 20;
    const dy = (parseFloat(document.getElementById("sb-yield").value) || 7.5);
    const growth = (parseFloat(document.getElementById("sb-cresc").value) || 3.0);
    const reinvest = document.getElementById("sb-reinvestir").checked;

    // Extrai capital inicial atual se disponível
    let capitalInicial = 0;
    try {
      const patText = document.getElementById("an-patrimonio").innerText;
      const numClean = patText.replace(/[^0-9,-]+/g,"").replace(",",".");
      const parsed = parseFloat(numClean);
      if (!isNaN(parsed) && parsed > 0) capitalInicial = parsed;
    } catch(e) {}

    const payload = {
      valor_inicial: capitalInicial,
      aporte_mensal: aporte,
      anos: anos,
      dy_base: dy,
      crescimento_anual_proventos: growth,
      taxa_reinvestimento: reinvest ? 1.0 : 0.0
    };

    const res = await fetch("/api/snowball/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error("Falha ao simular bola de neve");
    const data = await res.json();
    const s = data.resumo || {};

    // Atualiza métricas resumo
    const crossingEl = document.getElementById("sb-crossing-val");
    const crossingSub = document.getElementById("sb-crossing-sub");
    if (s.ano_crossover) {
      crossingEl.innerText = `Ano ${s.ano_crossover}`;
      crossingSub.innerText = `Em ${s.ano_crossover} anos a renda passiva supera o aporte de ${safeFormatBRL(aporte)}/mês!`;
    } else {
      crossingEl.innerText = `> ${anos} anos`;
      crossingSub.innerText = `Proventos mensais ainda abaixo do aporte no período.`;
    }

    document.getElementById("sb-renda-val").innerText = safeFormatBRL(s.renda_mensal_final || 0);
    document.getElementById("sb-renda-sub").innerText = `Equivalente a ~ ${s.equivalente_salarios_minimos || 0} salários mínimos / mês`;

    document.getElementById("sb-patrimonio-val").innerText = safeFormatBRL(s.patrimonio_final || 0);
    document.getElementById("sb-patrimonio-sub").innerText = `Sem reinvestir proventos: ${safeFormatBRL(s.patrimonio_sem_reinvestimento || 0)}`;

    const multPercent = s.multiplicador_patrimonial ? Math.round((s.multiplicador_patrimonial - 1) * 100) : 0;
    document.getElementById("sb-mult-val").innerText = `+${multPercent}%`;
    document.getElementById("sb-mult-sub").innerText = `Total aportado: ${safeFormatBRL(s.total_aportado_bolso || 0)}`;

    // Renderiza Gráfico Chart.js
    renderSnowballChart(data.curva || []);
  } catch (err) {
    console.warn("Erro ao simular bola de neve:", err);
  }
}

function renderSnowballChart(curva) {
  const canvas = document.getElementById("chart-snowball");
  if (!canvas || typeof Chart === "undefined") return;
  const ctx = canvas.getContext("2d");

  const labels = curva.map(t => t.ano === 0 ? "Início" : `Ano ${t.ano}`);
  const dataComDrip = curva.map(t => t.patrimonio);
  const dataSemDrip = curva.map(t => t.sem_reinvestimento);
  const dataProventos = curva.map(t => t.provento_anual);

  if (chartSnowball) {
    chartSnowball.destroy();
  }

  chartSnowball = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Patrimônio com DRIP (Barsi)",
          data: dataComDrip,
          borderColor: "#10b981",
          backgroundColor: "rgba(16, 185, 129, 0.12)",
          fill: true,
          tension: 0.3,
          borderWidth: 3,
          yAxisID: "y"
        },
        {
          label: "Patrimônio sem Reinvestimento",
          data: dataSemDrip,
          borderColor: "#94a3b8",
          borderDash: [5, 5],
          fill: false,
          tension: 0.2,
          borderWidth: 2,
          yAxisID: "y"
        },
        {
          label: "Proventos Anuais Gerados",
          data: dataProventos,
          type: "bar",
          backgroundColor: "rgba(245, 158, 11, 0.45)",
          borderColor: "#f59e0b",
          borderWidth: 1,
          yAxisID: "y1"
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { color: "rgba(255, 255, 255, 0.05)" } },
        y: {
          type: "linear",
          display: true,
          position: "left",
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: {
            callback: function(v) {
              if (v >= 1000000) return "R$ " + (v / 1000000).toFixed(1) + "M";
              if (v >= 1000) return "R$ " + (v / 1000).toFixed(0) + "k";
              return "R$ " + v;
            }
          }
        },
        y1: {
          type: "linear",
          display: true,
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: {
            callback: function(v) {
              if (v >= 1000) return "R$ " + (v / 1000).toFixed(0) + "k/ano";
              return "R$ " + v;
            }
          }
        }
      },
      plugins: {
        legend: { position: "top", labels: { boxWidth: 12, color: "#cbd5e1" } },
        tooltip: {
          callbacks: {
            label: function(context) {
              return `${context.dataset.label}: ${safeFormatBRL(context.parsed.y)}`;
            }
          }
        }
      }
    }
  });
}

function initAnalytics() {
  carregarAnalytics();
  carregarArtefatos();
  setTimeout(executarSimulacaoBolaDeNeve, 500);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initAnalytics);
} else {
  initAnalytics();
}
