/* fry-cards Studio — 表单状态 / 保存 / 预览 / 状态诊断 / Toast */
(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };
  var esc = window.FryPreview.escapeHtml;
  var I18N = window.FryI18n;
  I18N.apply();

  /* ---------- 主题（亮 / 暗，localStorage 记忆） ---------- */
  var ICON_MOON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  var ICON_SUN = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.4"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M19.4 4.6l-1.8 1.8M6.4 17.6l-1.8 1.8"/></svg>';
  var themeBtn = document.getElementById("theme-toggle");
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    themeBtn.innerHTML = t === "dark" ? ICON_SUN : ICON_MOON;
    try { localStorage.setItem("fry-theme", t); } catch (e) { /* 隐私模式忽略 */ }
  }
  var savedTheme = "light";
  try { savedTheme = localStorage.getItem("fry-theme") || "light"; } catch (e) { /* ignore */ }
  applyTheme(savedTheme);
  themeBtn.addEventListener("click", function () {
    var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    applyTheme(cur === "dark" ? "light" : "dark");
  });

  /* ---------- 语言切换（中 / 英，localStorage 记忆） ---------- */
  var langBtn = document.getElementById("lang-toggle");
  langBtn.addEventListener("click", function () {
    I18N.setLang(I18N.getLang() === "en" ? "zh" : "en");
    refreshFieldChipLabels();
    renderAliasRows();
    syncFoldBadges();
    if (document.querySelector('.panel[data-panel="status"]').classList.contains("active")) loadStatus();
  });

  /* ---------- 字段 chips（点按显隐 / 拖动排序 — CreditDaddy 组件设置同款） ---------- */
  var GRIP_SVG =
    '<svg class="drag-handle" viewBox="0 0 16 16" width="10" height="12" fill="currentColor" aria-hidden="true">' +
    '<circle cx="5" cy="3.2" r="1.4"/><circle cx="11" cy="3.2" r="1.4"/>' +
    '<circle cx="5" cy="8" r="1.4"/><circle cx="11" cy="8" r="1.4"/>' +
    '<circle cx="5" cy="12.8" r="1.4"/><circle cx="11" cy="12.8" r="1.4"/></svg>';
  var FIELD_DEFS = {
    "f-footer-fields": [
      ["status", "field.status"], ["elapsed", "field.elapsed"], ["speed", "field.speed"],
      ["cache", "field.cache"], ["model", "field.model"], ["tokens", "field.tokens"], ["context", "field.context"],
    ],
    "f-panel-fields": [
      ["model", "field.model_p"], ["reasoning", "field.reasoning_p"], ["tools", "field.tools_p"],
      ["context", "field.context"], ["elapsed", "field.elapsed_p"], ["speed", "field.speed_p"],
      ["cache", "field.cache"], ["tokens", "field.tokens"], ["status", "field.status"],
    ],
  };

  function renderFieldChips(containerId) {
    var box = document.getElementById(containerId);
    FIELD_DEFS[containerId].forEach(function (def) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "field-chip";
      chip.draggable = true;
      chip.dataset.field = def[0];
      chip.dataset.labelKey = def[1];
      chip.setAttribute("aria-pressed", "true");
      chip.innerHTML = GRIP_SVG + "<span>" + esc(I18N.t(def[1])) + "</span>";
      box.appendChild(chip);
    });
  }
  Object.keys(FIELD_DEFS).forEach(renderFieldChips);

  /* 语言切换时只更新标签文本，不重建 DOM——显隐状态与拖动顺序全部保留 */
  function refreshFieldChipLabels() {
    document.querySelectorAll(".field-chip").forEach(function (chip) {
      var span = chip.querySelector("span:last-child");
      if (span && chip.dataset.labelKey) span.textContent = I18N.t(chip.dataset.labelKey);
    });
  }

  /* 点按切换显隐（置灰 = 隐藏） */
  document.querySelectorAll(".field-chips").forEach(function (box) {
    box.addEventListener("click", function (e) {
      var chip = e.target.closest(".field-chip");
      if (!chip || box.classList.contains("locked")) return;
      var off = chip.classList.toggle("off");
      chip.setAttribute("aria-pressed", off ? "false" : "true");
    });
  });

  /* 按住拖动调整顺序（HTML5 DnD） */
  var dragChip = null;

  /* 返回“应插到它前面”的 chip；null = 插到末尾（filter 掉被拖动者自身） */
  function dropRefChip(box, x, y) {
    var chips = Array.prototype.slice
      .call(box.querySelectorAll(".field-chip"))
      .filter(function (c) { return c !== dragChip; });
    for (var i = 0; i < chips.length; i++) {
      var r = chips[i].getBoundingClientRect();
      if (y > r.bottom) continue;                       // 该行已过，看下一行
      if (y >= r.top && x >= r.left + r.width / 2) continue; // 同行中线右侧 → 看下一个
      return chips[i];
    }
    return null;
  }

  document.querySelectorAll(".field-chips").forEach(function (box) {
    box.addEventListener("dragstart", function (e) {
      var chip = e.target.closest(".field-chip");
      if (!chip || box.classList.contains("locked")) { e.preventDefault(); return; }
      dragChip = chip;
      chip.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", chip.dataset.field);
    });
    box.addEventListener("dragover", function (e) {
      if (!dragChip) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      box.querySelectorAll(".field-chip.drop-mark").forEach(function (c) { c.classList.remove("drop-mark"); });
      var ref = dropRefChip(box, e.clientX, e.clientY);
      if (ref) ref.classList.add("drop-mark");
    });
    box.addEventListener("drop", function (e) {
      if (!dragChip) return;
      e.preventDefault();
      var ref = dropRefChip(box, e.clientX, e.clientY);
      if (ref) box.insertBefore(dragChip, ref);
      else box.appendChild(dragChip);
    });
    box.addEventListener("dragleave", function (e) {
      if (e.relatedTarget && box.contains(e.relatedTarget)) return;
      box.querySelectorAll(".field-chip.drop-mark").forEach(function (c) { c.classList.remove("drop-mark"); });
    });
    box.addEventListener("dragend", function () {
      box.querySelectorAll(".field-chip.drop-mark").forEach(function (c) { c.classList.remove("drop-mark"); });
      if (dragChip) dragChip.classList.remove("dragging");
      dragChip = null;
    });
  });

  /* 按 order 重排 chips（已保存顺序在前，未选字段保持相对顺序跟在后面） */
  function orderRows(container, order) {
    if (!container || !order || !order.length) return;
    var chips = Array.prototype.slice.call(container.querySelectorAll(".field-chip"));
    var byField = {};
    chips.forEach(function (r) { byField[r.dataset.field] = r; });
    var seen = {};
    var result = [];
    order.forEach(function (f) {
      if (byField[f] && !seen[f]) { result.push(byField[f]); seen[f] = true; }
    });
    chips.forEach(function (r) {
      if (!seen[r.dataset.field]) { result.push(r); seen[r.dataset.field] = true; }
    });
    result.forEach(function (r) { container.appendChild(r); });
  }

  /* ---------- tabs ---------- */
  document.getElementById("tabs").addEventListener("click", function (e) {
    var b = e.target.closest(".tab");
    if (!b) return;
    document.querySelectorAll(".tab").forEach(function (t) { t.classList.toggle("active", t === b); });
    document.querySelectorAll(".panel").forEach(function (p) {
      p.classList.toggle("active", p.dataset.panel === b.dataset.tab);
    });
    if (b.dataset.tab === "status") loadStatus();
  });

  /* ---------- toast ---------- */
  var toastTimer = null;
  function toast(msg, cls) {
    var t = $("#toast");
    t.textContent = msg;
    t.className = "toast " + (cls || "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 4000);
  }

  async function api(path, opts) {
    var res = await fetch(path, opts);
    var data = null;
    try { data = await res.json(); }
    catch (e) { data = { ok: false, error: I18N.t("toast.badjson") + res.status + ")" }; }
    if (!res.ok) {
      var err = new Error((data && data.error) || "HTTP " + res.status);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  /* ---------- 折叠卡片摘要徽标（跟随主开关显示 已启用/已停用） ---------- */
  function syncFoldBadges() {
    document.querySelectorAll("[data-fold-badge]").forEach(function (b) {
      var input = document.getElementById(b.dataset.foldBadge);
      var on = !!(input && input.checked);
      b.textContent = I18N.t(on ? "st.on" : "st.off");
      b.classList.toggle("on", on);
    });
  }
  document.getElementById("config-form").addEventListener("change", syncFoldBadges);

  /* ---------- 配置表单 ---------- */
  var __origChatTypes = undefined;
  var __origFields = undefined;

  function setCheck(id, v) { document.getElementById(id).checked = !!v; }

  function setVal(id, v) {
    var el = document.getElementById(id);
    var s = String(v);
    if (el.tagName !== "SELECT") {
      // number 等 input 无 options — 直接赋值
      el.value = s;
      return;
    }
    var found = false;
    Array.prototype.forEach.call(el.options, function (o) { if (o.value === s) found = true; });
    if (!found) {
      var o = document.createElement("option");
      o.value = s;
      o.textContent = s + (I18N.getLang() === "en" ? " (custom)" : "（自定义）");
      el.appendChild(o);
    }
    el.value = s;
  }

  function fillForm(state) {
    var s = state.streaming;
    var d = state.display;
    setCheck("f-enabled", s.enabled);
    setVal("f-width", s.width_mode);
    setCheck("f-panel-expanded", s.panel_expanded);
    setVal("f-panel-text-size", d.panel_text_size);
    setCheck("f-completion-notice", s.completion_notice);
    setVal("f-completion-notice-text", s.completion_notice_text);
    setVal("f-content-lang", s.content_lang);

    // 聊天类型：三选项覆盖常见形态；异常值显示自定义项并在保存时原样保留
    __origChatTypes = s.chat_types;
    var ct = s.chat_types;
    if (ct === null || ct.length === 2) setVal("f-chat-types", "all");
    else if (ct.length === 1 && (ct[0] === "dm" || ct[0] === "group")) setVal("f-chat-types", ct[0]);
    else setVal("f-chat-types", "__custom");

    setCheck("f-header-enabled", s.header.enabled);
    setVal("f-header-min-duration", s.header.min_duration);
    setCheck("f-footer-enabled", s.footer.enabled);
    setCheck("f-footer-show-label", s.footer.show_label);
    setVal("f-footer-text-size", s.footer.text_size);

    __origFields = s.footer.fields;
    var firstRow = (s.footer.fields && s.footer.fields[0]) || [];
    var multi = !!(s.footer.fields && s.footer.fields.length > 1);
    var footerBox = document.getElementById("f-footer-fields");
    footerBox.classList.toggle("locked", multi);
    if (!multi) orderRows(footerBox, firstRow);
    footerBox.querySelectorAll(".field-chip").forEach(function (c) {
      var on = firstRow.indexOf(c.dataset.field) >= 0;
      c.classList.toggle("off", !on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
      c.draggable = !multi;
    });
    var note = $("#fields-note");
    if (note) note.hidden = !multi;

    setVal("f-body-text-size", s.body.text_size);
    setVal("f-flush-interval-ms", s.flush_interval_ms);
    var ais = s.ai_summary || {};
    setCheck("f-ais-enabled", ais.enabled);
    setVal("f-ais-min-chars", ais.min_chars == null ? 800 : ais.min_chars);
    setVal("f-ais-out-chars", ais.max_output_chars == null ? 160 : ais.max_output_chars);
    setVal("f-ais-timeout", ais.timeout_sec == null ? 20 : ais.timeout_sec);
    if (ais.model) setVal("f-ais-model", ais.model);
    setCheck("f-show-reasoning", d.show_reasoning);
    setCheck("f-show-tool-use", d.show_tool_use);
    setCheck("f-show-context", d.show_context);
    setCheck("f-truncate-model", d.truncate_model_name);
    setCheck("f-vendor-logo", d.vendor_logo);
    setCheck("f-alias-enabled", d.model_aliases_enabled);
    setVal("f-max-panels", d.max_reasoning_panels);
    setVal("f-unified-min-duration", d.unified_panel_min_duration);
    setVal("f-context-mode", d.context_display_mode);

    var pf = d.panel_fields || [];
    orderRows(document.getElementById("f-panel-fields"), pf);
    document.querySelectorAll("#f-panel-fields .field-chip").forEach(function (c) {
      var on = pf.indexOf(c.dataset.field) >= 0;
      c.classList.toggle("off", !on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
    });

    var gsb = ((state.gateway || {}).group_security_boundary) || {};
    setCheck("f-gsb-enabled", gsb.enabled);
    document.getElementById("f-gsb-allow").value = (gsb.allow_chats || []).join("\n");
    syncFoldBadges();
  }

  function num(sel) {
    var v = parseFloat($(sel).value);
    return isNaN(v) ? 0 : v;
  }

  function int(sel) {
    var v = parseInt($(sel).value, 10);
    return isNaN(v) ? 1 : v;
  }

  function readForm() {
    var ctSel = $("#f-chat-types").value;
    var chatTypes;
    if (ctSel === "__custom") chatTypes = __origChatTypes;
    else if (ctSel === "all") chatTypes = null;
    else chatTypes = [ctSel];

    var gsbChats = (document.getElementById("f-gsb-allow").value || "")
      .split("\n")
      .map(function (s) { return s.trim(); })
      .filter(Boolean);

    // 按 DOM 实际顺序（含拖动调整结果）收集未置灰的字段
    var picked = [];
    document.querySelectorAll("#f-footer-fields .field-chip").forEach(function (c) {
      if (!c.classList.contains("off")) picked.push(c.dataset.field);
    });
    var fields;
    if (__origFields && __origFields.length > 1) fields = __origFields; // 多行原样保留
    else fields = picked.length ? [picked] : [];

    var pickedPanel = [];
    document.querySelectorAll("#f-panel-fields .field-chip").forEach(function (c) {
      if (!c.classList.contains("off")) pickedPanel.push(c.dataset.field);
    });
    var panelFields = pickedPanel;

    return {
      streaming: {
        enabled: $("#f-enabled").checked,
        content_lang: $("#f-content-lang").value,
        chat_types: chatTypes,
        panel_expanded: $("#f-panel-expanded").checked,
        completion_notice: $("#f-completion-notice").checked,
        completion_notice_text: $("#f-completion-notice-text").value,
        width_mode: $("#f-width").value,
        flush_interval_ms: int("#f-flush-interval-ms"),
        header: {
          enabled: $("#f-header-enabled").checked,
          min_duration: num("#f-header-min-duration"),
        },
        footer: {
          enabled: $("#f-footer-enabled").checked,
          show_label: $("#f-footer-show-label").checked,
          fields: fields,
          text_size: $("#f-footer-text-size").value,
        },
        body: { text_size: $("#f-body-text-size").value },
        ai_summary: {
          enabled: $("#f-ais-enabled").checked,
          min_chars: Math.max(0, parseInt($("#f-ais-min-chars").value, 10) || 0),
          max_output_chars: Math.min(600, Math.max(40, parseInt($("#f-ais-out-chars").value, 10) || 160)),
          timeout_sec: Math.min(120, Math.max(1, num("#f-ais-timeout") || 20)),
          model: $("#f-ais-model").value,
        },
      },
      display: {
        show_reasoning: $("#f-show-reasoning").checked,
        show_tool_use: $("#f-show-tool-use").checked,
        show_context: $("#f-show-context").checked,
        truncate_model_name: $("#f-truncate-model").checked,
        vendor_logo: $("#f-vendor-logo").checked,
        model_aliases_enabled: $("#f-alias-enabled").checked,
        max_reasoning_panels: int("#f-max-panels"),
        unified_panel_min_duration: num("#f-unified-min-duration"),
        context_display_mode: $("#f-context-mode").value,
        panel_fields: panelFields,
        panel_text_size: $("#f-panel-text-size").value,
      },
      gateway: {
        group_security_boundary: {
          enabled: $("#f-gsb-enabled").checked,
          allow_chats: gsbChats,
        },
      },
    };
  }

  /* /api/state 的最近一次结果：工具箱页签（tools.js）据此拿候选模型与端点状态 */
  var lastState = null;
  var stateListeners = [];

  async function loadState() {
    var data = await api("/api/state");
    lastState = data;
    // 先灌摘要模型候选，再填表单：否则 setVal 找不到 option 会写进一个临时「自定义」项，
    // 随后被候选重建抹掉，用户已存的模型名就丢了。
    fillAiSummaryModelOptions(data.preview, data.tools);
    fillForm(data);
    fillModelOptions(data.preview);
    var ver = data.status && data.status.fry_version;
    var el = document.getElementById("brand-version");
    if (el && ver) el.textContent = "v" + ver;
    if (data.tools) {
      stateListeners.forEach(function (fn) {
        try { fn(data.tools); } catch (e) { /* 旁路出错不打断配置页 */ }
      });
    }
    return data;
  }

  /* 摘要模型下拉：候选同预览页口径（已配置 + 端点清单 + 厂商家族样本），
   * 首项「自动」value 空 = 由服务端挑 flash 小模型。 */
  function fillAiSummaryModelOptions(preview, tools) {
    var sel = document.getElementById("f-ais-model");
    if (!sel) return;
    var prev = sel.value;
    var seen = {};
    var ids = [];
    var pools = [(tools && tools.models) || [], (preview && preview.models) || []];
    pools.forEach(function (list) {
      list.forEach(function (m) {
        var id = m && m.id;
        if (id && !seen[id]) { seen[id] = 1; ids.push(id); }
      });
    });
    sel.innerHTML = "";
    var auto = document.createElement("option");
    auto.value = "";
    auto.textContent = I18N.t("opt.ais-auto");
    auto.title = (tools && tools.default_model) || "";
    sel.appendChild(auto);
    ids.forEach(function (id) {
      var o = document.createElement("option");
      o.value = id;
      o.textContent = id;
      sel.appendChild(o);
    });
    var still = Array.prototype.some.call(sel.options, function (o) { return o.value === prev; });
    sel.value = still ? prev : "";
  }

  $("#btn-reload").addEventListener("click", function () {
    loadState().then(function () { toast(I18N.t("toast.reloaded"), ""); })
      .catch(function (e) { toast(I18N.t("toast.readfail") + e.message, "err"); });
  });

  async function saveConfig() {
    var btn = $("#btn-save");
    btn.disabled = true;
    try {
      var payload = readForm();
      var data = await api("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      var parts = [];
      if (data.changed && data.changed.length) parts.push(data.changed.join("、"));
      var aliasData = await api("/api/aliases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: collectAliases() }),
      });
      parts.push(I18N.t("toast.aliases").replace("{}", aliasData.count));
      toast(parts.length ? I18N.t("toast.saved") + parts.join(" · ") : I18N.t("toast.nochange"), "ok");
      await loadState();
      await loadAliases();
      return true;
    } catch (err) {
      toast(I18N.t("toast.savefail") + err.message, "err");
      return false;
    } finally {
      btn.disabled = false;
    }
  }

  $("#config-form").addEventListener("submit", function (e) {
    e.preventDefault();
    saveConfig();
  });

  /* 保存并预览：落盘后切到预览页签直接渲染效果 */
  $("#btn-save-preview").addEventListener("click", async function () {
    var btn = this;
    btn.disabled = true;
    var ok = await saveConfig();
    btn.disabled = false;
    if (!ok) return;
    document.querySelector('.tab[data-tab="preview"]').click();
    $("#btn-render").click();
  });

  /* ---------- 预览 ---------- */
  $("#p-state").addEventListener("change", syncPreviewControls);
  function syncPreviewControls() {
    var streaming = $("#p-state").value === "streaming";
    $("#p-outcome").disabled = streaming;
    // 流式占位卡不含 model 字段（footer/统一面板在完成态才生成），选也没效果 → 置灰
    $("#p-model").disabled = streaming;
  }

  /* 模型下拉：候选来自服务端（已配置模型 + 配置内模型 + 厂商家族样本），
   * 首项「已配置模型（默认）」value 为空 → 渲染时由服务端解析当前配置模型，
   * 改完配置不必重开 Studio 也能跟着走。 */
  function fillModelOptions(preview) {
    var sel = $("#p-model");
    if (!sel) return;
    var prev = sel.value;
    var models = (preview && preview.models) || [];
    sel.innerHTML = "";
    var optDefault = document.createElement("option");
    optDefault.value = "";
    optDefault.textContent = I18N.t("opt.model-default");
    optDefault.title = (preview && preview.default_model) || "";
    sel.appendChild(optDefault);
    models.forEach(function (m) {
      var o = document.createElement("option");
      o.value = m.id;
      o.textContent = m.label;
      sel.appendChild(o);
    });
    // 保留用户已选（重新读取/保存后不跳回默认），选项已不存在则回落默认项
    var still = Array.prototype.some.call(sel.options, function (o) { return o.value === prev; });
    sel.value = still ? prev : "";
  }

  $("#btn-render").addEventListener("click", async function () {
    var btn = $("#btn-render");
    btn.disabled = true;
    try {
      var payload = {
        scenario: $("#p-scenario").value,
        state: $("#p-state").value,
        outcome: $("#p-outcome").value,
        model: $("#p-model").value,
      };
      var data = await api("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      var mount = $("#preview-mount");
      mount.innerHTML = "";
      mount.appendChild(window.FryPreview.renderCard(data.card));
    } catch (err) {
      toast(I18N.t("toast.previewfail") + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- 状态诊断 ---------- */
  function hookBadge(v) {
    if (v === "installed") return '<span class="badge ok">' + esc(I18N.t("st.installed")) + "</span>";
    if (v === "not_installed") return '<span class="badge warn">' + esc(I18N.t("st.not-installed")) + "</span>";
    return '<span class="badge mute">' + esc(I18N.t("st.unavailable")) + "</span>";
  }

  function verifyLine(name, v) {
    var cls = v === "compatible" ? "ok" : v === "incompatible" ? "bad" : "mute";
    var txt = I18N.t(v === "compatible" ? "st.compatible" : v === "incompatible" ? "st.incompatible" : "st.unavailable");
    return '<div class="status-line"><span>' + name + '</span><span class="badge ' + cls + '">' + esc(txt) + "</span></div>";
  }

  function renderStatus(st) {
    var patchedBadge;
    if (st.patched === null || st.patched === undefined) patchedBadge = '<span class="badge mute">' + esc(I18N.t("st.no-hermes")) + "</span>";
    else if (st.patched) patchedBadge = '<span class="badge ok">' + esc(I18N.t("st.patches-installed")) + "</span>";
    else patchedBadge = '<span class="badge warn">' + esc(I18N.t("st.patches-missing")) + "</span>";
    var markers = Object.keys(st.markers || {}).map(function (k) {
      var v = st.markers[k];
      return '<span class="badge ' + (v ? "ok" : "bad") + '">' + esc(k) + "</span>";
    }).join(" ");

    $("#status-mount").innerHTML =
      '<div class="status-grid">' +
      '<div class="status-card"><h3>' + esc(I18N.t("st.patched-gw")) + "</h3>" +
      '<div class="status-line"><span>' + esc(I18N.t("st.state")) + "</span>" + patchedBadge + "</div>" +
      (st.target ? '<div class="status-line mono">' + esc(st.target) + "</div>" : "") +
      (markers ? '<div class="status-line">' + markers + "</div>" : "") +
      '<div class="status-line"><span>Cron</span>' + hookBadge(st.cron_hook) +
      "<span>Clarify</span>" + hookBadge(st.clarify_hook) + "</div></div>" +

      '<div class="status-card"><h3>' + esc(I18N.t("st.verify")) + "</h3>" +
      verifyLine("gateway", st.verify.gateway) +
      verifyLine("cron", st.verify.cron) +
      verifyLine("clarify", st.verify.clarify) + "</div>" +

      '<div class="status-card"><h3>' + esc(I18N.t("st.runtime")) + "</h3>" +
      '<div class="status-line"><span>' + esc(I18N.t("st.streaming")) + "</span>" +
      (st.streaming_enabled ? '<span class="badge ok">' + esc(I18N.t("st.on")) + "</span>" : '<span class="badge warn">' + esc(I18N.t("st.off")) + "</span>") +
      "</div>" +
      '<div class="status-line"><span>' + esc(I18N.t("st.content-lang")) + '</span><span class="badge mute">' + esc(st.content_lang || "-") + "</span></div>" +
      '<div class="status-line"><span>' + esc(I18N.t("st.creds")) + "</span>" +
      (st.credentials ? '<span class="badge ok">' + esc(I18N.t("st.configured")) + "</span>" : '<span class="badge bad">' + esc(I18N.t("st.missing")) + "</span>") + "</div></div>" +

      '<div class="status-card"><h3>' + esc(I18N.t("st.env")) + "</h3>" +
      '<div class="status-line"><span>' + esc(I18N.t("st.plugin-ver")) + '</span><span class="badge mute">' + esc(st.fry_version || I18N.t("st.unknown")) + "</span></div>" +
      '<div class="status-line"><span>Hermes Agent</span><span class="badge mute">' + esc(st.hermes_version || I18N.t("st.no-cli")) + "</span></div>" +
      '<div class="status-line mono">' + esc(st.hermes_python || I18N.t("st.no-python")) + "</div>" +
      '<div class="status-line mono">' + esc(st.install_dir || I18N.t("st.no-dir")) + "</div></div>" +
      "</div>";
  }

  async function loadStatus() {
    var mount = $("#status-mount");
    try {
      var data = await api("/api/state");
      renderStatus(data.status);
    } catch (err) {
      mount.innerHTML = '<div class="status-card"><div class="status-line">' + esc(I18N.t("st.loadfail") + err.message) + "</div></div>";
    }
  }

  $("#btn-refresh-status").addEventListener("click", loadStatus);

  $("#btn-restart").addEventListener("click", async function () {
    if (!window.confirm(I18N.t("confirm.restart"))) return;
    var btn = $("#btn-restart");
    btn.disabled = true;
    try {
      var d = await api("/api/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (d.ok) toast(I18N.t("toast.restart-ok"), "ok");
      else toast(I18N.t("toast.restart-fail") + (d.error || (d.output || "").slice(-300) || I18N.t("toast.unknown-err")), "err");
      if (d.output) console.log("[restart]", d.output);
      loadStatus();
    } catch (err) {
      toast(I18N.t("toast.restart-reqfail") + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- 模型别名编辑器 ---------- */
  function dayLabel(di) { return (I18N.getLang() === "en" ? ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] : ["日", "一", "二", "三", "四", "五", "六"])[di]; }
  var aliasEntries = []; // [{key, value: string | {name, timeAliases}}]

  function aliasIsObject(entry) {
    return entry.value !== null && typeof entry.value === "object";
  }

  function renderAliasRows() {
    var host = $("#alias-rows");
    host.innerHTML = "";
    if (!aliasEntries.length) {
      host.innerHTML = '<div class="alias-empty">' + esc(I18N.t("alias.empty")) + "</div>";
      return;
    }
    aliasEntries.forEach(function (entry, i) {
      var isObj = aliasIsObject(entry);
      var nameVal = isObj ? (entry.value.name || "") : (entry.value || "");
      var div = document.createElement("div");
      div.className = "alias-entry";
      div.dataset.i = String(i);
      var rulesHtml = "";
      if (isObj) {
        var rules = entry.value.timeAliases || [];
        var sameDays = function (days, preset) {
          var cur = days == null ? [0, 1, 2, 3, 4, 5, 6] : days;
          return cur.length === preset.length && preset.every(function (d) { return cur.indexOf(d) >= 0; });
        };
        var rulesInner = rules
          .map(function (rule, ri) {
            var days = rule.days == null ? [0, 1, 2, 3, 4, 5, 6] : rule.days;
            // 中文习惯周一开始显示；底层数据仍 0=周日（claw 兼容），仅展示顺序调整
            var dayChips = [1, 2, 3, 4, 5, 6, 0].map(function (di) {
              var on = days.indexOf(di) >= 0 ? " on" : "";
              return '<button type="button" class="chip day-chip' + on + '" data-day="' + di + '">' + dayLabel(di) + "</button>";
            }).join("");
            var presets =
              '<span class="day-presets">' +
              '<button type="button" class="chip day-preset' + (sameDays(days, [1, 2, 3, 4, 5]) ? " on" : "") + '" data-mode="work">' + esc(I18N.t("preset.work")) + "</button>" +
              '<button type="button" class="chip day-preset' + (sameDays(days, [0, 6]) ? " on" : "") + '" data-mode="weekend">' + esc(I18N.t("preset.weekend")) + "</button>" +
              "</span>";
            // 两段式：星期一行（含快捷选择）/ 时间+名称一行——窄列（三列布局）下不再随机换行
            return (
              '<div class="alias-rule" data-r="' + ri + '">' +
              '<div class="rule-days">' + dayChips + presets + "</div>" +
              '<div class="rule-time-row">' +
              '<input type="time" class="rule-start" value="' + (rule.start || "") + '">' +
              "<span>–</span>" +
              '<input type="time" class="rule-end" value="' + (rule.end || "") + '">' +
              '<input type="text" class="rule-name" placeholder="' + escAttr(I18N.t("ph.rule-name")) + '" value="' + escAttr(rule.name) + '">' +
              '<button type="button" class="chip rule-del">✕</button>' +
              "</div>" +
              "</div>"
            );
          })
          .join("");
        // 兜底默认名放底部（以上规则都不命中时生效）
        rulesHtml =
          '<div class="alias-rules">' +
          rulesInner +
          '<div><button type="button" class="chip add-rule">' + esc(I18N.t("btn.add-rule")) + "</button></div>" +
          '<div class="alias-default-row"><span>' + esc(I18N.t("alias.default-row")) + "</span>" +
          '<input type="text" class="alias-default-name" placeholder="' + escAttr(I18N.t("ph.alias-default")) + '" value="' + escAttr(nameVal) + '"></div>' +
          "</div>";
      }
      div.innerHTML =
        '<div class="alias-row">' +
        '<input type="text" class="alias-key" placeholder="' + escAttr(I18N.t("ph.alias-key")) + '" value="' + escAttr(entry.key) + '">' +
        (isObj ? "" : '<input type="text" class="alias-name" placeholder="' + escAttr(I18N.t("ph.alias-name")) + '" value="' + escAttr(nameVal) + '">') +
        '<button type="button" class="chip alias-time' + (isObj ? " on" : "") + '" title="' + escAttr(I18N.t("title.alias-time")) + '">🕐</button>' +
        '<button type="button" class="chip alias-del" title="' + escAttr(I18N.t("title.del")) + '">✕</button>' +
        "</div>" +
        rulesHtml;
      host.appendChild(div);
    });
  }

  function escAttr(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }

  async function loadAliases() {
    try {
      var data = await api("/api/aliases");
      aliasEntries = (data.entries || []).map(function (e) {
        return { key: e.key, value: e.value };
      });
    } catch (e) {
      toast(I18N.t("toast.readfail") + e.message, "err");
      aliasEntries = [];
    }
    renderAliasRows();
  }

  function collectAliases() {
    // 空键行视为未填完，跳过不提交；其余原样送服务端校验
    return aliasEntries
      .filter(function (e) { return e.key && e.key.trim(); })
      .map(function (e) { return { key: e.key.trim(), value: e.value }; });
  }

  $("#btn-add-alias").addEventListener("click", function () {
    aliasEntries.push({ key: "", value: "" });
    renderAliasRows();
  });

  document.getElementById("alias-rows").addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t.classList || !t.classList.contains("chip")) return;
    var entryDiv = t.closest(".alias-entry");
    if (!entryDiv) return;
    var i = parseInt(entryDiv.dataset.i, 10);
    var entry = aliasEntries[i];
    if (!entry) return;

    if (t.classList.contains("alias-del")) {
      aliasEntries.splice(i, 1);
      renderAliasRows();
      return;
    }
    if (t.classList.contains("alias-time")) {
      if (aliasIsObject(entry)) {
        if (
          (entry.value.timeAliases || []).length &&
          !window.confirm(I18N.t("confirm.alias-static"))
        ) {
          return;
        }
        entry.value = entry.value.name || "";
      } else {
        entry.value = { name: entry.value || "", timeAliases: [] };
      }
      renderAliasRows();
      return;
    }
    var ruleDiv = t.closest(".alias-rule");
    if (t.classList.contains("add-rule")) {
      entry.value.timeAliases = entry.value.timeAliases || [];
      entry.value.timeAliases.push({ days: [1, 2, 3, 4, 5], name: "" });
      renderAliasRows();
      return;
    }
    if (!ruleDiv) return;
    var ri = parseInt(ruleDiv.dataset.r, 10);
    var rules = entry.value.timeAliases || [];
    if (t.classList.contains("rule-del")) {
      rules.splice(ri, 1);
      renderAliasRows();
      return;
    }
    if (t.classList.contains("day-chip")) {
      var rule = rules[ri];
      var day = parseInt(t.dataset.day, 10);
      var cur = rule.days == null ? [0, 1, 2, 3, 4, 5, 6] : rule.days.slice();
      var idx = cur.indexOf(day);
      if (idx >= 0) {
        if (cur.length === 1) return; // 至少保留一天
        cur.splice(idx, 1);
      } else {
        cur.push(day);
        cur.sort();
      }
      rule.days = cur;
      renderAliasRows();
      return;
    }
    if (t.classList.contains("day-preset")) {
      rules[ri].days = t.dataset.mode === "work" ? [1, 2, 3, 4, 5] : [0, 6];
      renderAliasRows();
    }
  });

  document.getElementById("alias-rows").addEventListener("input", function (ev) {
    var t = ev.target;
    var entryDiv = t.closest(".alias-entry");
    if (!entryDiv) return;
    var i = parseInt(entryDiv.dataset.i, 10);
    var entry = aliasEntries[i];
    if (!entry) return;

    if (t.classList.contains("alias-key")) { entry.key = t.value; return; }
    if (t.classList.contains("alias-name")) { entry.value = t.value; return; }
    if (!aliasIsObject(entry)) return;
    if (t.classList.contains("alias-default-name")) { entry.value.name = t.value; return; }
    var ruleDiv = t.closest(".alias-rule");
    if (!ruleDiv) return;
    var ri = parseInt(ruleDiv.dataset.r, 10);
    var rule = (entry.value.timeAliases || [])[ri];
    if (!rule) return;
    if (t.classList.contains("rule-start")) { if (t.value) rule.start = t.value; else delete rule.start; }
    else if (t.classList.contains("rule-end")) { if (t.value) rule.end = t.value; else delete rule.end; }
    else if (t.classList.contains("rule-name")) { rule.name = t.value; }
  });

  /* ---------- 初始化 ---------- */
  loadState()
    .then(function () { return loadAliases(); })
    .catch(function (e) { toast(I18N.t("toast.readfail") + e.message, "err"); });

  /* 工具箱页签（tools.js）复用同一套 api / toast / i18n 口径：
   * onStateLoaded 在每次 /api/state 成功之后回调，把 tools 段交给它。 */
  window.FryStudio = {
    api: api,
    toast: toast,
    esc: esc,
    i18n: I18N,
    onStateLoaded: function (fn) {
      if (typeof fn !== "function") return;
      stateListeners.push(fn);
      if (lastState && lastState.tools) { try { fn(lastState.tools); } catch (e) { /* 旁路不影响主流程 */ } }
    },
  };
})();
