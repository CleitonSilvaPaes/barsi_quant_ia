/**
 * Barsi Quant IA - Scripts Comuns & Helpers Globais
 */

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
