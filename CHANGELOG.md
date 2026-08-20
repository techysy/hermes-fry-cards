# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fix
- 移动端模型名过长导致换行 — footer 和统一面板 header 的模型名截断为 `.../model-name`（如 `mimo/mimo-v2.5` → `.../mimo-v2.5`）
- 修复测试中 `_build_footer_elements` 返回值索引错误（`result[1]` → `result[0]`）

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
