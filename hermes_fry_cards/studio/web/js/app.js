/* fry-cards Studio — 表单状态 / 保存 / 预览 / 状态诊断 / Toast */
(function () {
  "use strict";

  var FIELD_ORDER = ["status", "elapsed", "model", "tokens", "context"];
  var $ = function (sel) { return document.querySelector(sel); };
  var esc = window.FryPreview.escapeHtml;

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
    catch (e) { data = { ok: false, error: "响应不是 JSON (HTTP " + res.status + ")" }; }
    if (!res.ok) {
      var err = new Error((data && data.error) || "HTTP " + res.status);
      err.status = res.status;
      throw err;
    }
    return data;
  }

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
      o.textContent = s + "（自定义）";
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
    document.querySelectorAll("#f-footer-fields .chip").forEach(function (c) {
      c.classList.toggle("on", firstRow.indexOf(c.dataset.field) >= 0);
    });
    var multi = __origFields && __origFields.length > 1;
    document.querySelectorAll("#f-footer-fields .chip").forEach(function (c) {
      c.disabled = !!multi;
      c.style.opacity = multi ? 0.5 : "";
      c.style.cursor = multi ? "not-allowed" : "pointer";
    });
    var note = $("#fields-note");
    if (note) note.hidden = !multi;

    setVal("f-body-text-size", s.body.text_size);
    setCheck("f-show-reasoning", d.show_reasoning);
    setCheck("f-show-tool-use", d.show_tool_use);
    setCheck("f-show-context", d.show_context);
    setCheck("f-truncate-model", d.truncate_model_name);
    setVal("f-max-panels", d.max_reasoning_panels);
    setVal("f-unified-min-duration", d.unified_panel_min_duration);
    setVal("f-context-mode", d.context_display_mode);
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

    var picked = [];
    document.querySelectorAll("#f-footer-fields .chip.on").forEach(function (c) {
      picked.push(c.dataset.field);
    });
    var ordered = FIELD_ORDER.filter(function (f) { return picked.indexOf(f) >= 0; });
    var fields;
    if (__origFields && __origFields.length > 1) fields = __origFields; // 多行原样保留
    else fields = ordered.length ? [ordered] : [];

    return {
      streaming: {
        enabled: $("#f-enabled").checked,
        content_lang: $("#f-content-lang").value,
        chat_types: chatTypes,
        panel_expanded: $("#f-panel-expanded").checked,
        width_mode: $("#f-width").value,
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
      },
      display: {
        show_reasoning: $("#f-show-reasoning").checked,
        show_tool_use: $("#f-show-tool-use").checked,
        show_context: $("#f-show-context").checked,
        truncate_model_name: $("#f-truncate-model").checked,
        max_reasoning_panels: int("#f-max-panels"),
        unified_panel_min_duration: num("#f-unified-min-duration"),
        context_display_mode: $("#f-context-mode").value,
      },
    };
  }

  async function loadState() {
    var data = await api("/api/state");
    fillForm(data);
    return data;
  }

  $("#config-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var btn = $("#btn-save");
    btn.disabled = true;
    try {
      var payload = readForm();
      var data = await api("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast(data.changed && data.changed.length ? "已保存：" + data.changed.join("、") : "无配置变化", "ok");
      await loadState();
    } catch (err) {
      toast("保存失败：" + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

  $("#btn-reload").addEventListener("click", function () {
    loadState().then(function () { toast("已重新读取磁盘配置", ""); })
      .catch(function (e) { toast("读取失败：" + e.message, "err"); });
  });

  document.querySelectorAll("#f-footer-fields .chip").forEach(function (c) {
    c.addEventListener("click", function () {
      if (c.disabled) return;
      c.classList.toggle("on");
    });
  });

  /* ---------- 预览 ---------- */
  $("#p-state").addEventListener("change", function () {
    var streaming = $("#p-state").value === "streaming";
    $("#p-outcome").disabled = streaming;
  });

  $("#btn-render").addEventListener("click", async function () {
    var btn = $("#btn-render");
    btn.disabled = true;
    try {
      var payload = {
        scenario: $("#p-scenario").value,
        state: $("#p-state").value,
        outcome: $("#p-outcome").value,
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
      toast("预览失败：" + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- 状态诊断 ---------- */
  function hookBadge(v) {
    if (v === "installed") return '<span class="badge ok">已安装</span>';
    if (v === "not_installed") return '<span class="badge warn">未安装</span>';
    return '<span class="badge mute">不可用</span>';
  }

  function verifyLine(name, v) {
    var cls = v === "compatible" ? "ok" : v === "incompatible" ? "bad" : "mute";
    var txt = v === "compatible" ? "兼容" : v === "incompatible" ? "不兼容" : "不可用";
    return '<div class="status-line"><span>' + name + '</span><span class="badge ' + cls + '">' + txt + "</span></div>";
  }

  function renderStatus(st) {
    var patchedBadge;
    if (st.patched === null || st.patched === undefined) patchedBadge = '<span class="badge mute">未找到 Hermes</span>';
    else if (st.patched) patchedBadge = '<span class="badge ok">已注入</span>';
    else patchedBadge = '<span class="badge warn">未注入</span>';
    var markers = Object.keys(st.markers || {}).map(function (k) {
      var v = st.markers[k];
      return '<span class="badge ' + (v ? "ok" : "bad") + '">' + esc(k) + "</span>";
    }).join(" ");

    $("#status-mount").innerHTML =
      '<div class="status-grid">' +
      '<div class="status-card"><h3>网关补丁</h3>' +
      '<div class="status-line"><span>状态</span>' + patchedBadge + "</div>" +
      (st.target ? '<div class="status-line mono">' + esc(st.target) + "</div>" : "") +
      (markers ? '<div class="status-line">' + markers + "</div>" : "") +
      '<div class="status-line"><span>Cron</span>' + hookBadge(st.cron_hook) +
      "<span>Clarify</span>" + hookBadge(st.clarify_hook) + "</div></div>" +

      '<div class="status-card"><h3>兼容性（verify）</h3>' +
      verifyLine("gateway", st.verify.gateway) +
      verifyLine("cron", st.verify.cron) +
      verifyLine("clarify", st.verify.clarify) + "</div>" +

      '<div class="status-card"><h3>运行配置</h3>' +
      '<div class="status-line"><span>流式卡片</span>' +
      (st.streaming_enabled ? '<span class="badge ok">已启用</span>' : '<span class="badge warn">已停用</span>') +
      "</div>" +
      '<div class="status-line"><span>内容提示语</span><span class="badge mute">' + esc(st.content_lang || "-") + "</span></div>" +
      '<div class="status-line"><span>飞书凭据</span>' +
      (st.credentials ? '<span class="badge ok">已配置</span>' : '<span class="badge bad">缺失</span>') + "</div></div>" +

      '<div class="status-card"><h3>Hermes 环境</h3>' +
      '<div class="status-line mono">' + esc(st.hermes_python || "（未找到 hermes python）") + "</div>" +
      '<div class="status-line mono">' + esc(st.install_dir || "（未找到安装目录）") + "</div></div>" +
      "</div>";
  }

  async function loadStatus() {
    var mount = $("#status-mount");
    try {
      var data = await api("/api/state");
      renderStatus(data.status);
    } catch (err) {
      mount.innerHTML = '<div class="status-card"><div class="status-line">加载失败：' + esc(err.message) + "</div></div>";
    }
  }

  $("#btn-refresh-status").addEventListener("click", loadStatus);

  $("#btn-restart").addEventListener("click", async function () {
    if (!window.confirm("重启 Hermes 网关？运行中的会话会被中断。")) return;
    var btn = $("#btn-restart");
    btn.disabled = true;
    try {
      var d = await api("/api/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (d.ok) toast("网关重启成功", "ok");
      else toast("重启失败：" + (d.error || (d.output || "").slice(-300) || "未知错误"), "err");
      if (d.output) console.log("[restart]", d.output);
      loadStatus();
    } catch (err) {
      toast("重启请求失败：" + err.message, "err");
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- 初始化 ---------- */
  loadState().catch(function (e) { toast("读取配置失败：" + e.message, "err"); });
})();
