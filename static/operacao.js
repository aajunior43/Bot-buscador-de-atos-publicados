/**
 * Página /operacao — gráficos e SSE de atividade
 */
(function () {
  "use strict";

  if (window.Chart) {
    Chart.defaults.color = "#a0a8c0";
    Chart.defaults.borderColor = "rgba(255,255,255,0.06)";
    Chart.defaults.font.family = "'Inter', sans-serif";
  }

  function carregarGraficoPorMes() {
    if (!window.Chart) return;
    fetch("/api/graficos/por-mes")
      .then(function (r) {
        return r.json();
      })
      .then(function (dados) {
        var ctx = document.getElementById("chart-por-mes");
        if (!ctx) return;
        new Chart(ctx, {
          type: "line",
          data: {
            labels: dados.map(function (d) {
              return d.mes;
            }),
            datasets: [
              {
                label: "Total",
                data: dados.map(function (d) {
                  return d.total;
                }),
                borderColor: "#6366f1",
                backgroundColor: "rgba(99,102,241,0.08)",
                tension: 0.4,
                fill: true,
                pointRadius: 3,
              },
              {
                label: "Com Inajá",
                data: dados.map(function (d) {
                  return d.com_inaja;
                }),
                borderColor: "#22d3a5",
                backgroundColor: "rgba(34,211,165,0.08)",
                tension: 0.4,
                fill: true,
                pointRadius: 3,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                position: "bottom",
                labels: { boxWidth: 10, padding: 10, font: { size: 11 } },
              },
            },
            scales: {
              y: { beginAtZero: true },
            },
          },
        });
      })
      .catch(function () {});
  }

  function carregarGraficoPorTipo() {
    if (!window.Chart) return;
    fetch("/api/graficos/por-tipo")
      .then(function (r) {
        return r.json();
      })
      .then(function (dados) {
        var ctx = document.getElementById("chart-por-tipo");
        if (!ctx) return;
        var cores = [
          "#6366f1",
          "#22d3a5",
          "#fbbf24",
          "#f87171",
          "#38bdf8",
          "#a78bfa",
          "#34d399",
          "#fb923c",
        ];
        new Chart(ctx, {
          type: "doughnut",
          data: {
            labels: dados.map(function (d) {
              return d.tipo;
            }),
            datasets: [
              {
                data: dados.map(function (d) {
                  return d.total;
                }),
                backgroundColor: cores,
                borderColor: "#13161e",
                borderWidth: 2,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                position: "bottom",
                labels: { boxWidth: 10, padding: 10, font: { size: 11 } },
              },
            },
            cutout: "68%",
          },
        });
      })
      .catch(function () {});
  }

  function escapeHtml(v) {
    return String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderJob(job) {
    return (
      '<article class="activity-item status-' +
      escapeHtml(job.status) +
      '"><span class="status-dot"></span><div><strong>' +
      escapeHtml(job.etapa) +
      "</strong><small>" +
      escapeHtml(job.edicao_titulo || job.titulo || "Processo geral") +
      " · " +
      escapeHtml(job.atualizado_em) +
      "</small>" +
      (job.mensagem ? "<p>" + escapeHtml(job.mensagem) + "</p>" : "") +
      "</div></article>"
    );
  }

  function conectarSSE() {
    if (!window.EventSource) return;
    var source = new EventSource("/api/eventos");
    source.onmessage = function (event) {
      try {
        var data = JSON.parse(event.data);
        var state = document.getElementById("activity-state");
        var title = document.getElementById("activity-title");
        var message = document.getElementById("activity-message");
        var running = document.getElementById("running-list");
        var refresh = document.getElementById("activity-refresh");
        var countEl = document.getElementById("ops-jobs-count");
        if (!state) return;
        state.className =
          "activity-state " + (data.tem_atividade ? "is-running" : "is-idle");
        title.textContent = data.tem_atividade
          ? "Executando " + data.rodando.length + " etapa(s)"
          : "Sistema aguardando";
        message.textContent = data.tem_atividade
          ? "Processamento em andamento."
          : "Aguardando próximo ciclo do BOT ou ação manual.";
        running.innerHTML = data.rodando.length
          ? data.rodando.map(renderJob).join("")
          : '<p class="empty">Nada rodando agora.</p>';
        if (refresh)
          refresh.textContent = new Date().toLocaleTimeString("pt-BR");
        if (countEl) countEl.textContent = String(data.rodando.length || 0);
      } catch (e) {}
    };
  }

  function atualizarAutomacao() {
    fetch("/api/automacao", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (a) {
        function setText(id, val) {
          var el = document.getElementById(id);
          if (el) el.textContent = val == null || val === "" ? "—" : String(val);
        }
        setText("ciclo-web-ultimo", a.web_ultimo_br);
        setText("ciclo-web-proxima", a.web_proxima_br);
        setText("ciclo-web-msg", a.web_mensagem || "—");
        setText("ciclo-bot-ultimo", a.bot_ultimo_br);
        setText("ciclo-bot-proxima", a.bot_proxima_br);
        setText("ciclo-bot-msg", a.bot_mensagem || "—");
        setText("ciclo-pendentes", a.pendentes_ocr);
        setText("ciclo-fila", a.fila_proximo_ciclo);
        var refresh = document.getElementById("ciclo-refresh");
        if (refresh)
          refresh.textContent =
            "atualizado " + new Date().toLocaleTimeString("pt-BR");
      })
      .catch(function () {});
  }

  carregarGraficoPorMes();
  carregarGraficoPorTipo();
  conectarSSE();
  atualizarAutomacao();
  setInterval(atualizarAutomacao, 30000);
})();
