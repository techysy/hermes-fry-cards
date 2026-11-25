/* CardKit JSON → DOM 轻量渲染器 + markdown 子集（预览专用） */
(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* 取文本：i18n_content 按 Studio 界面语言优先（中 zh_cn / 英 en_us） */
  function pickText(el) {
    if (!el) return "";
    var uiEn = !!(window.FryI18n && window.FryI18n.getLang() === "en");
    if (el.i18n_content) {
      return (uiEn ? el.i18n_content.en_us || el.i18n_content.zh_cn
                   : el.i18n_content.zh_cn || el.i18n_content.en_us) || el.content || "";
    }
    return el.content || "";
  }

  /* 行内 markdown（入参已完成 HTML 转义） */
  function inlineMd(s) {
    // font 标签经 escapeHtml 后单引号可能仍为 ' 或已变 &#39;，两种形态都要匹配
    s = s.replace(
      /&lt;font color=(?:'|&#39;)([a-z]+)(?:'|&#39;)&gt;([\s\S]*?)&lt;\/font&gt;/g,
      function (m, c, t) { return '<span class="fc-' + c + '">' + t + "</span>"; },
    );
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    s = s.replace(/!\[([^\]]*)\]\(([^)]*)\)/g, function (m, alt, url) {
      // 预览用厂商 logo（Studio 本地静态 PNG）直接出图，其余图片仍按占位芯片显示
      var src = String(url || "");
      if (/^\/assets\/vendor-logos\/[\w.-]+$/.test(src)) {
        return '<img class="vlogo" src="' + src + '" alt="' + (alt || "") + '" title="' + src + '">';
      }
      return '<span class="mdimg">🖼 ' + (alt || "image") + "</span>";
    });
    s = s.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
    return s;
  }

  function splitCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (x) { return x.trim(); });
  }

  function buildTable(head, rows) {
    var th = splitCells(head).map(function (c) { return "<th>" + inlineMd(c) + "</th>"; }).join("");
    var tr = rows.map(function (r) {
      return "<tr>" + splitCells(r).map(function (c) { return "<td>" + inlineMd(c) + "</td>"; }).join("") + "</tr>";
    }).join("");
    return "<table><thead><tr>" + th + "</tr></thead><tbody>" + tr + "</tbody></table>";
  }

  function renderMarkdown(raw) {
    var src = escapeHtml(raw);
    var blocks = [];
    var work = src.replace(/```[^\n]*\n([\s\S]*?)```/g, function (m, body) {
      blocks.push(body);
      // 占位符两端补空格防被周边文本吞掉；匹配一律在 trim 后进行
      return "\n CB" + (blocks.length - 1) + " \n";
    });
    var lines = work.split("\n");
    var out = [];
    var i = 0;
    var isTableRow = function (l) { return /^\s*\|.*\|\s*$/.test(l); };
    var isSepRow = function (l) { return l.indexOf("-") >= 0 && /^\s*\|?[\s:|-]+\|[\s:|-]*\s*$/.test(l); };

    while (i < lines.length) {
      var line = lines[i];
      var ph = line.trim().match(/^CB(\d+)$/); // trim 后空格已被剥掉
      if (ph) {
        out.push("<pre><code>" + blocks[parseInt(ph[1], 10)] + "</code></pre>");
        i++;
        continue;
      }
      if (!line.trim()) { i++; continue; }

      if (isTableRow(line) && i + 1 < lines.length && isSepRow(lines[i + 1])) {
        var head = line;
        var rows = [];
        i += 2;
        while (i < lines.length && isTableRow(lines[i])) { rows.push(lines[i]); i++; }
        out.push(buildTable(head, rows));
        continue;
      }
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        var lvl = h[1].length <= 4 ? 4 : 5; // builder 降级后仅存 h4/h5
        out.push("<h" + lvl + ">" + inlineMd(h[2]) + "</h" + lvl + ">");
        i++;
        continue;
      }
      if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
      if (/^\s*&gt;\s?/.test(line)) {
        var bq = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
          bq.push(lines[i].replace(/^\s*&gt;\s?/, ""));
          i++;
        }
        out.push("<blockquote>" + inlineMd(bq.join(" ")) + "</blockquote>");
        continue;
      }
      if (/^\s*[-*+]\s+/.test(line)) {
        var ul = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          ul.push("<li>" + inlineMd(lines[i].replace(/^\s*[-*+]\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ul>" + ul.join("") + "</ul>");
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        var ol = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          ol.push("<li>" + inlineMd(lines[i].replace(/^\s*\d+\.\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ol>" + ol.join("") + "</ol>");
        continue;
      }
      var para = [line];
      i++;
      while (
        i < lines.length && lines[i].trim() &&
        !/^(#{1,6}\s|\s*[-*+]\s|\s*\d+\.\s|\s*\|)/.test(lines[i]) &&
        !/^\s*&gt;/.test(lines[i]) &&
        !/^CB\d+$/.test(lines[i].trim())
      ) {
        para.push(lines[i]);
        i++;
      }
      out.push("<p>" + inlineMd(para.join("\n")).replace(/\n/g, "<br>") + "</p>");
    }
    return out.join("\n");
  }

  function renderEl(el, mount) {
    if (!el || typeof el !== "object") return;
    switch (el.tag) {
      case "markdown": {
        var div = document.createElement("div");
        div.className = "md" + (el.text_size ? " ts-" + el.text_size : "");
        div.innerHTML = renderMarkdown(pickText(el));
        mount.appendChild(div);
        break;
      }
      case "collapsible_panel": {
        var d = document.createElement("details");
        d.className = "cp" + (el.border && el.border.color ? " border-" + el.border.color : "");
        if (el.expanded) d.open = true;
        var sum = document.createElement("summary");
        // 面板 header 前缀图标槽位：命中厂商时是 custom_icon（预览为 Studio 本地 PNG）
        var ic = el.header && el.header.icon;
        if (ic && ic.tag === "custom_icon" && /^\/assets\/vendor-logos\/[\w.-]+$/.test(String(ic.img_key || ""))) {
          var im = document.createElement("img");
          im.className = "vlogo";
          im.src = ic.img_key;
          im.alt = "";
          sum.appendChild(im);
        }
        var st = document.createElement("span");
        st.textContent = pickText(el.header && el.header.title);
        sum.appendChild(st);
        if (el.header && el.header.text_size) sum.className = "ts-" + el.header.text_size;
        d.appendChild(sum);
        var kids = document.createElement("div");
        kids.className = "cp-children";
        (el.elements || []).forEach(function (c) { renderEl(c, kids); });
        d.appendChild(kids);
        mount.appendChild(d);
        break;
      }
      case "div": {
        var row = document.createElement("div");
        row.className = "tool-row";
        if (el.margin) {
          var ml = String(el.margin).split(" ")[3];
          if (ml && ml !== "0px") row.classList.add("indent");
        }
        if (el.icon && el.icon.tag === "standard_icon") {
          var chip = document.createElement("span");
          chip.className = "icon-chip";
          chip.title = el.icon.token || "";
          chip.textContent = el.icon.token || "icon";
          row.appendChild(chip);
        }
        if (el.text) {
          var span = document.createElement("div");
          var ts = el.text.text_size ? " ts-" + el.text.text_size : "";
          if (el.text.tag === "lark_md") {
            span.className = "md" + ts;
            span.innerHTML = renderMarkdown(pickText(el.text));
          } else {
            span.className = ts.trim();
            span.textContent = pickText(el.text);
          }
          row.appendChild(span);
        }
        mount.appendChild(row);
        break;
      }
      case "hr":
        mount.appendChild(document.createElement("hr"));
        break;
      default: {
        var tag = document.createElement("span");
        tag.className = "unknown-el";
        tag.textContent = (window.FryI18n ? window.FryI18n.t("pv.element") : "元素 ") + el.tag;
        mount.appendChild(tag);
      }
    }
  }

  function renderCard(card) {
    var root = document.createElement("div");
    root.className = "feishu-card width-" + ((card.config && card.config.width_mode) || "default");
    if (card.header) {
      var h = document.createElement("div");
      h.className = "card-header t-" + (card.header.template || "blue");
      h.textContent = pickText(card.header.title);
      root.appendChild(h);
    }
    var body = document.createElement("div");
    body.className = "card-body";
    var els = (card.body && card.body.elements) || [];
    if (!els.length) {
      var empty = document.createElement("div");
      empty.className = "preview-empty";
      empty.textContent = window.FryI18n ? window.FryI18n.t("pv.empty") : "(无元素)";
      body.appendChild(empty);
    }
    els.forEach(function (el) { renderEl(el, body); });
    root.appendChild(body);
    return root;
  }

  window.FryPreview = { renderCard: renderCard, renderMarkdown: renderMarkdown, escapeHtml: escapeHtml };
})();
