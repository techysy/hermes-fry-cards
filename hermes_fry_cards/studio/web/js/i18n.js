/* fry-cards Studio — 界面 i18n（中 / 英，localStorage 记忆，data-i18n 声明式接入）
 *
 * 字典值为 [zh, en]。接入方式：
 *   data-i18n="key"       → 替换 textContent
 *   data-i18n-html="key"  → 替换 innerHTML（含 <code>/<strong> 的富文本提示用）
 *   data-i18n-title="key" / data-i18n-ph="key" / data-i18n-aria="key"
 * 动态文本（toast / 状态页 / 别名编辑器）用 FryI18n.t("key")。
 */
window.FryI18n = (function () {
  "use strict";

  var LANG_KEY = "fry-lang";
  var DICT = {
    /* ---------- 顶栏 / 导航 ---------- */
    "brand.meta": ["配置 <code>~/.hermes/config.yaml</code>", "Config <code>~/.hermes/config.yaml</code>"],
    "title.lang": ["切换到 English", "切换到中文"],
    "title.theme": ["切换亮 / 暗主题", "Toggle light / dark theme"],
    "tab.config": ["配置", "Config"],
    "tab.preview": ["预览", "Preview"],
    "tab.status": ["状态", "Status"],

    /* ---------- 配置页 ---------- */
    "legend.streaming": ["流式卡片", "Streaming Cards"],
    "lbl.enabled": ["启用流式卡片", "Enable streaming cards"],
    "lbl.width": ["卡片宽度 width_mode", "Card width (width_mode)"],
    "lbl.content-lang": ["内容提示语语言", "Content hint language"],
    "opt.zh": ["zh（中文）", "zh (Chinese)"],
    "opt.en": ["en（English）", "en (English)"],
    "lbl.chat-types": ["发卡片的聊天类型", "Chat types for cards"],
    "opt.all": ["全部类型", "All types"],
    "opt.dm": ["仅私聊 dm", "DM only"],
    "opt.group": ["仅群聊 group", "Group only"],
    "lbl.body-size": ["正文文字大小", "Body text size"],
    "lbl.flush-interval": ["流式刷新节流（毫秒）", "Streaming flush interval (ms)"],
    "hint.flush-interval": ["默认 500ms（50~2000）；兼顾打字机流畅感与移动端防卡顿闪退。", "Default 500ms (50~2000); balances smooth typing effect with crash prevention on mobile."],
    "opt.heading": ["heading（大）", "heading (large)"],
    "opt.notation": ["notation（小）", "notation (small)"],

    "legend.statusbar": ["状态栏 · 顶部 Header 与底部 Footer", "Status Bars · Header & Footer"],
    "sub.header": ["Header 顶部状态栏", "Header (top status bar)"],
    "lbl.header-enabled": ["显示 header", "Show header"],
    "lbl.header-min": ["快捷回复去标题阈值（秒，0=始终显示）", "Hide header under duration (s, 0=always show)"],
    "sub.footer": ["Footer 底部元数据栏", "Footer (bottom metadata bar)"],
    "hint.footer": ["ℹ️ 一般不开启：模型 / 耗时 / 上下文等元数据由「统一面板」承载。", "ℹ️ Usually off: model / elapsed / context metadata is carried by the Unified Panel."],
    "lbl.footer-enabled": ["显示 footer", "Show footer"],
    "lbl.footer-label": ["显示字段标签", "Show field labels"],
    "lbl.text-size": ["文字大小", "Text size"],
    "lbl.footer-fields": ["footer 字段", "Footer fields"],
    "tip.fields": ["点按切换显隐（置灰 = 隐藏）；按住拖动调整顺序。", "Click to toggle visibility (grey = hidden); hold and drag to reorder."],
    "fields-note": ["当前为多行字段配置：保存时原样保留，UI 不可编辑。", "Multi-row field config: preserved as-is on save; not editable in the UI."],

    "legend.notice": ["完成通知", "Completion Notice"],
    "lbl.notice-enabled": ["卡片完成后发送提醒消息", "Send a notice when the card completes"],
    "lbl.notice-text": ["提醒文字前缀", "Notice text prefix"],
    "hint.notice": ["通知会回复在卡片消息下，仅在卡片成功收尾后发送；错误会附带「出错」。", "The notice replies under the card message and is only sent after a clean finish; errors are suffixed with “Error”."],

    "legend.panel": ["统一面板（header 字段自由组合）", "Unified Panel (header fields, free combination)"],
    "lbl.panel-expanded": ["完成态面板保持展开", "Keep completed panel expanded"],
    "lbl.show-reasoning": ["展示推理过程", "Show reasoning"],
    "lbl.show-tool-use": ["展示工具调用面板", "Show tool-use panel"],
    "lbl.show-context": ["面板 header 显示上下文", "Show context in panel header"],
    "lbl.context-mode": ["上下文显示格式", "Context display format"],
    "lbl.max-panels": ["最多独立推理面板数（1~50）", "Max separate reasoning panels (1~50)"],
    "lbl.unified-min": ["面板最小耗时阈值（秒，短回复不显示）", "Min panel duration threshold (s, hidden for short replies)"],
    "lbl.truncate-model": ["面板 header 截断长模型名", "Truncate long model names in panel header"],
    "lbl.vendor-logo": ["统一面板厂商 logo（命中模型家族时）", "Vendor logo in unified panel (on model-family match)"],
    "lbl.panel-fields": ["面板 header 字段", "Panel header fields"],
    "lbl.panel-text-size": ["面板 header 字体", "Panel header text size"],
    "hint.panel-fields": ["ℹ️ 思考数/工具数为面板专属计数；上下文跟随上方「显示上下文」开关与显示格式；无数据的字段自动隐藏。", "ℹ️ Reasoning/tool counts are panel-exclusive; context follows the “show context” switch and format above; fields without data are hidden automatically."],

    "legend.alias": ["模型别名（统一面板 header 模型名）", "Model Aliases (panel header model name)"],
    "lbl.alias-enabled": ["启用别名（关闭则回落 ⇲ 截断）", "Enable aliases (falls back to ⇲ truncation)"],
    "hint.alias": [
      '键 = 模型名子串（不区分大小写，按添加顺序首中即生效）；值填显示名，或点 🕐 加<b>时段人设</b>——按北京时间 HH:MM + 星期自动切换（如 DeepSeek 峰谷：峰段梁文锋⚡️ / 谷段梁文谷⚡️）。改完下一条消息即热生效。',
      "",
    ],
    "btn.add-alias": ["＋ 添加别名", "＋ Add alias"],
    "hint.more": ["ℹ️ 查看说明", "ℹ️ Details"],
    "alias.empty": ["暂无别名——模型名将按 ⇲ 截断显示。", "No aliases — model names will be ⇲-truncated."],
    "ph.alias-key": ["匹配子串，如 deepseek", "Match substring, e.g. deepseek"],
    "ph.alias-name": ["显示名，如 梁文谷⚡️", "Display name, e.g. LiangWengu⚡️"],
    "ph.rule-name": ["该时段显示名", "Display name for this slot"],
    "ph.alias-default": ["默认显示名", "Default display name"],
    "alias.default-row": ["其他时间（以上规则都不命中时）：", "Other times (when no rule above matches):"],
    "preset.work": ["工作日", "Workdays"],
    "preset.weekend": ["周末", "Weekend"],
    "btn.add-rule": ["＋ 时段规则", "＋ Time-slot rule"],
    "title.alias-time": ["时段人设", "Time-slot persona"],
    "title.del": ["删除", "Delete"],
    "confirm.alias-static": ["转回静态名称将丢弃全部时段规则，仅保留默认名。继续？", "Switching back to a static name discards all time-slot rules, keeping only the default name. Continue?"],

    "legend.gsb": ["🛡️ 群聊安全边界（modular Hermes 0.21+）", "🛡️ Group Security Boundary (modular Hermes 0.21+)"],
    "lbl.gsb-enabled": ["启用（群聊回复套输出边界，私聊 DM 不受影响）", "Enable (wraps group replies with an output boundary; DMs unaffected)"],
    "lbl.gsb-allow": ["豁免群白名单（一行一个 chat_id，oc_xxx；填入的群不套边界）", "Exempt group allowlist (one chat_id per line, oc_xxx; listed groups skip the boundary)"],
    "hint.gsb": [
      '开启后群聊回复将被约束：<b>不透露 API key / 密码 / 令牌 / 内网 IP / 凭据 / 私人信息</b>，被群友索要时婉拒并引导私聊。边界走每轮 ephemeral prompt 注入（不碰持久 system prompt），hook 逻辑在插件内，<code>hermes update</code> 不会覆盖；改完下一条消息生效，必要时 <code>hermes gateway restart</code>。豁免群适合多 Agent 协作群。详见 README「群聊安全边界」。',
      "",
    ],

    "btn.reload": ["放弃修改，重新读取", "Discard & reload"],
    "btn.save-preview": ["保存并预览", "Save & preview"],
    "btn.save": ["仅保存", "Save only"],

    /* ---------- 字段 chips ---------- */
    "field.status": ["状态", "Status"],
    "field.elapsed": ["耗时", "Elapsed"],
    "field.speed": ["速度", "Speed"],
    "field.cache": ["缓存命中", "Cache hit"],
    "field.model": ["模型", "Model"],
    "field.tokens": ["Tokens", "Tokens"],
    "field.context": ["上下文", "Context"],
    "field.model_p": ["🍟 模型", "🍟 Model"],
    "field.reasoning_p": ["💭 思考数", "💭 Reasoning"],
    "field.tools_p": ["🔧 工具数", "🔧 Tools"],
    "field.elapsed_p": ["⏱️ 耗时", "⏱️ Elapsed"],
    "field.speed_p": ["⚡ 速度", "⚡ Speed"],

    /* ---------- 预览页 ---------- */
    "hint.preview": [
      '服务端用<strong>真实 builder</strong> 渲染（与线上卡片同一代码路径），非前端模拟。未保存的表单改动不会参与渲染；统一面板阈值等深层项以<strong>已保存配置</strong>为准。',
      "",
    ],
    "lbl.scenario": ["场景", "Scenario"],
    "opt.sc-workflow": ["工作流交错（思考+工具+答案）", "Interleaved workflow (reasoning+tools+answer)"],
    "opt.sc-short": ["快捷回复（短耗时）", "Quick reply (short)"],
    "opt.sc-tables": ["多表格压力（第6张起压缩）", "Table stress (6th+ compacted)"],
    "opt.sc-longtext": ["超长文本（18KB 截断）", "Extra-long text (18KB clamp)"],
    "lbl.state": ["状态", "State"],
    "opt.completed": ["完成态", "Completed"],
    "opt.streaming": ["流式中", "Streaming"],
    "lbl.outcome": ["结果", "Outcome"],
    "opt.oc-done": ["✅ 完成", "✅ Completed"],
    "opt.oc-error": ["❌ 出错", "❌ Error"],
    "opt.oc-aborted": ["🛑 中止", "🛑 Aborted"],
    "btn.render": ["渲染预览", "Render preview"],
    "preview.empty": ["点击「渲染预览」查看卡片", "Click “Render preview” to see the card"],

    /* ---------- 状态页 ---------- */
    "btn.refresh": ["刷新", "Refresh"],
    "btn.restart": ["重启网关", "Restart gateway"],
    "status.loading": ["加载中…", "Loading…"],
    "confirm.restart": ["重启 Hermes 网关？运行中的会话会被中断。", "Restart the Hermes gateway? Running sessions will be interrupted."],
    "st.patched-gw": ["网关补丁", "Gateway patches"],
    "st.state": ["状态", "State"],
    "st.patches-installed": ["已注入", "Injected"],
    "st.patches-missing": ["未注入", "Not injected"],
    "st.no-hermes": ["未找到 Hermes", "Hermes not found"],
    "st.installed": ["已安装", "Installed"],
    "st.not-installed": ["未安装", "Not installed"],
    "st.unavailable": ["不可用", "Unavailable"],
    "st.verify": ["兼容性（verify）", "Compatibility (verify)"],
    "st.compatible": ["兼容", "Compatible"],
    "st.incompatible": ["不兼容", "Incompatible"],
    "st.runtime": ["运行配置", "Runtime config"],
    "st.streaming": ["流式卡片", "Streaming cards"],
    "st.on": ["已启用", "Enabled"],
    "st.off": ["已停用", "Disabled"],
    "st.content-lang": ["内容提示语", "Content hints"],
    "st.creds": ["飞书凭据", "Feishu credentials"],
    "st.configured": ["已配置", "Configured"],
    "st.missing": ["缺失", "Missing"],
    "st.env": ["Hermes 环境", "Hermes environment"],
    "st.plugin-ver": ["插件版本", "Plugin version"],
    "st.unknown": ["未知", "Unknown"],
    "st.no-cli": ["未找到 CLI", "CLI not found"],
    "st.no-python": ["（未找到 hermes python）", "(hermes python not found)"],
    "st.no-dir": ["（未找到安装目录）", "(install dir not found)"],
    "st.loadfail": ["加载失败：", "Load failed: "],

    /* ---------- toast / 动态 ---------- */
    "toast.badjson": ["响应不是 JSON (HTTP ", "Response is not JSON (HTTP "],
    "toast.reloaded": ["已重新读取磁盘配置", "Config reloaded from disk"],
    "toast.readfail": ["读取失败：", "Read failed: "],
    "toast.saved": ["已保存：", "Saved: "],
    "toast.nochange": ["无配置变化", "No config changes"],
    "toast.savefail": ["保存失败：", "Save failed: "],
    "toast.aliases": ["别名 {} 条", "{} aliases"],
    "toast.previewfail": ["预览失败：", "Preview failed: "],
    "toast.restart-ok": ["网关重启成功", "Gateway restarted"],
    "toast.restart-fail": ["重启失败：", "Restart failed: "],
    "toast.restart-reqfail": ["重启请求失败：", "Restart request failed: "],
    "toast.unknown-err": ["未知错误", "Unknown error"],

    /* ---------- preview.js 渲染占位 ---------- */
    "pv.element": ["元素 ", "Element "],
    "pv.empty": ["(无元素)", "(empty)"],
  };

  // 富文本类 key（取 innerHTML 而非 textContent）
  DICT["hint.warn"] = [
    '⚠️ 保存会<strong>重写整个 config.yaml（YAML 注释会丢失）</strong>；写前自动备份到 <code>~/.hermes/backups/fry_studio/</code>（保留 20 份）。只写白名单键，你手写的其他配置原样保留。保存后需 <code>hermes gateway restart</code> 生效。',
    '⚠️ Saving <strong>rewrites the whole config.yaml (YAML comments will be lost)</strong>; an automatic backup goes to <code>~/.hermes/backups/fry_studio/</code> (20 kept). Only whitelisted keys are written — your other settings stay intact. Takes effect after <code>hermes gateway restart</code>.',
  ];
  DICT["hint.alias"][1] =
    'Key = model-name substring (case-insensitive; first match in insertion order wins). Value = display name, or click 🕐 for a <b>time-slot persona</b> — switches automatically by Beijing time HH:MM + weekday (e.g. DeepSeek peak/valley). Hot-applies from the next message.';
  DICT["hint.gsb"][1] =
    'When enabled, group replies are constrained: <b>never reveal API keys / passwords / tokens / internal IPs / credentials / private info</b>, and politely decline + redirect to DM when asked. The boundary rides the per-turn ephemeral prompt (the persistent system prompt is untouched); hook logic lives in the plugin so <code>hermes update</code> won\'t overwrite it. Applies from the next message; run <code>hermes gateway restart</code> if needed. Exempt groups suit multi-agent collaboration. See README "Group Security Boundary".';
  DICT["hint.preview"][1] =
    'Server-side rendering with the <strong>real builder</strong> (same code path as live cards) — not a frontend mock. Unsaved form edits do not participate; deep settings like the unified-panel threshold follow the <strong>saved config</strong>.';

  var lang = "zh";
  try { lang = localStorage.getItem(LANG_KEY) || "zh"; } catch (e) { /* 隐私模式忽略 */ }
  // 调试/分享用：?lang=en 或 #lang=en 直接覆盖（不写入 localStorage）
  var m = (location.search + "&" + location.hash.replace(/^#/, "")).match(/lang=(zh|en)/);
  if (m) lang = m[1];

  function t(key) {
    var v = DICT[key];
    if (!v) return key;
    var s = lang === "en" ? v[1] : v[0];
    return s || v[0];
  }

  var HTML_KEYS = {
    "brand.meta": true, "hint.warn": true, "hint.alias": true, "hint.gsb": true, "hint.preview": true,
    "hint.footer": true, "hint.panel-fields": true, "fields-note": true,
  };

  function apply(root) {
    (root || document).querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (HTML_KEYS[key]) el.innerHTML = t(key);
      else el.textContent = t(key);
    });
    (root || document).querySelectorAll("[data-i18n-title]").forEach(function (el) {
      el.title = t(el.getAttribute("data-i18n-title"));
    });
    (root || document).querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      el.placeholder = t(el.getAttribute("data-i18n-ph"));
    });
    (root || document).querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria")));
    });
    document.documentElement.setAttribute("lang", lang === "en" ? "en" : "zh-CN");
    var btn = document.getElementById("lang-toggle");
    if (btn) btn.textContent = lang === "en" ? "中" : "EN";
  }

  function setLang(l) {
    lang = l === "en" ? "en" : "zh";
    try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* ignore */ }
    apply();
  }

  return { t: t, apply: apply, setLang: setLang, getLang: function () { return lang; } };
})();
