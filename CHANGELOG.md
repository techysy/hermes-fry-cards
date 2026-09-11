# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.3.1] - 2026-09-11

### 修复 / Fixed
- **兼容 Hermes 0.21.1 的 queued follow-up 返回形态**（[#8](https://github.com/techysy/hermes-fry-cards/issues/8)）—
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
