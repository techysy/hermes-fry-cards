# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0-rc3] - 2026-08-21

### 上下文进度条

- **上下文显示** — 统一面板 header 新增上下文窗口使用量，三种显示模式：`text`（纯文本）、`bar`（进度条）、`text_bar`（文本+进度条）
- **独立配置开关** — `show_context`（开关）+ `context_display_mode`（模式），默认开启 text 模式
- **智能单位** — 小于 1M 用 k，大于等于 1M 用 m（避免 `0.1m/1.0m`）
- **移除 ⏳ 图标** — 进度条前不再显示 ⏳，更简洁

### Bug 修复

- **完成态 Duplicate ID 修复** — 复用流式阶段 reasoning text_el_id（如 `reasoning_0_text`），避免完成态 update 用固定 `reasoning_text` 冲突，修复卡片卡 loading
- **fallback 用带索引唯一 ID** — 即使 text_el_id 为空也不回落到固定 `reasoning_text`，彻底杜绝 Duplicate ID
- **/stop 中断时 footer 显示模型名和耗时** — `on_aborted` 补齐 footer_data

### 文档

- README 补充上下文显示配置说明和格式对比
- README 添加相关项目表格（aiduPOP、lark-hls-v2）
- CHANGELOG 更新至 rc3

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
