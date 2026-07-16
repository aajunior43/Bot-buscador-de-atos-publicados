/**
 * Markdown renderer — simple, safe, shared between pages
 */
(function () {
  "use strict";

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
  }

  window.renderMarkdown = function (md) {
    if (!md) return "";
    var html = escapeHtml(md);
    // headings
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    // bold / italic
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    // inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // unordered lists
    html = html.replace(/^[\s]*[-*] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
    // numbered lists
    html = html.replace(/^[\s]*\d+\. (.+)$/gm, "<li>$1</li>");
    // paragraphs (double newline)
    html = html.replace(/\n\n/g, "</p><p>");
    html = "<p>" + html + "</p>";
    // cleanup nested paragraphs inside lists
    html = html.replace(/<li><p>/g, "<li>").replace(/<\/p><\/li>/g, "</li>");
    // line breaks within paragraphs
    html = html.replace(/\n/g, "<br>");
    return html;
  };
})();
