# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.6.2] - 2026-11-02

### 新增 / Added
- **群聊非管理员工具隔离**（#55）：群聊里非管理员的轮次直接不下发写入/执行类工具集，
  Prompt 诱导再强也调不到不存在的 function calling（硬防御，与边界提示的软防御互补）。
  `gateway.group_security_boundary.enabled: true` 即生效：默认黑名单剔除
  `execute_code` / `terminal` / `file` / `computer_use`，查询类（web_search、memory、
  clarify …）默认可用；`non_admin_allowed_toolsets` 为严格白名单、
  `non_admin_disabled_toolsets` 在默认黑名单之上追加，**黑名单永远优先于白名单**
  （配置笔误打不开边界）；过滤结果同步并入 `agent.disabled_toolsets` 双保险。管理员
  判定复用 Hermes 斜杠命令的同一套 `allow_admin_from`（`gateway.slash_access`），
  判不了（旧版无模块 / scope 未配置 / 异常）一律不隔离，宁可漏防不可误伤。注入在
  `gateway/run_turn.py` 的 `_resolve_turn_toolsets` 返回前（可选注入点 `TOOL_GATING`，
  modular 专用），前台轮次与后台任务两条路径全覆盖。豁免群沿用 `allow_chats`；
  Config 暴露两个新名单，Studio 校验白名单与状态采集接通。**注意**：已建会话的 agent
  按构建时工具集缓存，启用后需重启网关（或对群聊 `/new`）才收紧存量会话；本版新增
  注入点，升级必须重跑 `uninstall + install`。

### 修复 / Fixed
- **完成态诊断日志补全树递归元素数**（#56）：新增 `count_elements_recursive`——飞书
  300305 校验的是全树递归节点总数，此前日志的 `card_elements` 只统计顶层
  （`len(body.elements)`），统一面板嵌套工具组/思考面板时严重低估（现场
  `card_elements=2` 实际全树超 200），一直误导排查。封卡失败与剥离面板重发的日志
  现带 `recursive=` 字段。

---

## [0.6.1] - 2026-10-31

### 修复 / Fixed
- **委派（子任务）完成回执上屏网关内部标记行**（#54）：委派类后台完成事件（`delegate_task`
  子任务批次）走 `BG_COMPLETION` 通道时 `command` / `started_at` 等字段全空，
  `background_completion_text` 兜底取去包裹后的首行——但委派通知不是 `[IMPORTANT: …]`
  形态，首行仍是 `[ASYNC DELEGATION BATCH COMPLETE — …]` 内部标记，直接上屏。兜底分支
  现识别内部标记：首行形如 `[大写标记 …]`（标记词全大写；`[2026-10-30 …]` 时间戳以数字
  开头不命中）或含 `BATCH COMPLETE` 就不上屏，改给通用句 `✅ 后台任务已完成`；shell
  后台任务的 `[IMPORTANT: …]` 拆包裹首行兜底照旧透传。

### 测试 / Tests
- 重建缺失的 `tools/clarify_gateway.py`：`tests/test_clarify_buttons.py` 随 0f3bf76 入库
  但依赖的 `tools/clarify_gateway.py` 从未提交，任何新 checkout 上全量 pytest 收集即失败。
  按 `tests/samples/` 归档的真实 Hermes 源码用法 + 插件侧 `clarify.py` 调用点反推 API 契约
  重建（`register` / `wait_for_response` / `resolve_gateway_clarify` /
  `get_pending_for_session` / `resolve_text_response_for_session` / `mark_awaiting_text` /
  `clear_session` / `get_clarify_timeout` / `CANCELLED` / `SKIPPED`，及插件直接访问的
  `_lock` / `_entries`）；模块注明为随仓参考实现，部署环境由 Hermes 自带 tools 包提供。
  `conftest.py` 把仓库根加入 sys.path，裸 pytest 也能导入。全量 **1062 passed**，
  ruff / mypy 零新增。

---

## [0.6.0] - 2026-10-29

### 修复 / Fixed
- **reasoning 面板无长度上限打满飞书客户端渲染**（#52）：`stream_element` 是覆盖语义全文重发，
  reasoning 分支此前无 clamp，生产实测单次推送峰值 28.8KB、单轮累计 1.3MB，客户端每秒重绘
  20KB+ markdown 直至卡顿无响应。流式 reasoning 与完成态统一面板的 reasoning 全文现按尾部
  窗口钳制到 `_REASONING_MAX_BYTES`（10KB，头部摘要 + 尾部结论 + 省略标记，两处观感一致）；
  `adapt_payload_size` 新增元素级分档（单元素全文 >8KB → ≥1s、>16KB → ≥2s，按最大单元素判定
  而非 flush 累加和），元素档位优先于载荷档位。
- **长工具轮次封卡必撞 300305 + 降级替换序号重号回 300317**（#53）：完成态整卡重建把工具面板
  子元素连同流式期已提交的一份重复计数，30 步全量面板即越过 200 硬上限；封卡降级替换复用
  删 loading 已占用的序号，被服务端判 300317，降级卡自己也换不上。三处修复：完成态统一面板
  内的工具组按紧凑渲染（每步只留标题行，子元素数压回 1/3）；封卡撞 300305 时先按
  `strip_unified_panel` 剥离统一面板重发一次（正文与状态优先入卡）；降级替换序号改为
  `session.sequence += 2` 跳过删 loading 占用的 N+1。
- **飞书瞬态错误后重试陷入 300301 Duplicate ID 死锁**（#51）：飞书服务端偶发瞬态异常（如 300308）
  重试后，元素在云端实际已成功落地但客户端保持 `created=False`。下次 flush 重发 `add_elements`
  被飞书返回 `300301 Duplicate ID` 拒绝，导致整批更新被原子拒绝并陷入死锁假死。在 `_do_batch_update`
  中增加对 `300301` 的提取与自愈恢复路径：自动将对应段纠正标记为 `created=True` 并补齐计数，
  下轮 flush 自动转为 `partial_update` 或静默态恢复正常流式更新。
- **Hermes 0.22.5+1713 锚点适配**：`run_notifications.py` 中 `_run_process_watcher` 变为 wrapper、
  实际逻辑移入 `_watch_process`；`_find_bg_busy_suppress_site` 的宿主函数扩展为
  `("_watch_process", "_run_process_watcher")` 顺序查找。

### 新增 / Added
- **流式刷新节流默认值调优为 500ms**：默认刷新间隔由 100ms 调整为 500ms（2 Hz），兼顾打字机流畅感与移动端/鸿蒙系统防卡顿闪退，大幅降低高频全卡重绘对客户端 UI 线程的压力；载荷自适应降频分档同步收拢为 ≥500ms 与 ≥800ms。
- **Studio 暴露流式刷新节流设置**：前端流式设置卡片中新增「流式刷新节流（毫秒）」输入项，
  绑定 `streaming.flush_interval_ms`（默认 500ms，50~2000ms 范围）；增加兼顾打字机流畅感与移动端防卡顿的提示。
- **统一面板 header 字体可配**：新增 `display.panel_text_size`（默认 `notation`，与此前写死值一致；
  可选 `normal_v2` / `normal` / `heading`）。此前统一面板 header 字号在 builder 硬编码，Studio 无法
  设置。`build_complete_card` 增加 `panel_text_size` 参数（封卡 / 完成态两个调用点同步）；
  Studio 的「统一面板」区新增「面板 header 字体」下拉，校验（`textsize` 同族）、状态采集、
  真实 builder 预览全链路接通，预览渲染按 `header.text_size` 应用字号（补了缺失的 `.ts-heading`）。

### 测试 / Tests
- 新增 `tests/test_issue51_duplicate_id_recovery.py`（Duplicate ID 错误提取与自愈死锁解除）。
- `test_patcher_modular.py` 补充宿主为 `_watch_process` 时的注入测试。
- `TestAdaptiveElementThrottle`（元素级降频分档与边界）、reasoning 钳制与元素档位触发
  （TestDoFlush）、`TestCompactToolPanel` / `TestStripUnifiedPanel`（紧凑面板、应急剥离、
  完成态 reasoning 钳制）、`TestCompletePanelStripRetry`（300305 剥离重发 / 非 300305 保留
  面板）、降级替换序号断言（TestOverflowFallbackReplace）。全量 **1035 passed**，ruff / mypy 零新增。

---

## [0.5.10] - 2026-10-26

cherry-pick 自本地补丁线 `local/0.5.9.4`（#47 / #49 / #50 实测反馈修复，作者已在部署上验证）：

### 新增 / Added
- **提示单槽位 + 流式期即时可见**（#50 / #47）：提示改为单槽位（后到覆盖先到，同一时刻卡上最多一条，
  忙碌 / 重定向 / 后台回执共用）。建卡时提示画进真实流式元素（`fry_notice`），flush 新增「通知槽位
  同步」一步：目标文案与画在元素上的不一致就当场补一次元素更新（无可复用元素时在 loading 之前
  `add_elements` 补画 `fry_notice_live_N`），回执到达当下即可见，不必等封卡重建。
- **后台回执用户文案**（#47）：`on_background_completion` 从 evt 透传 `command` / `started_at` /
  `exit_code`，卡片不再上屏模型侧内部文案（`[IMPORTANT: ...]`），改拼
  `✅ 后台任务已完成 — 命令 (Ns)`（缺字段时回退去包裹首行）；独立回执卡去掉 header（不再有
  `✅ Background: "..."` 标题），正文第一行就是内容本身，`config.summary` 仅作会话列表摘要。
- **turn_busy 纯文本回执抑制**（#47）：新增可选注入点 `BG_BUSY_SUPPRESS`（`_run_process_watcher`
  concise 文案合成之后、`_send_watcher_message` 之前）。回执已由卡片接管时按 session_id /
  session_key / started_at 登记，10 分钟窗口内命中即 `break` 跳过网关补发的 concise 纯文本；
  拿不到键、登记过期或插件禁用一律 False，网关照常发，功能无损降级。

### 修复 / Fixed
- **一次重定向触发两次 on_interrupted**（#49）：REDIRECT 与 REDIRECT_RESOLVED 对同一事件各调一次，
  第二次到达时 `old_message_id` 已被换成刚建好的新会话（同 id），原幂等判据分不出来，会再建第二个
  会话、提示写进随后被 dispose 的那个。`on_interrupted` 增加按 `new_message_id` 的 2 秒去重窗口
  （重复调用记 `interrupt_duplicate_skipped` 直接返回）+ 已建新会话复用
  （`_reuse_interrupted_session` 只补登记、不抢键）。
- **封卡后正文末尾残留进行中提示**（#50）：首段正文原地接管提示元素（复用同一 element id，不再
  旁边新增一段）；封卡渲染前 `drop_rendered_progress_notices` 把已画在卡上的提示（含后台回执）
  从完成态正文剔除——流式时显示、封卡后被正文覆盖；从未落到卡片元素上的提示仍保留在完成态
  通知位。接管过提示元素的正文段换卡重建时重派元素 id（`reassign_element_id`），避免新卡
  `fry_notice` 双元素撞 id。

### 测试 / Tests
- 新增 `tests/conftest.py`：`HERMES_HOME` 隔离到临时目录，测试夹具不再污染生产
  `state/fry-cards-live.json`。
- 提示单槽位 / 接管 / 封卡剔除、interrupt 去重、busy 抑制登记等状态级回归与真实源码锚点回归。
  本线全量 997 passed；`BG_BUSY_SUPPRESS` 真实源码回归补 `_SAMPLE_225` 归档样张候选，
  Windows 开发机不再永远 skip。

---

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.5.9] - 2026-10-25

v0.5.8 实测反馈两连修：合成轮次的 `streamed=False` 抑制误报（#48）与后台完成通知接管（#47，0.5.8 release notes「尚未覆盖」项正式落地）。

### 新增 / Added
- **后台完成通知接管**（#47）：新增可选注入点 `BG_COMPLETION`，挂在 `gateway/run_notifications.py`
  的 `_inject_watch_notification` 内部标记处理之前。watch/completion 通知不再以纯文本气泡直达
  （该路径不经过卡片管线，无 START 钩子可拦）：会话此刻有活跃卡（按 `session_key`，其次唯一
  活跃卡）时按 #43 通知分区并入（完成后通知置顶），无活跃卡时复用合成轮次的静态卡路径
  （`send_card_to_chat`，不 reply 已过期的触发消息 #52694）；都不可行时返回 False，Hermes 照常
  注入，无损降级。modular 布局自动发现 `gateway/run_notifications.py`（0.22.5 真实源码回归）。

### 修复 / Fixed
- **合成轮次的 `streamed=False` 重复投递误报**（#48）：合成轮次（后台完成通知触发的回复轮）
  没有 `event_message_id`，`mark_stream_delivered` 此前拿到 None 导致谓词恒 False，网关误打
  `Normal final-send NOT suppressed` 警告。`mark_stream_delivered` 与 `stream_card_healthy`
  均增加 `session_key` 兜底（syn-* 会话注册时已同步进 `_session_keys`），谓词按活跃卡健康
  状态正确回答。**升级需重跑 `uninstall` + `install`**（answer 钩子注入模板有变更）。

### 测试 / Tests
- 合成轮次按 session_key 解析、无任何标识返回 False、`stream_card_healthy` 双参数（#48）；
  `run_notifications` 存根注入、真实 0.22.5 源码上 `BG_COMPLETION` 落在内部标记之前（#47）；
  通知接管的状态级回归（活跃卡并入 / 静态卡 / chat 不匹配 / 投递失败降级）。全量 **947 passed**。

---

## [0.5.8] - 2026-10-24

v0.5.7 实测反馈两连修：#43 第二轮（连续打断、合成 id 回执、提示分区渲染）与 #46（长任务把飞书客户端渲染打满直至被系统杀掉）。

### 新增 / Added
- **连续打断换卡**（#43 反馈）：新增可选注入点 `REDIRECT_RESOLVED`，挂在 `_steer` 解包后、忙碌回执 30s 防抖之前——同一回合连续第二次打断时回执被防抖吞掉，原 REDIRECT 锚点走不到，旧卡会停在半截；现在判定一成立就换卡，与回执锚点幂等。
- **合成轮次 id 识别**（#43 反馈）：后台完成回执的 `["completion", ...]` 序列 id 不再按真实消息 reply（99992354 失败后标 FAILED），识别后卡片直接投聊天（send_card_to_chat），会话键保留原 id。
- **网关流消费者投递标记**（#43 反馈）：答案增量进卡后把 `has_durably_delivered_text` 谓词挂到 Hermes 的 stream consumer 上，按 fry 卡片健康状态回答，消除 `Normal final-send NOT suppressed ... streamed=False` 重复投递警告；封卡失败时补标文本回退，最终回复仍由网关补发。
- **节流暴露为配置**（#46）：新增 `streaming.flush_interval_ms`（默认 100，向 50~2000 收口），此前 `CARDKIT_MS = 0.100` 硬编码无出口；安卓设备为主的部署可设 300~500。studio 状态面板同步展示。
- **按载荷自适应降频**（#46，默认开启的保护网）：每次 flush 载荷 >16KB 节流窗口放大到 ≥300ms、>40KB ≥500ms，卡片拆小后自动回落；永不低于用户配置的基准。
- **工具面板只渲染最近 30 步**（#46）：header 保留总步数，更早的折叠成一行「…另有 N 步工具调用」；估算端按同一上限截取，长任务里卡片体积有上界，同时缓解 300305 与拆卡压力。

### 修复 / Fixed
- **提示区展示位分区**（#43 反馈）：提示直接渲染 Hermes 原文，不再套「💡 提示」标签；忙碌回执 / 重定向这类「本轮进行中」的提示落卡片底部（footer 之前），后台回执这类「完成后」的通知留在顶部。
- **重定向封卡清提示**（#43 反馈）：`on_interrupted` 收尾旧卡时清掉进行中提示，同一句话不再在新旧两卡各出现一遍；完成后的后台回执保留。

### 测试 / Tests
- `test_notice_in_card.py` 补充：提示分区（busy/redirect 底部、background 顶部）、合成 id 建会话、
  流消费者投递标记、重定向封卡清提示、空文案换卡；`test_patcher_real_v022.py` 补充
  `REDIRECT_RESOLVED` 在 0.22.0 / 0.22.5 真实源码上落在防抖之前的回归；`test_patcher.py` 补充
  answer 钩子挂载流消费者标记。
- #46：自适应降频分档与边界（`test_flush.py`）、`flush_interval_ms` 配置收口（`test_config.py`）、
  面板截断 + 估算一致（`test_cardkit.py`）、节流接线与载荷降频集成（`test_controller.py`）；
  rollover 记账回归改用带输出的 exec 步骤（轻步骤面板截断后有界、不再拆卡）。
- 全量 **936 passed**，ruff / mypy 相对上一版零新增。

---

## [0.5.7] - 2026-10-23

修复工具面板元素记账（#41），长工具轮次不再静默撞 200 元素上限后整轮回落纯文本；忙碌回执 / 重定向 / 后台回执改为写进当前卡片（#43）。

### 新增 / Added
- **提示消息入卡**（#43）：Hermes 在回合中合成的忙碌回执 / 重定向 / 后台回执不再另发纯文本，改为写进当前卡片的通知区（完成态卡顶 `💡 提示`，流式态建卡带最新一条）。文案完全沿用 Hermes 原文，插件不另写。
  - 归属解析沿用 `_sessions` / `_session_keys`：`message_id` → `session_key` → 唯一活跃卡；多卡且无标识时放弃，让 Hermes 照常发原文。
  - 重定向沿用 `on_interrupted` + `_interrupt_map` 换卡，旧卡收尾、新卡接手。
  - 新增 `BUSY_ACK` / `REDIRECT` 两个可选注入点：旧版 Hermes 缺锚点时跳过注入，`verify` 不报错。

### 修复 / Fixed
- **工具面板增量恒为 0**（#41）：`_do_flush` 提前推进 `session.tool_panel_estimate`，批更新前的
  快照读到的已是新值，增量永远为 0，面板元素从不进 `element_count`，`ELEMENT_THRESHOLD` 主动拆卡
  一次也不触发。改为只在 `_do_batch_update` 成功后推进。
- **拆卡后新卡重发旧卡步骤**（#41）：dirty 分支写死 `start = 0`，新卡面板从全量步骤渲染。新增
  `panel_steps_start()`，三处分支统一取本卡首个工具段的 `tool_offset`。
- **强制拆卡不推进 `split_index`**（#41）：300305 后 `split_index` 前移到首个未创建 segment，
  完成态 `active_segments()` 不再重渲已封印内容（否则再次超限、重试失败后整轮回落纯文本）；
  日志补 `split_idx=`。
- **拆卡判断重复计入面板**（#41）：`_maybe_rollover_tool_segment` 以恒为 0 的 `seg.element_estimate`
  为基准，每步把整块面板重复计入，约 17 步就拆卡。改为以本卡已入账的面板总量为基准。
- **面板占满时拆卡永不触发**：`find_tool_split_offset` 返回 `None` 后直接放弃，改为按 step 边界
  强制拆，本卡至少保留 1 个 step。
- **拆出只有 loading 的空白卡**：拆卡前容量判断把旧面板重复算进 `new_el_total`，新卡首个工具段又是
  整体替换面板。改为按「替换后的面板估算」计算并扣掉旧面板。
- **通知区多条提示共用固定 `element_id=fry_notice`**（#43）：命中两条提示时卡内元素 id 冲突；
  改为按序号派生 `fry_notice_1`、`fry_notice_2`。
- **通知区元素未计入 `element_count`**（#43）：接近 300305 时会少算一格；建卡基线与完成态重建
  都按提示条数同步记账。
- **网关重启后遗留卡片一直转圈**（#43）：活跃卡登记到 profile 的 `state/fry-cards-live.json`，
  进程启动时把「登记过但不属于本进程」的卡封成「已中断」；单条登记连续 3 个进程封不掉即出列。
- **Hermes 0.22.3+ 装不上 `BUSY_ACK` / `REDIRECT`**（#43）：回执发送迁到了
  `_busy_interrupt_and_ack`，锚点按 `_handle_active_session_busy_message` → `_busy_interrupt_and_ack`
  顺序查找；此前可选锚点缺失会静默跳过，两个 marker 一个都没注入。
- **触发压缩封卡**：会话发生上下文压缩（会话分裂、in-place compaction 等）时主动封印旧卡，
  避免卡片停留在流式转圈；新增可选注入点 `COMPRESSION` 挂载在 `Session split detected` 处。
- **钩子调用签名别名兼容**：`on_turn_redirected` 允许接收 `message_id` 作为别名并兼容缺失的 `chat_id`；
  `on_busy_ack` 允许接收或不接收 `ctrl`，兼容镜像烘入的不同模板调用形状。

### 测试 / Tests
- 新增 `tests/test_element_budget.py`（#41 状态级回归）与 `tests/test_notice_in_card.py`（#43）。
- 真实 Hermes v0.22.0 / v0.22.5 源码上的锚点与 `apply` → `remove` 回归。

---

## [0.5.5] - 2026-10-22

日志降噪：回复目标消息已失效（99992354 / 1000023 / 231003 / 230011）不再打完整堆栈。

### 修复 / Fixed
- **目标消息失效时日志被 traceback 刷屏**：后台任务完成通知（completion，`proc_xxx`）往原消息
  回卡片时，若原消息已删除/撤回/`open_message_id` 失效，飞书返回
  `99992354 not a valid open_message_id`。异常本已捕获并降级纯文本、功能无损，但
  `card_reply_failed` 与 `background card delivery failed` 两处都用 `exc_info=True`
  打了完整堆栈（warning + traceback 各一条，实测一天数次）。
  新增 `MSG_ID_INVALID = 99992354` 并纳入 `_TERMINAL_MESSAGE_CODES`（与既有
  1000023 消息不存在、231003 已删除、230011 已撤回同组）；命中终端码时只留一行
  warning（`target_message_gone` / `skipped`），不再带堆栈。真正的 API/凭据/限流错误
  维持 `exc_info=True` 不变，避免降噪掩盖真问题。功能行为（纯文本兜底 + `mark_failed`）不变。

### 测试 / Tests
- 新增 3 条用例：99992354 只产生一条无堆栈 warning 且仍降级纯文本；非终端码（如 230020）
  仍带堆栈；`on_background_deliver` 遇到目标失效时单行 warning 且返回 False。
- 修复 `test_clarify_buttons.py::test_patch_application_does_not_import_optional_toast`：
  其引用的 `clarify._TOAST_MODULE` 在重构中丢失，导致整轮 pytest INTERNALERROR；
  补回常量并改为经 `__import__(_TOAST_MODULE, ...)` 懒加载 toast 类（保持惰性导入语义）。

## [0.5.4] - 2026-10-20

修复 #37 无事件续轮回复投递两次；修复 #38 适配 Hermes 微模块重构（run_turn_progress / run_turn_persistence）。

### 修复 / Fixed
- **无事件续轮的回复投递两次（纯文本 + 合成卡）**（#37）：0.5.3 的 `FOLLOWUP_DONE` 只在续轮
  结束后补卡，而无事件续轮（后台通知 / steer）在流式阶段没有 inbound id、插件没有会话 →
  `on_answer_delta` 返回 False → Hermes 走自己的纯文本流式投递，收尾再建合成卡就重复了。
  改为在续轮递归 `_run_agent` 之前预建卡片（新增注入点 1c `on_queued_followup_card_ready`）：
  已存在则复用，无消息 id 落合成卡并按 session_key 注册（与核心 `next_session_key` 同源表达式）；
  `FOLLOWUP_DONE` 的无 id 分支改为按同一 session_key 复用该卡。预建失败只降级为原行为
  （debug 日志），不影响封卡与纯文本放行。
- **适配 Hermes 微模块重构**（#38）：新版 Hermes 把 `progress_callback` 拆到
  `gateway/run_turn_progress.py`、`hooks.emit("agent:end")` 拆到 `gateway/run_turn_persistence.py`，
  `verify` 报 `Missing modular Hermes injection targets: tool` 且 agent:end 断言失败。
  `_default_modular_paths()` 按存在与否动态注册 `progress` / `persistence`（旧版布局不受影响）；
  tool 锚点优先 `progress`、回退 `runner`，verify 与注入共用同一解析顺序；agent:end 在 `turn`
  与 `persistence` 任一命中即可；无注入点的 `persistence` 原样跳过不写回。
  **升级注意**：`FOLLOWUP_COMPLETE` 与 `FOLLOWUP_DONE` 注入块内容均有变化，必须重跑
  `uninstall` + `install`。

### 测试 / Tests
- 预建复用 / 预建建卡 / 禁用控制器返回 None（`test_followup_completion.py`）。
- 注入顺序：预建必须落在封上一张卡之后、递归 `_run_agent` 之前——真实 v0.22.0 源码 + 手写夹具双覆盖。
- 微模块 progress / persistence 布局：verify 通过、TOOL 钩子落 progress 不落 runner、persistence
  不被写回、marker 恰好一次、apply 幂等、remove 逐字节还原（`test_patcher_modular.py`）。
- 全量 839 passed（基线 830 + 9 条新测试）。

### 内部 / Internal
- 新增 `StreamCardController.resolve_session_id(message_id, session_key)`。
- `Patcher(modular_paths=...)` 校验放宽为必需模块 ⊆ 传入，并拒绝未知模块名（防拼写错误让锚点
  静默回退到旧模块）。

---

## [0.5.3] - 2026-10-15

修复 #34 拆卡后工具面板 300313 抖动；修复 #35 排队续轮回复回退纯文本。

### 修复 / Fixed
- **拆卡后工具面板批更新指向不存在的元素**（#34）：`split_tool_segment` 给拆出的新段分配
  `tools_{c}`，与 `_new_tool` 的共享面板 `tool_panel` 不一致——卡片上不存在该元素，批更新
  必报 `300313 not find elementID`，回滚重建后下一轮又回落，形成「失败 → 重建 → 再失败」
  抖动。拆段改为沿用 `tool_panel`；dirty 分支兜底改用 `TOOL_PANEL_ELEMENT_ID`，避免将来
  新路径再指向不存在的元素。既有 `test_issue3_tool_panel_stale.py` 的断言
  （`"tool_panel" in str(a) or stale_el_id in str(a)`）允许回落到 `tools_N`，故未能拦住，
  新增用例断言在真实 action payload 上并显式拒绝任何 `tools_*`。
- **排队续轮的回复回退纯文本**（#35）：续轮由 `_run_agent_queued_followup` 递归
  `_run_agent` 执行，不经过 `_handle_message_with_agent`，因而没有自己的 COMPLETE 钩子，
  卡片停在「处理中」、回复按默认路径发成纯文本。
  **修正诊断**：续轮并非「对插件完全不可见」——流式钩子挂在 `TurnRunner` 上，而
  `_run_agent` 每次调用都重建 `TurnRunner`，递归续轮照样进钩子。新增
  `on_queued_followup_completed` 封续轮自己的卡并写回 `already_sent` / `final_response`
  抑制重复投递；**封卡失败则放行纯文本**，避免整轮静默；无 inbound id 的无事件续轮
  （后台通知 / steer）落到合成卡 `syn-*`；中断轮不改写。
  **升级注意**：新增 `FOLLOWUP_DONE` 注入 marker，必须重跑 `uninstall` + `install`。

### 测试 / Tests
- 新增 `test_patcher_real_v022.py`：对着真实 Hermes v0.22.0 源码跑完整 patcher 流程
  （锚点齐全 / 产出合法 Python / 每个 marker 恰好一次 / 重复 apply 幂等 / remove 逐字节
  还原），并钉住两个 follow-up 钩子共用锚点时的注入顺序。取源沿用既有约定：本地安装优先
  → 否则下载钉死的 tag → 否则 skip。
- 新增 `test_split_tool_segment_panel_id.py`（#34 回归）、`test_followup_completion.py`
  （#35 四例：封卡 / 中断 / 失败放行 / 非 dict）。

### 内部 / Internal
- 修正 `patcher.py` 中 `specs` 的类型标注（`object` → `Callable`）：原标注使
  `finder(tree, lines)` 不可调用，此前 mypy 未扫到该路径。

---

## [0.5.2] - 2026-10-13

修复 #32：收尾闸门打回重写时卡片正文保留旧稿。

### 修复 / Fixed
- **闸门打回后旧稿残留卡片**（#32）：答案被 Hermes 收尾闸门打回重写时，流式旧稿已写进
  ANSWER 段，完成钩子只在「完成文本是流式段后缀」时去重，段首旧稿被当作合法历史保留，
  封卡后正文出现「旧稿 + 定稿」两段。新增 `answer_rewritten` 替换信号一路透传：网关完成
  钩子按本轮 `agent_messages` 的 `finish_reason=verify_hook_continue` 判定（`patcher.py`），
  `on_completion_answer(..., replaced=True)` 用定稿整段替换最近一个 ANSWER 段，旁白等更早
  的段保留；默认去重口径（后缀/前缀/重叠）完全不变。
  **升级注意**：钩子内容有变，需重跑 `uninstall` + `install` 刷新注入块。

---

## [0.5.1] - 2026-10-12

借鉴 lark-hls-v2 流式经验，优化流式渲染排版、高频 flush 性能与任务生命周期管理。

### 优化 / Improved
- **流式 Markdown 防碎字截断**（`truncate_unclosed_markdown`）：流式推送阶段自动截断未闭合的代码块（```）、行内代码（`）、粗体（**）和悬挂斜体（*），避免飞书端渲染时出现排版撕裂与碎字跳动；最终完成态封卡时保留完整原文。
- **流式处理链 LRU 缓存**（`prepare_streaming_markdown`）：引入 `@lru_cache(maxsize=128)` 缓存标题降级、表格压缩与未闭合截断处理结果，大幅减少高频 flush 期间大文本的重复正则替换与解析开销。
- **FlushController 任务生命周期追踪**：保留后台 `asyncio.Task` 强引用（`_pending_flush_tasks`），防止 Python GC 回收未决任务；`wait_for_flush` 同步等待全部后台任务完成。

### 修复 / Fixed
- **Windows 路径跨平台测试断言**（`tests/test_paths.py`）：修复反斜杠路径与正斜杠标签比较差异，全套 812 个测试全绿。

---

## [0.5.0] - 2026-10-10

集中修复 @849506054 报告的 6 条 issue（#17–#22，全部带实测数据与根因定位）；
版本号直接落在 0.5.0，中间的 0.4.8–0.4.11 未单独发布。

### 修复 / Fixed
- **reasoning delta 拖慢长回复 87–262s**（#17）：`on_reasoning()` 每个 delta 都重读并
  `yaml.safe_load` 整份 config.yaml（20.8KB ≈ 64.6ms/次，py-spy 采样 68% 时间停在
  `_reload`）。`PluginConfig._reload()` 改为以 `(mtime_ns, size)` 为键缓存解析结果——配置
  真被改（`/reasoning` 运行时改写）才重解析，稳态零开销；`controller.on_reasoning()` 把
  `_resolve_session` 判定提到 `show_reasoning` 之前（与 `on_answer` 同序），非飞书会话
  直接早退。实测同回复 agent 耗时 138.5s → 7.9s。
- **官方镜像布局下 cron / clarify 钩子静默跳过**（#20）：`_code_roots()` 只认
  `<HERMES_HOME>/hermes-agent` 与 `/usr/local/lib/hermes-agent` 两种 home 布局，官方
  Docker 镜像（源码在 `/opt/hermes`）下 `CronPatcher` 找错文件、`ClarifyPatcher` 直接
  抛错，install 均 try/except 静默跳过——表面装好，实际 cron 卡片与交互按钮全程缺失。
  现在追加从已安装 `gateway` 包位置反推的真实代码根（`importlib.util.find_spec`），
  home 布局优先级不变；`verify` 三目标在官方镜像下全部 Compatible。
- **cron 卡片头部显示未来时间**（#21）：头部时间取的是 `job.next_run_at`（下一次计划
  时刻），`*/10` 的 job 永远差一个周期。改为投递时刻
  `datetime.now().astimezone().isoformat()`（`patch.py::on_cron_deliver` 内覆盖，
  无需重装注入生效）。
- **卡片内 markdown 表格长单元格被裁字**（#18）：飞书富文本表格按可用宽度折行但行高
  固定不涨，折出的第 2、3 行被硬裁（三列以上、单元格文本偏长时移动端基本必现）。
  渲染管线改为把表格片段输出为**原生 `table` 元素**（`columns[].width: "auto"` +
  `row_height: "auto"`，超宽自动可滑动），文本片段仍走 markdown 元素——完成态卡片
  ANSWER 段、`build_cron_card()`、`build_background_card()` 三处共用
  `_append_content_elements()` 统一入口。超限兜底沿用 `_MAX_CARD_TABLES = 5`
  的 `_downgrade_tables()` 字段列表（第 6 个表格起仍无损压缩）。
- **完成态卡片正文出现两遍**（#19）：流式丢尾（卡片只推到答案前缀就收尾）时，
  `on_completion_answer` 的 `endswith` 守卫只认「流式段以完整完成文本结尾」，判假后
  把完整文本 `_new_answer` 追加成第二段 ANSWER，封卡后同一段内容出现两遍。新增
  去重叠分支：完成文本与最后一段 ANSWER 是前缀/重叠关系（≥ `_MIN_OVERLAP_TAIL = 8`
  字）时原地接上而非追加；旁白段与 `_completion_answers` 重放去重行为不变
  （#13 契约回归测试锁定）。
- **表密集型长回复拆卡估算低估，根治 300305 复发**（#22 请求 2）：
  `estimate_answer_elements()` 重写为**两条路径取大者**——流式路径（1 个 markdown
  元素 + 表格按 `(1+数据行)×列` 展开服务端单元格（表头行此前漏计）+ 每张 markdown
  图片 1 个 img 元素（此前完全未计））与完成态路径（与 #18 的
  `_append_content_elements` 渲染链逐步对齐：原生 table 1 元素/表 + 文本按
  `_MAX_CHUNK_CHARS` 切块数）的保守上界；估算/渲染共用同一套
  `split_markdown_tables`/`parse_table_block`，不再各扫一遍。
- **Studio 胶囊导航宽屏下与内容区错位**：tab 条原用固定 `24px` 边距贴视口左边，
  而 `main` 是 `max-width` 居中布局——宽屏下胶囊超出内容区左边界。改为与 main
  同一公式的 `max(24px, calc((100vw - max-width)/2 + 24px))` 左边距（960/1380 两档
  断点同步），并加 `max-width` 防窄屏溢出。

### 新增 / Added
- **Studio 界面中英文 i18n**：顶栏新增语言切换按钮（主题切换左侧，`fry-lang`
  localStorage 记忆；`?lang=en` / `#lang=en` 调试直切不写存储）。新增
  `studio/web/js/i18n.js` 声明式字典模块（`data-i18n` textContent /
  `data-i18n-html` 富文本 / title / placeholder / aria-label），静态文案、字段
  chips 标签、toast 提示、状态诊断页、模型别名编辑器（含星期 chips）、预览渲染器
  `pickText` 语言偏好全部接入；语言切换不重建 DOM，chips 显隐状态与拖动顺序保留。

### 变更 / Changed
- **Studio 顶栏与内容区同宽居中**：`.topbar` 由全宽贴边改为 `max-width: 960px`
  （≥1280px 时 1380px）居中——品牌区左缘与右侧 GitHub / 语言 / 主题按钮组右缘
  均与内容区对齐，不再横跨视口。
- **Studio 配置页首行压矮**：「模型别名」（矮容器）与「状态栏 · Header/Footer」
  （高容器）互换位置，首行变为 流式卡片 / 模型别名 / 完成通知——第一屏可见
  有效内容明显增加。
- **Studio 信息密度优化（渐进披露 + 瀑布流）**：「模型别名」「群聊安全边界」
  两个低频区块改为默认折叠的 `details` 卡片，摘要行带启用状态徽标（已启用绿 /
  已停用灰，跟随主开关实时同步、随界面语言切换）；两处多行长说明收进
  「ℹ️ 查看说明」折叠条；配置区由 grid 改为 CSS 多列瀑布流（窄屏 1 列 /
  常规 2 列 / ≥1280px 3 列），卡片按自然高度排列——折叠卡收起后同列后续
  卡片自动上移，不留行高对齐造成的空洞。
- **Studio 字段调整改为 CreditDaddy 同款组件设置**：footer 与统一面板 header 两组
  字段由「复选框 + ↑↓ 箭头」改为 chips 组件——点按切换显隐（置灰 = 隐藏，`aria-pressed`
  同步）、按住拖动调整顺序（HTML5 DnD，换行感知的中线落点判定 + 左侧墨条落点提示）。
  chips 由 `FIELD_DEFS` 单源渲染；多行 footer 配置的只读锁定行为不变。

### 变更 / Changed
- **300305 估算对照日志**（#22 请求 1）：`cardkit_batch_update` 失败告警新增
  `new_est=[el_id~estimate,...]` 逐项估算；`complete attempt N failed` 告警新增
  `card_elements`（完成态重建卡片真实元素数，含长文切块二次膨胀）与 `tracked`
  （流式期追踪估算）两个字段——两条日志配对即可定位低估来自流式估算还是完成态重建。

### 新增 / Added
- **封卡失败整卡降级替换**（#22 请求 3）：3 次 `complete` 重建均失败（典型 300305）
  后，在 loading 清理与 `close_streaming` 之外追加一步：用
  `build_overflow_fallback_card()`（红色 header + 双语提示）整卡替换失控的流式卡，
  提示用户完整内容以纯文本在下方送达，避免聊天里出现「半封卡片 + 纯文本」两张内容
  重叠的实体。

---

## [0.4.7] - 2026-09-29

### 变更 / Changed
- **Studio 图标全面 SVG 化**：字段列表 ↑↓ 调序按钮改为 SVG chevron，主题切换按钮
  （月亮 / 太阳）与 GitHub 顶栏图标同步替换文字/emoji——随主题色 `currentColor` 渲染，
  暗色下不再出现彩色 emoji 与界面割裂；操作按钮补 `aria-label`。

---

## [0.4.6] - 2026-09-29

### 新增 / Added
- **Studio 字段 chips 拖动排序**：footer 与统一面板两组字段 chips 支持按住拖动调整顺序
  （原生 HTML5 Drag & Drop，无依赖）；保存按容器内实际顺序写入 `streaming.footer.fields` /
  `display.platforms.feishu.panel_fields`（替代原先固定 FIELD_ORDER 排序）；重新打开 Studio
  时按已保存顺序回填重排 chips（未选字段保持相对顺序跟在后面）。多行 fields 配置下 chips
  仍整体禁用（不可点也不可拖）。

---

## [0.4.5] - 2026-09-29

### 新增 / Added
- **包首发 PyPI**：[pypi.org/project/hermes-fry-cards](https://pypi.org/project/hermes-fry-cards) —
  支持 `pip install hermes-fry-cards` 直接安装（升级 `pip install -U hermes-fry-cards`），
  安装后仍需 `verify` → `install` 注入并重启网关。

### 变更 / Changed
- **主色柔化**：黑白主题的操作色由纯黑 `#1b1c1f` 调整为石墨蓝灰 `#39404a`（暗色下反白
  同步柔化为 `#e7e8ea`），激活 tab / 选中 chip / 主按钮增加同色系轻阴影，缓解高对比
  生硬感；checkbox、toast、按钮 hover 全部随变量自动过渡。纯 CSS 变量调整，无功能变化。

---

## [0.4.4] - 2026-09-28

### 变更 / Changed
- **Studio 视觉体系对齐 CreditDaddy**（纯前端，功能与配置键不变）：
  - 黑白主操作色（激活 tab / 选中 chip / 主按钮黑底白字，暗色下自动反白）
  - 顶栏重构：🍟 logo + 大标题 + `v版本 · 配置路径` 副行，右侧 GitHub / 主题切换图标按钮
  - 配置 / 预览 / 状态改为独立**胶囊导航条**（白底圆角容器 + 激活黑胶囊）
  - fieldset 去边框**卡片化**（大圆角 + 浅阴影），提示条同款卡片化
  - 新增**亮 / 暗双主题**（右上角 🌙/☀️ 切换，localStorage 记忆，head 内联预设防刷新闪白）
  - 全量颜色变量化（暗色下输入框 / 别名编辑器 / 状态卡 / toast 全套适配）

---

## [0.4.3] - 2026-09-28

### 修复 / Fixed
- **Studio「面板 header 字段」chips 点击无反应** — 0.4.2 新增的统一面板字段选择区漏绑
  click 事件（footer 字段区已有），点击无法切换选中态；补齐事件绑定，勾选/取消恢复正常，
  保存排序（PANEL_FIELD_ORDER）与 `display.platforms.feishu.panel_fields` 写入链路不变。

---

## [0.4.2] - 2026-09-28

### 新增 / Added
- **footer 新增 `speed`（tok/s）与 `cache`（缓存命中 %）字段** — 口径与 Hermes CLI 状态栏一致：
  速度 = 滚动近 10 次 API 调用 `sum(output)/sum(latency)` 纯生成吞吐（剔除工具执行时间）；
  命中率 = `cache_read / prompt_tokens`（prompt 已含缓存，同 `turn_usage.py` 归一化）。
  数据取自 gateway 活跃 agent（`_running_agents` → `_agent_cache` tuple 解包）best-effort
  采集（`patch.collect_stream_stats`），零读取/无 agent 时字段自动隐藏（无数据 ≠ 0%）。
  默认 footer 布局变为 `[status, elapsed, speed, cache, context, model]`。
- **统一面板 header 字段化，与 footer 共用字段池** — 新增 `display.platforms.feishu.panel_fields`
  （有序一维列表，默认 `[model, reasoning, tools, context, elapsed]` 与历史布局逐字一致）；
  面板专属字段 `reasoning`（💭 推理轮数）/ `tools`（🔧 工具步数）零计数自动隐藏，
  共享字段（speed/cache/tokens/status 等）经 footer 渲染器回落；面板 header 标题补 i18n
  （英文 `Cache hit {}%` / 中文 `缓存命中 {}%`）。
- **Studio 字段自由组合** — footer 与统一面板各一组 chip 开关（参考 CreditDaddy 仪表盘
  组件开关交互），勾选即组合、保存排序；预览（真实 builder）footer_data 带
  `208 tok/s` / `缓存命中 54%` 样例；统一面板字段区含 9 个可选字段。
- **上下文显示格式 `bar`/`block` 支持面板 header 复用** — 面板 context 渲染与 footer
  共用 `_context_progress_bar` 等助手，`block`/`block_text` 废弃样式照旧回落。

### 修复 / Fixed
- **Studio footer 字段白名单缺 `speed`/`cache`** — GUI 勾选新字段保存会被 400 拒绝；
  `_FOOTER_FIELDS` 校验白名单补齐（含测试回归）。

### 升级注意
- 注入模板变更（完成钩子/排队 follow-up 钩子新增 stats 采集），**已装环境须重跑
  `uninstall` + `install`**；旧模板不传新键，controller 侧向后兼容（字段自动隐藏）。

---

## [0.4.1] - 2026-09-28

### 变更 / Changed
- **Studio 默认监听 `0.0.0.0:8765`**（原 `127.0.0.1`）— `_cmd_studio` 默认值、`run_studio_server` 默认值、
  `systemd/hermes-fry-cards-studio.service` 的 `ExecStart` 三处同步；本机仍打印/打开 `http://127.0.0.1:8765`
  （新增 `_display_host` 把通配地址回落 loopback，避免浏览器拿到 `http://0.0.0.0:8765`），
  局域网用 `http://<本机IP>:8765` 访问。
- Host 门白名单**可配置**：新增 `studio.allowed_hosts`（附加网段前缀或完整主机，默认仅 loopback），
  取代硬编码网段；非白名单来源照旧 403；README / README.en 补「Studio 可写
  config.yaml 与飞书凭据，别做端口转发或公网暴露」警告与 `--host 127.0.0.1` 回退方式。

### 修复 / Fixed
- **完成态卡片丢失最终答案**（[#13](https://github.com/techysy/hermes-fry-cards/issues/13)）—
  多工具回合中工具间旁白形成 ANSWER 段后，完成态注入被旧 guard 整体跳过；现改为完成态答案
  追加为新 ANSWER 段（保住交错时间线），`endswith` 去重 + 完成重试防重放。
- **cron 投递 230001 invalid receive_id**（[#14](https://github.com/techysy/hermes-fry-cards/issues/14)）—
  Hermes 0.21.0 起部分调度布局的 delivery 是 dict，旧钩子仅走 `getattr` 取不到 `chat_id`；
  现 dict/attr 双路径 + `delivery → locals → target` 三级优先，跨调度布局解析投递身份。
- **clarify toast 导入拖爆 adapter 10s 加载预算**（[#15](https://github.com/techysy/hermes-fry-cards/issues/15)）—
  `apply_patch` 在 adapter 导入期执行，toast 类的 SDK 导入等效顶层导入（冷进程 9–16s）；
  改为首次回调需要时惰性加载（哨兵缓存，旧 SDK 缺失静默降级），adapter 导入不再触碰 SDK。
- **Studio 正文大字号**（[#16](https://github.com/techysy/hermes-fry-cards/issues/16) 部分）—
  字号白名单补 `heading` 档并在 Studio 暴露。

### 新增 / Added（补录）
- **完成通知**（[#16](https://github.com/techysy/hermes-fry-cards/issues/16) 部分）—
  `streaming.completion_notice` / `completion_notice_text`（默认关）：卡片收尾后以回复形式
  发送「回答结束 · 耗时」短通知（CardKit 更新不触发飞书提醒的兜底）。

### 新增 / Added
- **Studio systemd 用户服务模板** — 新增 `systemd/hermes-fry-cards-studio.service`，支持登录/系统启动后自动启动、异常退出自动重启和 `journalctl` 日志；默认监听 `0.0.0.0:8765`（本机 `127.0.0.1:8765`），不会自动打开浏览器。
- **Studio 自启文档** — README 与英文文档补充安装、启用、状态检查和日志查看命令。
- `TestWildcardBindDefaults`：守住三处默认绑 `0.0.0.0`（CLI / `run_studio_server` / systemd 模板）
  与 `_display_host` 通配地址回落 loopback。

### 验证 / Tests
- 验证服务模板使用 Hermes venv Python、`--no-browser` 和 `Restart=on-failure`。
- `pytest tests/ -q` 713 passed / 0 failed（含 4 例默认绑定守护）。

---

## [0.4.0] - 2026-09-20

> **算法与架构大改版**：渲染层引入 Markdown 防爆引擎（无损表格压缩 + 字节级内容预算），
> 新增 **Studio 可视化配置工作坊**（真 builder 预览 / 白名单安全写回 / 状态诊断），
> 模型别名支持时段人设（与 openclaw/claw-fry-cards 格式互通），群聊安全边界可视化。
> **配置 schema 向后兼容**——升级无需改任何现有配置；纯新增能力，无破坏性变更。

### 优化 / Changed
- **完成态卡片统一面板按真实工作流交错渲染**（[#10](https://github.com/techysy/hermes-fry-cards/pull/10)，感谢 @JasonXX89）—
  统一面板内从「所有思考集中在前、所有工具堆在末尾」改为按真实发生顺序交错展现：
  `💭 思考1 → 🔧 工具组1 → 💭 思考2 → 🔧 工具组2 …`，保持复杂 Agent 执行链路的时间线一致性。
- **工具面板标题状态感知（生成中「工具执行中」 vs 终态「工具执行」）** —
  流式生成阶段工具折叠面板标题为 `🔧 工具执行中 · N 步`，卡片生成完毕后自动转为 `🔧 工具执行 · N 步 (Xs)`。

### 新增 / Added
- **Markdown 防爆引擎**（借鉴 [aiduPOP](https://github.com/monkey2jack/aiduPOP) 贝氏降级引擎）—
  - **无损表格压缩**：超限表格（>5）不再包装为代码块，改为 fence 感知扫描后压缩为
    「**Table N · Row M**」字段列表，内容完整保留且可读性更好；inline code/转义中的竖线
    与未闭合代码围栏均正确处理；引擎异常时自动回退旧代码块方案。
  - **字节级内容预算 `clamp_utf8`**（18KB ≈ 6000 汉字）：飞书卡片 JSON ~30KB 上限此前仅靠
    元素数估算防护，单段超长 answer 可在拆卡触发前先撞上限。流式进行中渐进截断
    （preserve_tail=False），完成态封卡首 60% + 尾 40% 双保（结论不丢）；
    覆盖 `build_complete_card` / `build_cron_card` / `build_background_card` 与流式 answer flush。
- **瞬态错误码扩充**：重试集合新增频控 `230020` 与开放平台频率限制 `99991400`（限流类错误短退避重试即可恢复）。
- **内容层文案双语支持**：正文内嵌提示语（表格转换引导、两条截断提示）纳入 `i18n.py` 词条表，
  新增 `streaming.content_lang`（`zh`/`en`，默认 `zh`）构建时选取——UI 词条走 `i18n_content` 双语 dict
  由飞书客户端按 locale 渲染，markdown 正文只接受纯字符串无法如此，故按部署方偏好定死（需重启网关生效）。
- **Studio 可视化配置工作坊**（`python -m hermes_fry_cards studio`，借鉴 [aiduPOP](https://github.com/monkey2jack/aiduPOP) studio）—
  纯 stdlib `http.server` + 原生前端，三页签：**配置**（白名单键表单编辑，按「流式卡片 / 状态栏
  （Header 与 Footer 合并一组、组内分节，Footer 分节带「一般不开启」提示）/ **统一面板**
  ——模型·推理·工具·上下文全套开关** / 模型别名 / 🛡️ 群聊安全边界（`gateway.group_security_boundary`
  白名单只写 enabled/allow_chats，手写 `text` 与 gateway 兄弟键原样保留；置底）」分组——
  含多行 footer 字段保护与异常值原样回传；时段规则行两段式布局（星期一行[含工作日/周末快捷选择] /
  时间+名称一行，弹性均分任何列宽不换行；兜底默认名「其他时间」置于规则列表底部）；宽屏 ≥1280px 三列布局）/ **预览**（服务端调**真实 builder** 渲染，与线上卡片同一代码路径——快捷回复 / 工作流
  交错 / 多表格压缩 / 超长截断 × 流式·完成·出错态；overrides 只作用于渲染不落盘）/ **状态**（hook 注入
  markers、cron/clarify、三目标 verify 兼容性、凭据、一键重启网关）。
  写回安全五件套：严格校验 400 / 解析失败拒写 409（保护凭证）/ 写前备份轮转 20 份 / 白名单深合并
  （手写键存活）/ tmp+fsync+rename 原子落盘；安全面仅 loopback、Host 门防 DNS rebinding、无 CORS、
  body ≤1MB（413 有界排空防客户端写端 RST）、`nosniff`。
  ⚠️ 保存重写 config.yaml 会丢 YAML 注释（UI 显著提示）；`display.*` 展示类键保存免重启。
- **模型别名时段人设 + Studio 可视化编辑**（参考 [claw-fry-cards](https://github.com/techysy/claw-fry-cards) 的
  openclaw `modelAliases` 配置，**格式逐字兼容，同一份 JSON 两边通用**）——
  - `model_aliases.json` 值支持字符串或对象 `{name, timeAliases:[{days, start, end, name}]}`：按**北京时间
    （固定 UTC+8，与宿主机时区无关）** HH:MM + 星期自动切换显示名（DeepSeek 峰谷价：峰段梁文锋⚡️ / 谷段
    梁文谷⚡️）。`days` 数组（0=周日）或字符串 `"1-5"`/`"0,6"`/`"1-5,0"`，时间窗 `[start,end)` 左闭右开、
    跨午夜、起止相等=全天，规则首中即返、不命中回落 `name`——语义与 claw `resolveModelAlias` 逐字对齐。
  - 新增总开关 `display.model_aliases_enabled`（默认开；关闭整体回落 ⇲ 截断，配置保留，热更新）。
  - Studio 配置页新增「模型别名」组：条目增删、🕐 时段人设模式（默认名 + 星期芯片 + 起止时间 + 时段名）、
    空键行不提交；经 `GET/POST /api/aliases` 走严格校验（重复键/越界 days/非 HH:MM/未知字段 → 400）、
    解析失败拒写 409、写前备份轮转、原子落盘——安全面与 config.yaml 保存完全一致。

### 修复 / Fixed（Studio 真机走查）
- **preview.js 写入缺失**导致 `app.js` 因 `window.FryPreview` 未定义整体挂掉（表单不填充、页签无响应）——补文件 + 新增全资源可达性测试。
- **setVal 对 number input 误走 `<select>.options 分支**崩溃（`Array.prototype.forEach called on null`），表单填充死在 header 阈值行。
- **CB0 代码围栏占位符**被 `trim()` 吃掉两端空格后正则失配，代码块泄漏 ` CB0 ` 文本——改 trim 后匹配。

### 测试 / Tests
- 新增 `tests/test_md_guard.py` 20 用例（扫描器/压缩/钳制/错误码/文案语言）+ `content_lang` 配置 5 用例
  + 模型别名解析 16 用例（claw 同款时间锚点：半开区间/跨午夜/legacy days/固定 UTC+8/总开关优先级）
  + `tests/test_studio.py` 91 用例（校验/拒写/备份/白名单/Host 门/路径穿越/1MB 上限/预览/别名端点/
  群聊安全边界端点/静态资源可达性）。
  本地全量 **681 passed**（Windows，另 2 例 Win 路径断言差异为存量）；容器 Hermes v0.21.3 全量回归
  **701 passed** + Studio 服务冒烟（静态 4×200 / state / preview schema 2.0 / 外来 Host → 403）。

---

## [0.3.3] - 2026-09-12

### 新增 / Added
- **Clarify 选项按钮卡片**（[#7](https://github.com/techysy/hermes-fry-cards/pull/7)，感谢 @iMissNan）—
  补齐飞书适配器缺失的 `send_clarify` 原生交互（官方 `base.py` 明确「有原生按钮的平台 SHOULD override」）：
  单选 = 每选项一个按钮 + 「✏️ 其他」；多选 = toggle 勾选框 + 「提交选择」+ 「其他」；
  点击后就地变绿显示「✅ 已收到你的选择」；「其他」→ 卡片切换为输入态（form 容器内嵌 input，飞书 V6.8+）→
  提交后解出自定义文本。防呆：本体若未来原生实现 `send_clarify`，`verify` 会拒绝注入。

### 修复 / Fixed
- **审批卡片点击无反应（鉴权误用群准入策略）**（[#7](https://github.com/techysy/hermes-fry-cards/pull/7)）—
  症状：命令审批卡点「允许/拒绝」无反应，日志 `Unauthorized approval click`。
  根因：本体 `_handle_approval_card_action` 用 `_allow_group_message`（群消息**准入策略**，管群里谁能发言）
  校验按钮点击，未配置 per-chat 规则的部署会拒绝**所有**人工点击；而异步解析器 `_resolve_approval` 里
  本来就有正确的 `_is_interactive_operator_authorized` 二次校验——同步这道门既用错又冗余。
  修法：以交互操作者校验为准（thread-local bypass 进入原处理器，校验不过仍拒绝，**安全面未放松**），
  并补全四类点击 toast：受理（success）/ 拒绝（warning）/ 过期（warning，原版静默吞掉）/ 无权限（error）。
- **300313 恢复时工具面板 stale 后永不重建**（[#3](https://github.com/techysy/hermes-fry-cards/issues/3)）—
  工具面板是**经建卡骨架预置**的共享元素（`tool_panel_created=True`）。300313 回滚 stale segment 后，
  下一轮 flush 因 `tool_panel_created` 已为 True 落入 TOOL 分支的 `else`——仅翻 `created`/`dirty` 标志、
  **不发任何 action**。实测后果比原报告更严重：恢复轮整轮 flush 发出 **0 次 `batch_update`**
  （无 add 也无 partial_update），面板在卡片上持续缺失，而本地已标记 created=True，此后不再尝试恢复。
  修法：300313 命中 TOOL segment 时把 `tool_panel_created` 一并重置为 False，下一轮走「首次创建」分支重建面板。
- **Security · 审批鉴权门异常时 fail-closed**（review 期追加）—
  原 `authorized = True` 作初值：若 `_is_interactive_operator_authorized` 抛异常（`self._admins` 缺失、
  类型错误等），`authorized` 保持 True → bypass 置位 → 审批点击被**静默放行**（fail-open）。
  改为异常路径显式置 False 并升为 WARNING 级日志（原 debug 级会掩盖问题）。

> 全量 **569 passed**（含 #7 的 21 例 +#3 的复现/回归用例 + security 反证用例）。

---

## [0.3.2] - 2026-09-12

### 新增 / Added
- **聊天类型过滤 `streaming.chat_types`** — 按 `source.chat_type` 控制哪些聊天类型发流式卡片（[#6](https://github.com/techysy/hermes-fry-cards/pull/6)，感谢 @JasonXX89）：
  缺省全部类型都发（保持向后兼容）；显式给出列表后，仅列表内类型发卡片，其余（如群聊）回落纯文本。
  典型用法 `chat_types: [dm]` → 群聊不发卡片、私聊正常。需重启网关生效。

### 修复 / Fixed
- **无 message_id 轮次卡片丢失退化为纯文本轰炸**（[#4](https://github.com/techysy/hermes-fry-cards/pull/4)，感谢 @iMissNan）—
  三处根因修复：① `on_message_started` 在 message_id 为空但有 chat_id 时创建 `synthetic` 会话，
  卡片经 `send_card_to_chat` 直接投递到聊天（不再依赖回复真实消息）；② 全部 delta/complete hook 透传
  `session_key`，controller 侧 `_resolve_session` 兜底查找会话（合成会话以 session_key 注册）；
  ③ 拆卡（300305）路径同步支持 synthetic 投递。
  复现场景：cron/后台任务完成通知、clarify 选择题恢复轮——此前整轮无卡片，长回复被 Hermes 拆成几十条纯文本刷屏。

### 性能 / Performance
- **answer 段元素动态重估，根治 300305 被动拆卡**（[#5](https://github.com/techysy/hermes-fry-cards/pull/5)，感谢 @iMissNan，报告人 群友 linxuan）—
  根因：answer segment 创建时一次性估算恒记 1 个元素，但服务端会随内容膨胀（markdown 表格展开为独立单元格、
  完成态长文按 `_MAX_CHUNK_CHARS` 切块），本地 `element_count` 严重低估，直到撞飞书硬上限（300305）才被动 force-split。
  修复：新增 `estimate_answer_elements(text)`（按当前文本算分块数 + 表格单元格数，代码块内伪表格不计）；
  `_do_flush` 步骤 0 每次 flush 对已创建 answer 段重估，差值同步进 `element_count`，让阈值判断追上真实重量，
  **主动在超阈值前拆卡**。实测场景：单日 27 次撞墙、最狠一轮拆成 104 张卡 → 消除。

> 三者合并后全量 **547 passed**（基线 534 + 13 例新增）。

---

## [0.3.1] - 2026-09-11

### 修复 / Fixed
- **兼容 Hermes 0.21.1 的 queued follow-up 返回形态**（[#8](https://github.com/techysy/hermes-fry-cards/issues/8)，感谢群友 淼淼 和 思如 反馈定位）—
  0.21.1 在 `_run_agent_queued_followup` 里把 `return _preserve_queued_followup_history_offset(result, followup_result)`
  改为先赋值 `merged` 再补 `queued_terminal_inbound_id` 后 return，插件的精确行匹配 verify 失败。
  `_find_followup_result_site` / `_ANCHOR_CHECKS` 现同时匹配两种形态（`return ...` 与 `<var> = ...` 赋值）；
  hook 插在赋值语句前、只读已绑定的 `followup_result`，两种形态下语义等价。
  测试 fixture 补 0.21.1 形态，全量 534 passed。

---

## [0.3.0] - 2026-09-07

### 新增 / Added
- **快捷回复去标题开关 `header.min_duration`** — 完成态卡片顶部状态栏可按耗时条件隐藏：
  当本次回复**无工具调用且耗时小于阈值**（秒）时，不显示顶部 `✅ 已完成` 状态栏（快回复更干净）；
  正常耗时任务或带工具调用照常显示。`0`（默认）= 不启用，行为不变。
- **群聊安全边界（modular Hermes 0.21+）** — 对 `gateway/run_turn_runner.py` 新增注入 hook：
  群友@ bot 时向 ephemeral system prompt 追加输出边界（不透露 API key/密码/令牌/内网 IP/凭据/
  私人信息、不主动执行敏感查询），DM 不受影响；`gateway.group_security_boundary.enabled` 总开关
  （默认关）+ `allow_chats` 豁免白名单（多 Agent 协作开发群放行）。**逻辑在插件内，`hermes update`
  不会覆盖，重跑 `hermes_fry_cards install` 即重打。**

---

## [0.2.0] - 2026-09-05

### 新增 / Added
- **Hermes 0.21 modular 网关布局支持**（外部 PR #2 by moliyjin-521）— 自动识别 0.21 拆分后的
  `run_inbound.py` / `run_turn.py` / `run_turn_runner.py` / `run_busy.py` 布局，把 15 个 hook
  分布到各拆分文件；同时 patch 新的 `cron/scheduler_delivery.py`，并保留 legacy
  `gateway/run.py` / `cron/scheduler.py` 兼容路径。modular apply/remove/restore 按源文件原子化、
  独立备份。新增 `Patcher(modular_paths=...)` 与自包含 modular 回归 fixture
- **token 吊销运行时恢复** — 遇到 `99991663 Invalid access token`（飞书后台保存权限/
  发版/停启用会立即吊销所有已发出的 tenant_token）时，自动清空 SDK 进程内 token 缓存
  并重试一次，无需重启网关；新增 `FeishuClient.invalidate_token_cache()`
- **模型别名** — 新增 `~/.hermes/model_aliases.json` 独立配置文件：
  key 对模型名做大小写不敏感子串匹配，命中显示别名（如 `"longcat": "哈基米"`），
  未命中回落 `truncate_model_name` 截断逻辑；每次渲染重读，改文件即生效无需重启。
  新增 `_display_model()` 统一模型显示名决策（builder.py 两处调用点收敛）
- **show_reasoning 默认开启** + 推理面板完成自动收起 — 新装用户开箱即见推理内容，
  完成后面板自动折叠避免占用卡片空间

### 变更 / Changed
- CLI `status` 命令读取 Hermes profile `.env`（`load_dotenv`）— 与网关启动器一致，
  裸插件 CLI 不再把已持久化的有效凭据误报为缺失

### 修复 / Fixed
- **seal/complete 失败时删除 loading 图标** — 卡片不再残留「处理中」状态
- **修正 4 个从未通过的陈旧测试** — 对齐合并统一面板后的实际行为（统一面板标识、
  footer 元素、element_count 会计语义），全量 519 测试通过 / 0 失败

### 文档 / Docs
- **TROUBLESHOOTING.md** — 新增排障指南：hfc 插件冲突（base.py 残留 patch）、卡片不显示、hook 未生效、cron 推送不显示
- **docs/CONFIGURATION.md** — 新增配置说明（show_reasoning 等默认值）
- **README.md** — 文档入口、排障指南链接、修复损坏 emoji 与失效链接、架构总览图
- **docs/README.md** — 新增排障指南与配置入口
- **hermes-fry-cards-skill.md** — 新增 AI Agent 自动配置开发指南

---

## [0.1.1] - 2026-08-23

### 内部稳定性优化（对照 OPTIMIZATION_PLAN P0/P1，无配置项与行为面变化）

首个正式版本。rc7 → 0.1.1 全部改动为内部稳定性提升，配置项与卡片样式无任何变化。

- **统一 session 注册与清理** — 新增 `_register_session` / `_dispose_session` 单一入口，
  `_cleanup` / `_cleanup_session` 改为兼容委托；清理幂等（重复执行不报错），
  interrupt 后旧 session 清理只删仍指向自己的索引、不误删新 session 映射；
  同 message_id 旧 session 已终态时允许重建（原先永久拒新直到进程重启）
- **FlushController 竞态修复**
  - `_do_flush` 收尾加完成态快照：`mark_completed()` 与重刷请求交叉时，
    不再可能对已完成卡片发起多余的 CardKit API 调用
  - 立即 flush 路径先取消遗留 pending timer，消除「timer 触发 + 立即路径」双刷同一份数据
  - timer 触发由 `call_soon(create_task)` 改为直接 `create_task`，收窄完成标记竞态窗口
- **失败分类与结构化日志**
  - `mark_failed(reason=...)` 记录首次失败原因，幂等保留不被后续覆盖
  - 建卡失败区分 API 错误（含飞书错误码）与未知错误；完成重试耗尽记录 `card_complete_failed`
  - `on_completed_wait` 三个 fallback 分支统一为 `_yield_to_gateway(reason=...)` 单一决策点
  - 日志事件标准化：session_created / card_created / card_reply_failed /
    fallback_to_text / stale_pruned / session_disposed / cleanup_idempotent
- 新增 9 个回归测试：cleanup 幂等、session_key 接管保护、A→B→C redirect 链路、
  终态重建、reflush 完成抑制、遗留 timer 双刷消除、timeout/FAILED fallback 决策
  （499 通过 / 4 个基线遗留失败，无新增失败）

---

## [0.1.0-rc7] - 2026-08-22

### 变更

- **默认值对齐当前推荐配置** — 新装用户开箱即用即为此套样式
  - `streaming.enabled` 默认 `true`（启用流式卡片）
  - `streaming.header.enabled` 默认 `true`（显示顶部状态栏）
  - `streaming.footer.enabled` 默认 `false`（隐藏底部元数据栏）
  - `footer.fields` 默认顺序 `status → elapsed → model → context`
  - `truncate_model_name` 默认 `true`（截断模型名）
- README 配置示例 / 默认值表同步更新

---

## [0.1.0-rc6] - 2026-08-22

### 修复

- **卡片元素超限（300305）强制拆卡恢复** — 补全 rc5 遗漏的独立错误码路径
  - 新增 `CARDKIT_ELEMENT_LIMIT_TOTAL = 300305` 识别，`_handle_flush_error` 告警
  - 当卡片实际元素总数超过飞书硬上限时，自动封印旧卡、创建新卡，并把未创建的
    segment 迁移到新卡继续流式，避免消息永久卡在「处理中」
- **远程图片 URL 过滤** — CardKit 拒绝远程 URL 作为 image key（`200570 invalid image keys`），
  工具输出等无法走异步上传路径的文本先 strip 远程图片引用（保留飞书 `img_*` key），
  并包进代码围栏避免渲染失败

---

## [0.1.0-rc5] - 2026-08-21

### 新增

- **限制 reasoning 面板数量** — 兼容「不支持分段思考」的模型（如 deepseek-v4-flash）
  - 新增配置 `display.platforms.feishu.max_reasoning_panels`（默认 3）
  - 超过上限后，后续 reasoning 片段合并进最后一个面板，不再新建独立面板
  - 修复卡片元素溢出（`300305 element exceeds the limit`）
- **统一面板按需显示** — 新增配置 `display.platforms.feishu.unified_panel_min_duration`（默认 5 秒）
  - 有工具调用 → 始终显示统一面板
  - 无工具但有推理且耗时 ≥ 阈值 → 显示
  - 无工具且耗时 < 阈值（或纯答案）→ 不显示统一面板
- **bar 进度条改为渐变阴影** — 使用 `█▓▒░` 密度渐变（`[███▓▒░░░] 35%`）
- **废弃 block/block_text 样式** — 桌面端/移动端显示不一致，自动回落到 `bar`/`text_bar`

---

## [0.1.0-rc4] - 2026-08-21

### 新增

- **进度条新增 block/block_text 样式** — 使用 ▪▫ 字符
  - `block`：`[▪▪▪▫▫▫▫▫▫▫]`
  - `block_text`：`20k/1.0m [▪▪▪▫▫▫▫▫▫▫] 21%`
- **模型名截断开关** — 新增配置 `display.platforms.feishu.truncate_model_name`（默认 false）
  - `or/lc/LongCat-2.0` → `⇲LongCat-2.0`

### 修复

- **context_display_mode 白名单补充 block/block_text** — 修复 block 模式配置不生效回落 text

---

## [0.1.0-rc3] - 2026-08-21

### 新增

- **上下文进度条** — 统一面板 header 新增上下文窗口使用量，三种显示模式：
  - `text`：`55.6k/1.0m (5%)`（纯文本）
  - `bar`：`██░░░░░░`（进度条）
  - `text_bar`：`20k/1.0m [██░░░░░░] 21%`（文本+进度条）
- **独立配置开关** — `show_context`（开关）+ `context_display_mode`（模式），默认开启 text 模式
- **智能单位** — 小于 1M 用 k，大于等于 1M 用 m（避免 `0.1m/1.0m`）
- **统一面板图标更新** — 🛠️→🔧（工具）、⌚️→⏱️（耗时）

### 修复

- **完成态 Duplicate ID 修复** — 复用流式阶段 reasoning `text_el_id`（如 `reasoning_0_text`），避免完成态 update 用固定 `reasoning_text` 冲突，修复卡片卡 loading
- **fallback 用带索引唯一 ID** — 即使 `text_el_id` 为空也不回落到固定 `reasoning_text`，彻底杜绝 Duplicate ID
- **/stop 中断时 footer 显示模型名和耗时** — `on_aborted` 补齐 `footer_data`

### 文档

- README 补充上下文显示配置说明和格式对比
- README 添加相关项目表格（aiduPOP、lark-hls-v2）
- 新增 skill 文件（AI Agent 自动配置用）
- 测试报告：490 通过 / 4 基线失败（无新增回归）

---

## [0.1.0-rc2] - 2026-08-21

### 代码审查修复（dsh review）

- **版本号统一** — `__init__.py` 与 `pyproject.toml` 统一为 `0.1.0-rc1`
- **footer 显示完整模型名** — 移除模型名截断，显示完整供应商/模型（如 `mimo/mimo-v2.5`）
- **完成态统一面板复用流式 reasoning 文本元素 ID** — 修复 `reasoning_text` Duplicate ID 导致卡片无法收尾
- **卡片创建按配置注入 tool_panel 元素** — 修复 `CardKit batch update failed: not find elementID: tool_panel`
- **统一面板 header 尾部空格** — `elapsed_ms` 为空时不再产生 `· ⌚️ ` 尾部空格
- **`_completion_session` 简化冗余条件** — 明确「排除 COMPLETED/ABORTED，保留 FAILED 以便收尾」
- **`_cmd_status` 复用 `marker_status()`** — 避免重复读取文件（新增 `Patcher.marker_status()`）
- **`_get_cron_patcher` 失败加日志** — cron patcher 不可用时输出 debug 日志
- **`feishu.py` User-Agent 动态版本号** — 从 `__version__` 读取，不再硬编码 `1.0`
- **`segment_helper.py` 阈值注释修正** — 明确余量为 18（波动）+ 2（footer）

### Fix
- 移动端模型名过长导致换行 — footer 和统一面板 header 的模型名截断为 `.../model-name`（如 `mimo/mimo-v2.5` → `.../mimo-v2.5`）
- 修复测试中 `_build_footer_elements` 返回值索引错误（`result[1]` → `result[0]`）
- 拆卡后 `tool_panel_created` 未重置，导致新卡缺少 `tool_panel` 元素引发 300313 错误

## [0.1.0-rc1] - 2026-08-21

### 新增

- **流式态工具调用合并到共享面板** — 流式阶段多个工具调用更新同一个 pending 面板，不再每步新建面板，保持卡片紧凑
- **推理轮次复用面板** — 多轮推理复用同一面板容器，统一图标样式，支持展开/收起
- **Header 状态色框** — 顶部 header 根据状态自动着色：流式中蓝色、完成绿色、中断/错误红色；合并面板头部同步着色
- **Duration fallback** — 时间显示优先使用 `footer_data.duration`，缺失时 fallback 到 session 运行时间，避免空白
- **独立 git 历史** — 仓库重建，仅包含 techysy 自己的提交
- **包名 / CLI 统一** — 包名 `hermes-fry-cards`，导入名 `hermes_fry_cards`，CLI 命令同步更新

### Changed

- 包名统一为 `hermes-fry-cards`
- 导入路径从 `hermes_potato_stream` 改为 `hermes_fry_cards`
- Footer 在合并面板模式下禁用，避免与统一面板重复
- Header 样式改为 `🥔 Agent · N轮 思维 · N步 工具 🍟 token`

### Docs

- README 大幅精简重构：功能概览表格化、配置项表格化、新增徽章
- 安装文档 INSTALL.md 支持手动 + AI Agent 双路径安装
- 英文版 README.en.md 同步更新
- 新增 `.gitignore` 排除 `__pycache__` / `egg-info`

### Added

- Merge streaming tool calls into a shared panel
- Reuse reasoning panel across rounds with collapsible icon
- Status-colored header (blue/green/red) synced with merged panel header
- Duration fallback chain: footer_data.duration → session runtime
- Independent git history with techysy-only commits
- Unified package name `hermes-fry-cards` and import `hermes_fry_cards`

### License

- 保留原项目 MIT 协议（作者 Cheerwhy / hermes-lark-streaming contributors）

### Fix

- Truncate long model names on mobile to prevent line wrap (`mimo/mimo-v2.5` → `.../mimo-v2.5`)
- Fix test index error in `_build_footer_elements` return value (`result[1]` → `result[0]`)
- Reset `tool_panel_created` after card split to prevent missing `tool_panel` element (300313 error)
