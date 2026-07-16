/**
 * Global search overlay — Ctrl+K / Cmd+K
 */
(function () {
  "use strict";

  var overlay, input, results;

  function createOverlay() {
    if (overlay) return;
    overlay = document.createElement("div");
    overlay.className = "search-overlay";
    overlay.id = "global-search-overlay";
    overlay.innerHTML =
      '<div class="search-card">' +
      '<label for="global-search-input">Buscar publicações e edições</label>' +
      '<div class="search-row">' +
      '<input type="text" id="global-search-input" class="search-input" placeholder="Digite para buscar…" autocomplete="off">' +
      '<button type="button" class="btn btn-secondary btn-small" data-close-search>Cancelar</button>' +
      "</div>" +
      '<div class="search-results" id="global-search-results"></div>' +
      '<small style="display:block;margin-top:.5rem;color:var(--text-3);font-size:.72rem">' +
      "Enter para buscar · Esc para fechar</small>" +
      "</div>";
    document.body.appendChild(overlay);
    input = document.getElementById("global-search-input");
    results = document.getElementById("global-search-results");

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });
    overlay.querySelector("[data-close-search]").addEventListener("click", close);

    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
      if (e.key === "Enter") doSearch(input.value);
    });

    var debounceTimer;
    input.addEventListener("input", function () {
      clearTimeout(debounceTimer);
      var q = input.value.trim();
      if (q.length < 2) { results.innerHTML = ""; return; }
      debounceTimer = setTimeout(function () { doSearch(q); }, 300);
    });
  }

  function doSearch(q) {
    if (!q || q.length < 2) { results.innerHTML = ""; return; }
    results.innerHTML = "<div style='padding:.5rem;color:var(--text-3)'>Buscando…</div>";
    fetch("/api/busca?q=" + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || (!data.publicacoes && !data.edicoes)) {
          results.innerHTML = "<div style='padding:.5rem;color:var(--text-3)'>Nenhum resultado</div>";
          return;
        }
        var html = "";
        if (data.edicoes && data.edicoes.length) {
          html += "<div style='font-size:.72rem;color:var(--text-3);margin-bottom:.25rem'>Edições</div>";
          data.edicoes.slice(0, 5).forEach(function (e) {
            html += '<a href="/edicoes/' + e.id + '" class="search-result-item">' +
              '<strong>' + (e.titulo || "Edição " + e.id) + "</strong>" +
              (e.data_publicacao ? "<small>" + e.data_publicacao + "</small>" : "") +
              "</a>";
          });
        }
        if (data.publicacoes && data.publicacoes.length) {
          html += "<div style='font-size:.72rem;color:var(--text-3);margin-top:.5rem;margin-bottom:.25rem'>Publicações</div>";
          data.publicacoes.slice(0, 10).forEach(function (p) {
            html += '<a href="/edicoes/' + p.edicao_id + '" class="search-result-item">' +
              "<strong>" + (p.tipo || "Ato") + " " + (p.numero || "") + "</strong>" +
              (p.assunto ? "<small>" + p.assunto.slice(0, 80) + "</small>" : "") +
              "</a>";
          });
        }
        results.innerHTML = html;
      })
      .catch(function () {
        results.innerHTML = "<div style='padding:.5rem;color:var(--text-3)'>Erro ao buscar</div>";
      });
  }

  function open() {
    createOverlay();
    overlay.classList.add("is-open");
    setTimeout(function () { input && input.focus(); }, 100);
  }

  function close() {
    if (overlay) overlay.classList.remove("is-open");
    if (input) input.value = "";
    if (results) results.innerHTML = "";
  }

  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
      e.preventDefault();
      open();
    }
    if (e.key === "Escape" && overlay && overlay.classList.contains("is-open")) {
      close();
    }
  });
})();
