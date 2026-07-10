/**
 * Monitor Inajá — JS global (shell, toast, processamento ao vivo)
 */
(function () {
  "use strict";

  window.showToast = function (msg) {
    let t = document.getElementById("global-toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "global-toast";
      t.className = "global-toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("visible");
    setTimeout(function () {
      t.classList.remove("visible");
    }, 4200);
  };

  window.parseProgress = function (input) {
    if (!input) return null;
    var msg =
      typeof input === "string" ? input : input.msg || JSON.stringify(input);
    var cur = input.current || input.progress_current || null;
    var tot = input.total || input.progress_total || null;
    var step = input.step || input.progress_step || "ocr";

    if (cur == null || tot == null) {
      var m = String(msg).match(/Página\s+(\d+)\s*\/\s*(\d+)/i);
      if (m) {
        cur = parseInt(m[1], 10);
        tot = parseInt(m[2], 10);
      }
    }
    var pct = cur != null && tot ? Math.round((cur / tot) * 100) : 0;

    if (!step || step === "unknown") {
      if (/download|pdf|baixando/i.test(msg)) step = "download";
      else if (/ocr|rapido|estruturado|complet/i.test(msg)) step = "ocr";
      else if (/detect|publicaç|menç/i.test(msg)) step = "detect";
      else if (/ia|refin/i.test(msg)) step = "ia";
    }
    return { current: cur, total: tot, percent: pct, step: step, raw: msg };
  };

  window.formatProgressInfo = function (prog, startTime) {
    if (!prog || !prog.current || !prog.total) return "";
    var now = Date.now();
    var elapsed = startTime ? Math.round((now - startTime) / 1000) : 0;
    var eta = "";
    if (elapsed > 3 && prog.percent > 5) {
      var rate = prog.current / (elapsed / 60);
      var remaining = Math.max(0, prog.total - prog.current);
      var etaMin = Math.round(remaining / rate);
      eta = " · ETA ~" + etaMin + "min";
    }
    return (
      prog.current +
      "/" +
      prog.total +
      " (" +
      prog.percent +
      "%)" +
      (elapsed ? " · " + elapsed + "s" : "") +
      eta
    );
  };

  window.startProcessing = function (form, loadingText, edicaoIdHint) {
    var btn = form.querySelector("button");
    if (!btn) return true;
    if (btn.disabled) return false;
    btn.disabled = true;
    var originalText = btn.innerHTML;
    btn.innerHTML = "… " + loadingText;

    var action = form.getAttribute("action");
    if (action && window.fetch) {
      fetch(action, {
        method: form.method || "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
      })
        .then(function (r) {
          if (r.ok) {
            if (window.showToast)
              window.showToast("Processamento iniciado. Acompanhe na Fila.");
            var container = form.closest(".actions") || form.parentNode;
            var monId = "live-monitor-" + (edicaoIdHint || "global");
            var monContainer = document.getElementById(monId);
            if (!monContainer) {
              monContainer = document.createElement("div");
              monContainer.id = monId;
              container.appendChild(monContainer);
            }
            var eid = edicaoIdHint;
            if (!eid) {
              var m = window.location.pathname.match(/\/edicoes\/(\d+)/);
              if (m) eid = parseInt(m[1], 10);
            }
            if (eid && window.createLiveMonitor) {
              window.createLiveMonitor(eid, monContainer, [
                {
                  etapa: loadingText,
                  status: "rodando",
                  mensagem: "Iniciado...",
                },
              ]);
            } else {
              window.location.href = "/status";
            }
          } else {
            btn.innerHTML = originalText;
            btn.disabled = false;
            if (window.showToast)
              window.showToast("Falha ao iniciar. Ver Fila.");
          }
        })
        .catch(function () {
          btn.innerHTML = originalText;
          btn.disabled = false;
          if (window.showToast)
            window.showToast("Erro de rede. Tente novamente.");
        });
      return false;
    }

    if (window.showToast)
      window.showToast("Processamento iniciado. Acompanhe na Fila.");
    return true;
  };

  window.createLiveMonitor = function (edicaoId, container, initialJobs) {
    if (!container) return;
    container.innerHTML = "";
    var card = document.createElement("div");
    card.className = "live-monitor-card panel fade-in";
    card.innerHTML =
      '<div class="panel-head">' +
      "<h3>Processamento ao vivo <small>(Edição " +
      edicaoId +
      ")</small></h3>" +
      '<button type="button" class="btn btn-secondary btn-small" data-close-monitor>Fechar</button>' +
      "</div>" +
      '<div class="pipeline">' +
      '<div class="pipeline-step" data-step="download"><span class="step-icon">1</span> Download <span class="step-status"></span></div>' +
      '<div class="pipeline-step" data-step="ocr"><span class="step-icon">2</span> OCR <span class="step-status"></span>' +
      '<div class="sub-progress"><progress value="0" max="100"></progress><small class="page-info"></small></div></div>' +
      '<div class="pipeline-step" data-step="detect"><span class="step-icon">3</span> Detecção <span class="step-status"></span></div>' +
      '<div class="pipeline-step" data-step="ia"><span class="step-icon">4</span> IA <span class="step-status"></span></div>' +
      "</div>" +
      '<div class="live-log"><strong>Log recente</strong><ul class="log-list"></ul></div>' +
      '<div class="live-status"><small>Atualizando…</small></div>';
    container.appendChild(card);
    card._startTime = Date.now();

    var closeBtn = card.querySelector("[data-close-monitor]");
    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        card.remove();
      });
    }

    var updateUI = function (jobs) {
      if (!jobs || !jobs.length) return;
      var latest = jobs[0];
      var logList = card.querySelector(".log-list");
      logList.innerHTML = "";
      jobs
        .slice(0, 5)
        .reverse()
        .forEach(function (j) {
          if (j.mensagem) {
            var li = document.createElement("li");
            li.textContent = j.etapa + ": " + j.mensagem;
            logList.appendChild(li);
          }
        });

      card.querySelectorAll(".pipeline-step").forEach(function (s) {
        s.classList.remove("active", "done", "error");
      });
      var prog = window.parseProgress(latest);
      if (prog) {
        var stepEl = card.querySelector('[data-step="' + prog.step + '"]');
        if (stepEl) {
          var isDone = latest.status === "concluido";
          var isErr = latest.status === "erro";
          stepEl.classList.add(isErr ? "error" : isDone ? "done" : "active");
          if (prog.step === "ocr" || String(prog.step).indexOf("ocr") >= 0) {
            var p = stepEl.querySelector("progress");
            var info = stepEl.querySelector(".page-info");
            var c = prog.current || latest.progress_current || 0;
            var t = prog.total || latest.progress_total || 100;
            if (p) {
              p.value = c;
              p.max = t;
            }
            if (info)
              info.textContent = window.formatProgressInfo(
                { current: c, total: t, percent: prog.percent },
                card._startTime
              );
          } else {
            var statusSpan = stepEl.querySelector(".step-status");
            if (statusSpan)
              statusSpan.textContent = isDone ? "✓" : isErr ? "✗" : "…";
          }
        }
      }
      var statusEl = card.querySelector(".live-status small");
      var infoTxt =
        latest.status === "rodando"
          ? "Rodando: " + latest.etapa
          : "Status: " + latest.status;
      if (latest.mensagem) infoTxt += " — " + latest.mensagem;
      statusEl.textContent = infoTxt;
    };

    if (initialJobs) updateUI(initialJobs);

    var poll = setInterval(function () {
      fetch("/api/edicoes/" + edicaoId + "/live-status")
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.jobs) updateUI(data.jobs);
          if (!data.has_running) {
            clearInterval(poll);
            var s = card.querySelector(".live-status small");
            if (s) s.textContent = "Processamento concluído.";
            setTimeout(function () {
              if (card.parentNode) card.parentNode.removeChild(card);
            }, 3200);
          }
        })
        .catch(function () {});
    }, 2000);

    if (window.EventSource && !card._sseBound) {
      card._sseBound = true;
      try {
        var sse = new EventSource("/api/eventos");
        sse.onmessage = function (ev) {
          try {
            var d = JSON.parse(ev.data);
            var match = (d.rodando || []).find(function (j) {
              return String(j.edicao_id) === String(edicaoId);
            });
            if (match)
              updateUI([match].concat(d.rodando || []).slice(0, 5));
          } catch (e) {}
        };
        var obs = new MutationObserver(function () {
          if (!card.parentNode) {
            sse.close();
            obs.disconnect();
          }
        });
        obs.observe(card.parentNode || document.body, {
          childList: true,
          subtree: true,
        });
      } catch (e) {}
    }

    return card;
  };

  function atualizarHeaderProcessing(rodandoCount) {
    var el = document.getElementById("global-processing");
    if (!el) return;
    el.style.display = rodandoCount > 0 ? "inline-flex" : "none";
    el.title = rodandoCount + " etapa(s) em execução";
  }

  function initGlobalActivity() {
    if (!window.EventSource) return;
    try {
      var src = new EventSource("/api/eventos");
      src.onmessage = function (ev) {
        try {
          var data = JSON.parse(ev.data);
          var count = (data.rodando && data.rodando.length) || 0;
          atualizarHeaderProcessing(count);
        } catch (e) {}
      };
    } catch (e) {}
  }

  function initMobileNav() {
    var toggle = document.getElementById("nav-toggle");
    var nav = document.getElementById("main-nav");
    if (!toggle || !nav) return;
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  /** Chips leves no topo: BOT vivo + fila pendente (sem poluir o menu). */
  function initNavLiveChips() {
    var host = document.getElementById("nav-live-chips");
    if (!host || !window.fetch) return;

    function chip(label, cls, title) {
      return (
        '<span class="nav-chip ' +
        (cls || "") +
        '" title="' +
        (title || "") +
        '">' +
        label +
        "</span>"
      );
    }

    function refresh() {
      fetch("/api/automacao", { headers: { Accept: "application/json" } })
        .then(function (r) {
          return r.ok ? r.json() : null;
        })
        .then(function (st) {
          if (!st) return;
          var parts = [];
          if (st.bot_vivo) {
            parts.push(chip("BOT", "is-on", "Bot de processamento ativo"));
          } else {
            parts.push(
              chip("BOT off", "is-off", "Bot parado — use o menu [1] ou [3]")
            );
          }
          var pend = st.pendentes_ocr;
          if (pend != null) {
            parts.push(
              chip(
                pend + " fila",
                pend > 100 ? "is-warn" : pend > 0 ? "" : "is-on",
                pend + " edição(ões) pendente(s) de OCR"
              )
            );
          }
          host.innerHTML = parts.join("");
        })
        .catch(function () {});
    }

    refresh();
    setInterval(refresh, 60000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initGlobalActivity();
    initMobileNav();
    initNavLiveChips();
  });
})();

