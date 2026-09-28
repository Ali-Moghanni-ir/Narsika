/* Minimal, escape-first Markdown for assistant answers. No raw HTML is ever emitted from model text. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const inline = value => escape(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
  const cells = line => line.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());

  function render(source) {
    const lines = String(source ?? '').replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (/^```/.test(line)) {
        const block = [];
        i += 1;
        while (i < lines.length && !/^```/.test(lines[i])) {
          block.push(lines[i]);
          i += 1;
        }
        i += 1;
        out.push(`<pre dir="ltr"><code>${escape(block.join('\n'))}</code></pre>`);
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        out.push(`<${heading[1].length <= 2 ? 'h3' : 'h4'}>${inline(heading[2])}</${heading[1].length <= 2 ? 'h3' : 'h4'}>`);
        i += 1;
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || '')) {
        const head = cells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          rows.push(cells(lines[i]));
          i += 1;
        }
        out.push(`<table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
        continue;
      }
      const bullet = /^\s*[-*•]\s+/;
      const ordered = /^\s*\d+[.)]\s+/;
      if (bullet.test(line) || ordered.test(line)) {
        const pattern = bullet.test(line) ? bullet : ordered;
        const tag = pattern === bullet ? 'ul' : 'ol';
        const items = [];
        while (i < lines.length && pattern.test(lines[i])) {
          items.push(`<li>${inline(lines[i].replace(pattern, ''))}</li>`);
          i += 1;
        }
        out.push(`<${tag}>${items.join('')}</${tag}>`);
        continue;
      }
      if (!line.trim()) {
        i += 1;
        continue;
      }
      const paragraph = [];
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*[-*•]\s|\s*\d+[.)]\s|\s*\|)/.test(lines[i])) {
        paragraph.push(inline(lines[i]));
        i += 1;
      }
      if (!paragraph.length) {
        paragraph.push(inline(line));
        i += 1;
      }
      out.push(`<p>${paragraph.join('<br>')}</p>`);
    }
    return out.join('');
  }

  window.NarsikaMarkdown = {render, escape};
})();
