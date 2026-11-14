/**
 * Barsi Quant IA - Scripts Comuns & Helpers Globais
 */

/**
 * Retorna o token CSRF atual obtido da meta tag ou do cookie barsi_csrf.
 */
function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content && meta.content !== '{{CSRF_TOKEN}}') {
    return meta.content;
  }
  const match = document.cookie.match(/(?:^|;\s*)barsi_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

/**
 * Interceptor global do Fetch: anexa automaticamente o cabeçalho X-CSRF-Token
 * em requisições mutantes (POST, PUT, DELETE, PATCH).
 */
(function() {
  const _origFetch = window.fetch;
  window.fetch = function(url, options = {}) {
    const opts = Object.assign({}, options);
    const method = (opts.method || 'GET').toUpperCase();
    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
      const token = getCsrfToken();
      if (token) {
        if (!opts.headers) {
          opts.headers = {};
        }
        if (opts.headers instanceof Headers) {
          if (!opts.headers.has('X-CSRF-Token')) {
            opts.headers.set('X-CSRF-Token', token);
          }
        } else if (Array.isArray(opts.headers)) {
          const exists = opts.headers.some(h => h[0].toLowerCase() === 'x-csrf-token');
          if (!exists) opts.headers.push(['X-CSRF-Token', token]);
        } else {
          if (!opts.headers['X-CSRF-Token'] && !opts.headers['x-csrf-token']) {
            opts.headers['X-CSRF-Token'] = token;
          }
        }
      }
    }
    return _origFetch(url, opts);
  };
})();

function showToast(msg, type = "success") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type === 'success' ? 'toast-success' : 'toast-error'}`;
  toast.innerHTML = `<span>${type === 'success' ? '✅' : '❌'}</span> <span>${msg}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function formatBRL(val) {
  return (val || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatPct(val) {
  return ((val || 0)).toFixed(1) + '%';
}

function toggleSidebar() {
  const sidebar = document.querySelector(".sidebar");
  const overlay = document.querySelector(".sidebar-overlay");
  if (sidebar && overlay) {
    sidebar.classList.toggle("open");
    overlay.classList.toggle("active");
  }
}

function renderDynamicArtifactChart(canvasId, art, data, chartInstances) {
  const canvasEl = document.getElementById(canvasId);
  if (!canvasEl) return null;
  const ctx = canvasEl.getContext("2d");

  if (chartInstances && chartInstances[art.id]) {
    try { chartInstances[art.id].destroy(); } catch (e) {}
  }

  const isMobile = window.innerWidth < 600;
  const palette = [
    '#38bdf8', '#22c55e', '#f59e0b', '#ec4899', '#8b5cf6',
    '#2563eb', '#14b8a6', '#f97316', '#a855f7', '#06b6d4'
  ];

  const chartType = art.chart_type || 'bar';
  let datasets = [];

  if (chartType === 'doughnut' || chartType === 'pie') {
    const bgColors = (data.labels || []).map((_, i) => palette[i % palette.length]);
    datasets = [{
      data: data.values || [],
      backgroundColor: bgColors,
      borderColor: '#111827',
      borderWidth: 2
    }];
  } else {
    datasets = (data.datasets && data.datasets.length) ? data.datasets.map((ds, idx) => {
      const color = palette[idx % palette.length];
      return {
        label: ds.label,
        data: ds.data,
        borderColor: color,
        backgroundColor: chartType === 'line' ? 'rgba(56, 189, 248, 0.12)' : color,
        fill: chartType === 'line',
        tension: 0.35,
        borderWidth: chartType === 'line' ? 2 : 1,
        borderRadius: chartType === 'bar' ? 4 : 0,
        pointRadius: chartType === 'line' ? (isMobile ? 3 : 4) : 0
      };
    }) : [{
      label: art.name,
      data: data.values || [],
      borderColor: '#38bdf8',
      backgroundColor: '#38bdf8',
      borderRadius: 4
    }];
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: chartType !== 'bar' || (data.datasets && data.datasets.length > 1),
        position: (chartType === 'doughnut' || chartType === 'pie') ? 'bottom' : 'top',
        labels: {
          color: '#cbd5e1',
          font: { size: isMobile ? 9 : 11 },
          boxWidth: isMobile ? 10 : 12,
          padding: isMobile ? 6 : 10
        }
      },
      tooltip: {
        mode: (chartType === 'doughnut' || chartType === 'pie') ? 'nearest' : 'index',
        intersect: chartType === 'doughnut' || chartType === 'pie'
      }
    }
  };

  if (chartType === 'line' || chartType === 'bar') {
    options.scales = {
      x: {
        grid: { color: '#1e293b' },
        ticks: { color: '#94a3b8', font: { size: isMobile ? 8 : 10 }, maxRotation: 45 }
      },
      y: {
        grid: { color: '#1e293b' },
        ticks: { color: '#94a3b8', font: { size: isMobile ? 8 : 10 } }
      }
    };
  }

  const instance = new Chart(ctx, {
    type: chartType,
    data: {
      labels: data.labels || [],
      datasets: datasets
    },
    options: options
  });

  if (chartInstances) {
    chartInstances[art.id] = instance;
  }
  return instance;
}

/**
 * Controle de Tema Claro / Escuro (Roadmap v3.0 Sprint 1)
 */
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try {
    localStorage.setItem('barsi_theme', next);
  } catch (e) {}
  updateThemeIcon(next);
}

function updateThemeIcon(theme) {
  const icon = document.getElementById('theme-icon');
  if (icon) {
    icon.textContent = theme === 'light' ? '☀️' : '🌙';
  }
}

/**
 * Suporte a Progressive Web App (PWA) e Registro do Service Worker (Sprint 4)
 */
let deferredPwaPrompt = null;

function registrarServiceWorker() {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
        .then(reg => {
          console.log('✓ Barsi PWA Service Worker registrado com sucesso no escopo:', reg.scope);
        })
        .catch(err => {
          console.warn('Falha ao registrar Service Worker:', err);
        });
    });
  }
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPwaPrompt = e;
  const btn = document.getElementById('pwa-install-btn');
  if (btn) {
    btn.style.display = 'inline-flex';
  }
});

async function instalarPWA() {
  if (!deferredPwaPrompt) {
    showToast("Para instalar o app, utilize a opção 'Adicionar à tela inicial' no menu do seu navegador.");
    return;
  }
  deferredPwaPrompt.prompt();
  const choice = await deferredPwaPrompt.userChoice;
  if (choice.outcome === 'accepted') {
    showToast("✓ Aplicativo Barsi Quant IA instalado com sucesso!", "success");
    const btn = document.getElementById('pwa-install-btn');
    if (btn) btn.style.display = 'none';
  }
  deferredPwaPrompt = null;
}

window.addEventListener('appinstalled', () => {
  console.log('Barsi Quant IA instalado como PWA.');
  const btn = document.getElementById('pwa-install-btn');
  if (btn) btn.style.display = 'none';
  deferredPwaPrompt = null;
});

document.addEventListener('DOMContentLoaded', () => {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  updateThemeIcon(current);
  registrarServiceWorker();
});


