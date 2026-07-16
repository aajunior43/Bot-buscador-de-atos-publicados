/**
 * Admin page JS — tabs, agent status, fetch actions
 */
(function () {
  "use strict";

  var STORAGE_KEY = "admin_active_tab";

  function switchTab(name) {
    document.querySelectorAll(".admin-tab").forEach(function (t) {
      t.style.display = t.id === "tab-" + name ? "block" : "none";
    });
    document.querySelectorAll(".tab-btn").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    try { localStorage.setItem(STORAGE_KEY, name); } catch (e) {}
  }

  function initTabs() {
    var saved = "";
    try { saved = localStorage.getItem(STORAGE_KEY) || ""; } catch (e) {}
    var first = null;
    document.querySelectorAll(".tab-btn").forEach(function (btn) {
      var tabName = btn.dataset.tab;
      if (!first) first = tabName;
      btn.addEventListener("click", function () { switchTab(tabName); });
    });
    switchTab(saved && document.getElementById("tab-" + saved) ? saved : first);
  }

  function pollAgente() {
    var el = document.getElementById("agente-status");
    if (!el) return;
    fetch("/admin/api/agente/status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        el.textContent = d.ativo ? "Ativo (" + d.modo + ")" : "Inativo";
        el.className = "badge" + (d.ativo ? " badge-ia" : "");
      })
      .catch(function () {});
  }

  function setupForms() {
    document.querySelectorAll("[data-admin-action]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        var action = btn.dataset.adminAction;
        var confirmMsg = btn.dataset.confirm;
        if (confirmMsg && !confirm(confirmMsg)) return;
        btn.disabled = true;
        var original = btn.innerHTML;
        btn.innerHTML = "… processando";
        fetch(action, { method: "POST" })
          .then(function (r) {
            if (r.ok && btn.dataset.reload) { window.location.reload(); return; }
            return r.text();
          })
          .then(function (msg) {
            var out = document.getElementById("admin-result");
            if (out) out.textContent = msg || "OK";
            btn.innerHTML = original;
            btn.disabled = false;
          })
          .catch(function () {
            btn.innerHTML = original;
            btn.disabled = false;
          });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTabs();
    pollAgente();
    if (document.getElementById("agente-status")) setInterval(pollAgente, 10000);
    setupForms();
  });
})();
