/* fry-cards Studio — 工具箱页签（AI 摘要 / 文字翻译）
 *
 * 只读 /api/state 的 tools 段 + POST /api/tools；不写配置、不碰卡片链路。
 * 凭据始终留在服务端：这里的请求体只有文本、模式、语言、模型名与超时。
 */
(function () {
  "use strict";

  var S = window.FryStudio;
  if (!S) return;
  var $ = function (sel) { return document.querySelector(sel); };
  var esc = S.esc;
  var I18N = S.i18n;

  var toolsState = null;   // /api/state.tools
  var lastResult = "";     // 结果区纯文本（复制 / 「搬到输入」用）

  var elMode = $("#t-mode");
  var elLang = $("#t-lang");
  var elLangWrap = $("#t-lang-wrap");
  var elModel = $("#t-model");
  var elTimeout = $("#t-timeout");
  var elInput = $("#t-input");
  var elInputCount = $("#t-input-count");
  var elOut = $("#t-output");
  var elMeta = $("#t-meta");
  var elEndpoint = $("#tools-endpoint");
  var btnRun = $("#btn-tool-run");

  /* ---------- 端点状态条（只说可用性与来源标签，绝不显示 key） ---------- */
  function renderEndpointBar() {
    if (!elEndpoint) return;
    if (!toolsState) { elEndpoint.hidden = true; return; }
    elEndpoint.hidden = false;
    if (toolsState.available) {
      elEndpoint.className = "hint tools-endpoint ok";
      elEndpoint.textContent = I18N.t("tools.endpoint-ok").replace("{}", toolsState.source || "-");
    } else {
      elEndpoint.className = "hint tools-endpoint warn";
      elEndpoint.textContent = I18N.t("tools.endpoint-missing");
    }
  }

  /* ---------- 模型下拉：候选来自服务端（端点侧清单），可留空由服务端挑 flash ---------- */
  function fillModels() {
    if (!elModel) return;
    var prev = elModel.value;
    var models = (toolsState && toolsState.models) || [];
    elModel.innerHTML = "";
    var auto = document.createElement("option");
    auto.value = "";
    auto.textContent = I18N.t("opt.tool-model-auto");
    auto.title = (toolsState && toolsState.default_model) || "";
    elModel.appendChild(auto);
    models.forEach(function (m) {
      var o = document.createElement("option");
      o.value = m.id;
      o.textContent = m.label || m.id;
      elModel.appendChild(o);
    });
    var still = Array.prototype.some.call(elModel.options, function (o) { return o.value === prev; });
    elModel.value = still ? prev : "";
  }

  function fillLangs() {
    if (!elLang) return;
    var prev = elLang.value;
    elLang.innerHTML = "";
    var auto = document.createElement("option");
    auto.value = "";
    auto.textContent = I18N.t("opt.lang-auto");
    elLang.appendChild(auto);
    ((toolsState && toolsState.target_langs) || []).forEach(function (l) {
      var o = document.createElement("option");
      o.value = l.id;
      o.textContent = l.label;
      elLang.appendChild(o);
    });
    var still = Array.prototype.some.call(elLang.options, function (o) { return o.value === prev; });
    elLang.value = still ? prev : "";
  }

  function syncMode() {
    var translate = elMode.value === "translate";
    elLangWrap.hidden = !translate;
    if (translate) renderEmpty(I18N.t("tools.hint-translate"));
    else renderEmpty(I18N.t("tools.empty"));
  }

  function renderEmpty(text) {
    if (lastResult) return;  // 已有结果就别被切换清掉显示（结果区自己会更新）
    elOut.innerHTML = '<div class="preview-empty">' + esc(text) + "</div>";
  }

  function renderResult(data) {
    lastResult = String(data.text || "");
    var box = document.createElement("div");
    box.className = "tools-result";
    box.textContent = lastResult;
    elOut.innerHTML = "";
    elOut.appendChild(box);
    renderMeta(data);
  }

  function renderMeta(data) {
    var usage = data.usage || {};
    var parts = [];
    if (data.model) parts.push(data.model);
    if (typeof data.elapsed_ms === "number") parts.push((data.elapsed_ms / 1000).toFixed(1) + "s");
    var pin = usage.prompt_tokens, pout = usage.completion_tokens;
    if (typeof pin === "number" || typeof pout === "number") {
      parts.push("↑" + (pin || 0) + " ↓" + (pout || 0) + " tok");
    }
    if (data.target_lang) parts.push("→ " + data.target_lang);
    if (!parts.length) { elMeta.hidden = true; return; }
    elMeta.hidden = false;
    elMeta.textContent = I18N.t("tools.meta") + parts.join(" · ");
  }

  function renderError(message) {
    lastResult = "";
    elMeta.hidden = true;
    elOut.innerHTML = '<div class="tools-error">' + esc(message) + "</div>";
  }

  function updateCount() {
    var n = elInput.value.length;
    var max = (toolsState && toolsState.max_input_chars) || 20000;
    elInputCount.textContent = n + " / " + max;
    elInputCount.classList.toggle("over", n > max);
  }

  /* ---------- 运行 ---------- */
  async function run() {
    var text = elInput.value.trim();
    if (!text) { S.toast(I18N.t("tools.need-input"), "err"); return; }
    var max = (toolsState && toolsState.max_input_chars) || 20000;
    if (text.length > max) {
      S.toast(I18N.t("tools.too-long").replace("{}", max).replace("{}", text.length), "err");
      return;
    }
    btnRun.disabled = true;
    var started = Date.now();
    elMeta.hidden = false;
    elMeta.textContent = I18N.t("tools.running");
    try {
      var payload = {
        mode: elMode.value,
        text: text,
        model: elModel.value,
        timeout_sec: parseFloat(elTimeout.value) || 20,
      };
      if (elMode.value === "translate") payload.target_lang = elLang.value;
      var data = await S.api("/api/tools", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!data.ok) {
        renderError(data.error || I18N.t("tools.unknown-fail"));
        S.toast(I18N.t("tools.failed"), "err");
        return;
      }
      renderResult(data);
      S.toast(I18N.t("tools.done").replace("{}", ((Date.now() - started) / 1000).toFixed(1)), "ok");
    } catch (err) {
      renderError(err.message || String(err));
      S.toast(I18N.t("tools.failed"), "err");
    } finally {
      btnRun.disabled = false;
    }
  }

  /* ---------- 复制 / 搬到输入 ---------- */
  async function copyResult() {
    if (!lastResult) { S.toast(I18N.t("tools.no-result"), ""); return; }
    try {
      await navigator.clipboard.writeText(lastResult);
      S.toast(I18N.t("tools.copied"), "ok");
    } catch (e) {
      // 非安全上下文（http://192.168.x.x）下 Clipboard API 不可用：回落选中复制
      var box = document.createElement("textarea");
      box.value = lastResult;
      box.style.position = "fixed";
      box.style.opacity = "0";
      document.body.appendChild(box);
      box.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      document.body.removeChild(box);
      S.toast(ok ? I18N.t("tools.copied") : I18N.t("tools.copy-fail"), ok ? "ok" : "err");
    }
  }

  function swapToInput() {
    if (!lastResult) { S.toast(I18N.t("tools.no-result"), ""); return; }
    elInput.value = lastResult;
    elMode.value = elMode.value === "summary" ? "translate" : "summary";
    syncMode();
    updateCount();
  }

  /* ---------- 事件 ---------- */
  if (btnRun) btnRun.addEventListener("click", run);
  if (elInput) elInput.addEventListener("input", updateCount);
  if (elMode) elMode.addEventListener("change", syncMode);
  var btnCopy = $("#btn-tool-copy");
  if (btnCopy) btnCopy.addEventListener("click", copyResult);
  var btnSwap = $("#btn-tool-swap");
  if (btnSwap) btnSwap.addEventListener("click", swapToInput);

  /* ---------- 服务端状态接入 ---------- */
  S.onStateLoaded(function (tools) {
    toolsState = tools || null;
    renderEndpointBar();
    fillModels();
    fillLangs();
    if (tools && typeof tools.timeout_sec === "number" && !elTimeout.dataset.touched) {
      elTimeout.value = String(tools.timeout_sec);
    }
    updateCount();
  });

  if (elTimeout) elTimeout.addEventListener("input", function () { elTimeout.dataset.touched = "1"; });

  updateCount();
  syncMode();
})();
