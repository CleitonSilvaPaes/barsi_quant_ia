/**
 * Barsi Quant IA - Lógica dos Insights da IA (RLHF & Chain-of-Thought)
 */

let insightsData = [];
let filtroDecisao = 'TODOS';
let pendingFeedback = {};
let feedbackDebounceTimer = null;
const PAGE_SIZE = 9;
let currentPage = 0;

async function carregarInsights() {
  try {
    const res = await fetch("/api/insights");
    const data = await res.json();
    insightsData = data.items || [];

    document.getElementById("stat-concordancia").innerText = `${data.taxa_concordancia}%`;
    document.getElementById("stat-concordancia-sub").innerText = `${data.validados_count} de ${data.total_count} análises validadas`;
    document.getElementById("stat-total-insights").innerText = data.total_count;
    document.getElementById("stat-acumular").innerText = data.acumular_count;
    document.getElementById("stat-aguardar").innerText = data.aguardar_count;

    // P4: Restaura filtro persistido no localStorage
    try {
      const salvo = localStorage.getItem('insights_filter');
      if (salvo && ['TODOS', 'ACUMULAR', 'AGUARDAR'].includes(salvo)) {
        filtroDecisao = salvo;
      }
    } catch(e) {}
    atualizarBotoesFiltro(filtroDecisao);

    renderizarCards();
  } catch (err) {
    document.getElementById("cards-container").innerHTML = `<div style="color:var(--red);">Erro ao carregar insights: ${err}</div>`;
  }
}

function filtrarCards(decisao, btnEl) {
  filtroDecisao = decisao;
  currentPage = 0;
  try {
    localStorage.setItem('insights_filter', decisao);
  } catch(e) {}
  atualizarBotoesFiltro(decisao);
  renderizarCards();
}

function atualizarBotoesFiltro(decisao) {
  document.querySelectorAll('.actions-bar .btn-filter').forEach(b => {
    const d = b.getAttribute('data-decisao');
    if (d === decisao) {
      b.style.borderColor = 'var(--accent)';
      b.style.color = '#fff';
    } else {
      b.style.borderColor = 'var(--border)';
      b.style.color = 'var(--text-muted)';
    }
  });
}

function mudarPagina(delta) {
  currentPage += delta;
  renderizarCards();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderizarCards() {
  const container = document.getElementById("cards-container");
  const paginationEl = document.getElementById("pagination-controls");
  container.innerHTML = "";

  const filtrados = insightsData.filter(item => {
    if (filtroDecisao === 'TODOS') return true;
    return item.acao_sugerida === filtroDecisao;
  });

  if (filtrados.length === 0) {
    container.innerHTML = `<div style="color:var(--text-muted); grid-column: 1/-1; text-align:center; padding:2rem;">Nenhum insight encontrado para este filtro.</div>`;
    if (paginationEl) paginationEl.innerHTML = "";
    return;
  }

  const totalPages = Math.ceil(filtrados.length / PAGE_SIZE);
  if (currentPage >= totalPages) currentPage = totalPages - 1;
  if (currentPage < 0) currentPage = 0;

  const start = currentPage * PAGE_SIZE;
  const pageItems = filtrados.slice(start, start + PAGE_SIZE);

  pageItems.forEach(item => {
    const isAcumular = item.acao_sugerida === "ACUMULAR";
    const badgeClass = isAcumular ? "badge-acumular" : "badge-aguardar";
    const margemColor = item.margem >= 0 ? "var(--green)" : "var(--red)";

    const feedbackAtual = item.feedback_usuario || "";
    const isConcordo = feedbackAtual.toUpperCase().includes("CONCORDO");
    const isDiscordo = feedbackAtual.toUpperCase().includes("DISCORDO");

    const card = document.createElement("div");
    card.className = "card";
    card.style.margin = "0";
    card.style.display = "flex";
    card.style.flexDirection = "column";
    card.style.justifyContent = "space-between";

    card.innerHTML = `
      <div>
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.75rem;">
          <div>
            <strong style="font-size: 1.15rem; color: #fff;">${esc(item.ticker)}</strong>
            <div style="font-size: 0.78rem; color: var(--text-muted);">${esc(item.empresa || '')} • <span style="color:var(--accent);">${esc(item.setor || 'B.E.S.T.')}</span></div>
          </div>
          <span class="${esc(badgeClass)}" style="font-size: 0.8rem;">${esc(item.acao_sugerida)}</span>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; background: rgba(0,0,0,0.25); border-radius: 6px; padding: 0.6rem; margin-bottom: 1rem; text-align: center;">
          <div>
            <div style="font-size: 0.7rem; color: var(--text-muted);">COTAÇÃO</div>
            <div style="font-weight: 600;">R$ ${item.preco.toFixed(2)}</div>
          </div>
          <div>
            <div style="font-size: 0.7rem; color: var(--text-muted);">PREÇO TETO</div>
            <div style="font-weight: 600;">R$ ${item.teto.toFixed(2)}</div>
          </div>
          <div>
            <div style="font-size: 0.7rem; color: var(--text-muted);">MARGEM</div>
            <div style="font-weight: 700; color: ${margemColor};">${item.margem.toFixed(1)}%</div>
          </div>
        </div>

        <div style="margin-bottom: 1rem;">
          <div style="font-size: 0.72rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.25rem; font-weight: 700;">Raciocínio da IA (Chain-of-Thought):</div>
          <p style="font-size: 0.84rem; color: #cbd5e1; line-height: 1.45; background: #090d16; padding: 0.65rem 0.85rem; border-radius: 6px; border: 1px solid var(--border);">
            ${esc(item.insight_ia || 'Margem de segurança atrativa para acúmulo previdenciário com base no Método Barsi.')}
          </p>
        </div>
      </div>

      <div>
        <div style="font-size: 0.72rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.4rem; font-weight: 700;">
          Validação Humana (RLHF):
          ${feedbackAtual ? `<span style="color:var(--green); text-transform:none; font-weight:normal; margin-left:5px;">✓ Atual: ${esc(feedbackAtual)}</span>` : ''}
        </div>

        <div style="display: flex; gap: 0.5rem; margin-bottom: 0.5rem;">
          <button onclick="enviarFeedback('${esc(item.ticker)}', 'CONCORDO')" class="btn ${isConcordo ? 'btn-green' : 'btn-outline'}" style="flex:1; justify-content:center; font-size:0.8rem;">
            👍 Concordo
          </button>
          <button onclick="enviarFeedback('${esc(item.ticker)}', 'DISCORDO')" class="btn ${isDiscordo ? 'btn-amber' : 'btn-outline'}" style="flex:1; justify-content:center; font-size:0.8rem;">
            👎 Discordo
          </button>
        </div>

        <div style="display: flex; gap: 0.35rem;">
          <input type="text" id="custom-fb-${esc(item.ticker)}" class="tbl-input" placeholder="Justificativa ou nota personalizada..." value="${(feedbackAtual !== 'CONCORDO' && feedbackAtual !== 'DISCORDO') ? esc(feedbackAtual) : ''}" style="font-size:0.78rem;" />
          <button onclick="enviarCustomFeedback('${esc(item.ticker)}')" class="btn btn-outline" style="padding:0.25rem 0.6rem; font-size:0.75rem;">Salvar</button>
        </div>
      </div>
    `;

    container.appendChild(card);
  });

  if (paginationEl) {
    if (totalPages > 1) {
      paginationEl.innerHTML = `
        <button class="btn btn-outline" onclick="mudarPagina(-1)" ${currentPage === 0 ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>◀ Anterior</button>
        <span style="font-size: 0.85rem; color: var(--text-muted);">Página <strong>${currentPage + 1}</strong> de <strong>${totalPages}</strong> (${filtrados.length} análises)</span>
        <button class="btn btn-outline" onclick="mudarPagina(1)" ${currentPage >= totalPages - 1 ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>Próxima ▶</button>
      `;
    } else {
      paginationEl.innerHTML = "";
    }
  }
}

function enviarFeedback(ticker, voto) {
  // 1. Atualização Otimista na Interface
  pendingFeedback[ticker] = voto;
  const item = insightsData.find(i => i.ticker === ticker);
  if (item) {
    item.feedback_usuario = voto;
    renderizarCards();
  }
  showToast(`Gravado ${voto} para ${ticker} (salvando em lote...)`);

  // 2. Debounce de 2000ms para envio consolidado em batch
  if (feedbackDebounceTimer) clearTimeout(feedbackDebounceTimer);
  feedbackDebounceTimer = setTimeout(enviarBatchFeedback, 2000);
}

async function enviarBatchFeedback() {
  const tickers = Object.keys(pendingFeedback);
  if (tickers.length === 0) return;

  const payload = {
    items: tickers.map(t => ({ ticker: t, feedback: pendingFeedback[t] }))
  };
  pendingFeedback = {};

  try {
    const res = await fetch("/api/insights/feedback/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const out = await res.json();
    showToast(`✓ ${out.count} feedback(s) salvos com sucesso no SQLite!`, "success");
  } catch (err) {
    showToast("Erro ao sincronizar feedbacks em lote: " + err, "error");
  }
}

function enviarCustomFeedback(ticker) {
  const input = document.getElementById(`custom-fb-${ticker}`);
  const texto = (input.value || "").trim();
  if (!texto) return;
  enviarFeedback(ticker, texto);
}

window.addEventListener("beforeunload", () => {
  const tickers = Object.keys(pendingFeedback);
  if (tickers.length > 0) {
    const payload = {
      items: tickers.map(t => ({ ticker: t, feedback: pendingFeedback[t] }))
    };
    fetch("/api/insights/feedback/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true
    });
  }
});

async function executarSync() {
  try {
    showToast("Executando análise completa de mercado...");
    const res = await fetch("/api/run", { method: "POST" });
    await res.json();
    showToast("Novos insights gerados com sucesso!");
    carregarInsights();
  } catch (err) {
    showToast("Erro na análise: " + err, "error");
  }
}

async function carregarAlertasTese() {
  const section = document.getElementById("thesis-alerts-section");
  const listEl = document.getElementById("thesis-alerts-list");
  if (!section || !listEl) return;

  try {
    const res = await fetch("/api/early-warning/alerts");
    if (!res.ok) return;
    const data = await res.json();
    const alerts = data.alerts || [];

    if (alerts.length === 0) {
      section.style.display = "none";
      listEl.innerHTML = "";
      return;
    }

    section.style.display = "block";
    listEl.innerHTML = "";

    alerts.forEach(alert => {
      const isCritical = alert.severity === "CRITICAL";
      const borderCol = isCritical ? "rgba(239, 68, 68, 0.4)" : "rgba(245, 158, 11, 0.4)";
      const bgCol = isCritical ? "rgba(239, 68, 68, 0.12)" : "rgba(245, 158, 11, 0.12)";
      const badgeCol = isCritical ? "#ef4444" : "#f59e0b";

      const item = document.createElement("div");
      item.style.cssText = `background: ${bgCol}; border: 1px solid ${borderCol}; border-radius: 6px; padding: 0.85rem; display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem;`;
      item.innerHTML = `
        <div style="flex: 1;">
          <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; flex-wrap: wrap;">
            <strong style="color: #fff; font-size: 0.95rem;">${alert.ticker}</strong>
            <span style="background: ${badgeCol}; color: #000; font-size: 0.7rem; font-weight: 700; padding: 0.15rem 0.4rem; border-radius: 4px;">
              ${alert.severity} • ${alert.category.replace('_', ' ')}
            </span>
            <span style="font-size: 0.75rem; color: var(--text-muted);">
              Detectado em ${new Date(alert.detected_at).toLocaleDateString()}
            </span>
          </div>
          <div style="font-size: 0.85rem; color: #fff; margin-bottom: 0.35rem;">
            ${alert.news_title}
          </div>
          <div style="font-size: 0.78rem; color: var(--text-muted); font-style: italic;">
            "${alert.snippet}"
          </div>
          ${alert.news_url ? `<div style="margin-top: 0.3rem;"><a href="${alert.news_url}" target="_blank" rel="noopener noreferrer" style="font-size: 0.75rem; color: var(--accent);">🔗 Ler notícia na fonte</a></div>` : ''}
        </div>
        <button class="btn btn-outline" style="font-size: 0.75rem; padding: 0.25rem 0.5rem; border-color: var(--border);" onclick="dispensarAlertaTese(${alert.id})">
          Dispensar
        </button>
      `;
      listEl.appendChild(item);
    });
  } catch (e) {
    console.warn("Erro ao buscar alertas de tese:", e);
  }
}

async function escanearAlertasTese() {
  const btn = document.getElementById("btn-scan-alerts");
  if (btn) {
    btn.disabled = true;
    btn.innerText = "⏳ Escaneando...";
  }
  try {
    showToast("Iniciando varredura de notícias via Google News RSS...");
    const res = await fetch("/api/early-warning/scan", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Erro na varredura");

    if (data.alerts_found > 0) {
      showToast(`⚠️ Atenção: ${data.alerts_found} alerta(s) de risco identificados!`, "error");
    } else {
      showToast("✓ Nenhuma ameaça de tese detectada nas notícias recentes.", "success");
    }
    await carregarAlertasTese();
  } catch (err) {
    showToast("Erro ao escanear notícias: " + err.message, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = "🔍 Escanear Notícias Agora";
    }
  }
}

async function dispensarAlertaTese(id) {
  try {
    const res = await fetch(`/api/early-warning/${id}/dismiss`, { method: "POST" });
    if (!res.ok) throw new Error("Erro ao dispensar alerta");
    showToast("Alerta dispensado.");
    await carregarAlertasTese();
  } catch (e) {
    showToast(e.message, "error");
  }
}

window.addEventListener("DOMContentLoaded", () => {
  carregarInsights();
  carregarAlertasTese();
});

