---
name: hermes-fry-cards
description: "Develop hermes-fry-cards Feishu/Lark streaming cards plugin."
version: 1.1.0
author: yangyu
license: MIT
metadata:
  hermes:
    tags: [feishu, lark, streaming, cards, plugin, development]
    related_skills: [lark-streaming]
---

# hermes-fry-cards 开发指南

> 🍟 **项目地址**：[techysy/hermes-fry-cards](https://github.com/techysy/hermes-fry-cards)
> 基于 [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) 独立开发版本

## 触发条件

当用户提到 hermes-fry-cards、fry-cards、薯条卡片、飞书卡片插件开发时触发。

## 核心开发原则

### 1. 最小改动原则（最重要）

**永远不要重写文件，只做 targeted patch。**

- 用 `patch` 工具做精确修改，不要用 `write_file` 覆盖整个文件
- 重写文件会破坏原有结构，导致测试失败（曾导致 150+ 测试失败）
- 即使觉得代码不完美，也要通过增量 patch 修改

### 2. 配置独立开关

每个功能都要有独立的配置项，不要耦合：

```yaml
display:
  platforms:
    feishu:
      show_tool_use: true        # 工具面板
      show_reasoning: true       # 推理展示
      show_context: true         # 上下文显示
      context_display_mode: text  # 上下文格式（默认 text，推荐信息风用 text_bar）
```

### 3. 文档同步

- 改配置/功能后**立即**更新 README.md 与 CHANGELOG.md
- README 配置表必须与代码一致

### 4. 快速验证

- 改完一个功能就测试，不要攒一堆改动再测
- 用 `pytest tests/ -q` 验证（基线：499 通过 / 4 失败遗留）

## 项目结构

```
hermes_fry_cards/
├── cardkit/
│   ├── builder.py      # 卡片构建（不要重写！）
│   ├── markdown.py     # Markdown 处理
│   └── i18n.py         # 国际化
├── streaming/          # 流式卡片运行时
│   ├── controller.py   # 建卡/flush/拆卡/完成编排
│   ├── session.py      # CardSession 状态机 + 注册/失败标记
│   ├── segments.py     # SegmentState 扁平 segment 列表
│   ├── segment_helper.py  # action 构建 / 元素估算 / 拆卡切点
│   ├── flush.py        # FlushController 节流调度器
│   ├── tooluse.py      # ToolUseTracker 工具生命周期
│   ├── image.py        # ImageResolver 图片转 img_key
│   └── unavailable_guard.py  # 消息撤回自动终止
├── controller.py       # StreamCardController 主控制器
├── config.py           # 配置读取
├── patch.py            # AST 注入逻辑
└── patcher.py          # Hook 注入器
```

## 常用命令

```bash
# 安装
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -e .
$HERMES_PYTHON -m hermes_fry_cards verify
$HERMES_PYTHON -m hermes_fry_cards install

# 测试
$HERMES_PYTHON -m pytest tests/ -q

# 卸载
$HERMES_PYTHON -m hermes_fry_cards uninstall
```

> 网关重启必须在**外部终端**执行，不能从 gateway 进程内部触发。

## 配置项参考（0.1.1 默认值）

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `streaming.enabled` | 启用流式卡片 | `true` |
| `show_tool_use` | 展示工具调用面板 | `true` |
| `show_reasoning` | 展示推理过程 | `true`（2026-08-24 起默认开启，commit ca5a74c） |
| `show_context` | 统一面板 header 显示上下文窗口 | `true` |
| `context_display_mode` | `text`/`bar`/`text_bar`（block 已废弃） | `text`（2026-08-21 起，commit 9c76473） |
| `max_reasoning_panels` | 最多保留独立推理面板数，超出合并防溢出 | `3` |
| `unified_panel_min_duration` | 无工具或耗时 ≤ 此值不显示统一面板（秒） | `5` |
| `truncate_model_name` | 截断模型名（`nvidia/moonshotai/kimi-k3` → `⇲kimi-k3`） | `true` |
| 模型别名 | `~/.hermes/model_aliases.json`：`{"longcat": "哈基米"}` 子串匹配，命中优先于截断；每次渲染重读热更新 | 无 |
| `panel_expanded` | 完成态面板保持展开 | `false` |
| `header.enabled` | 顶部状态栏 | `true` |
| `footer.enabled` | 底部元数据栏 | `false` |
| `footer.fields` | footer 字段顺序 | `status→elapsed→model→context` |
| `width_mode` | 卡片宽度 | `default` |

> rc7 起默认值对齐推荐配置，新装开箱即用。

## 上下文显示格式

| `context_display_mode` | 效果 |
|------------------------|------|
| `text`（默认） | `55.6k/1.0m (5%)` |
| `bar` | `[███▓▒░░░] 35%`（渐变阴影） |
| `text_bar` | `20k/1.0m [███▓▒░░░] 21%` |

> block/block_text 已废弃，桌面端/移动端显示不一致，自动回落 bar/text_bar。

## 热更新 vs 需重启

| 类型 | 配置项 |
|------|--------|
| **热更新**（改 config.yaml 下一条消息生效） | show_reasoning / show_tool_use / show_context / context_display_mode / max_reasoning_panels / unified_panel_min_duration / truncate_model_name |
| **需重启网关** | 插件代码、streaming.enabled、header/footer/body/width_mode 结构类配置、飞书凭据 |

## 发布流程

1. 更新 `pyproject.toml` 和 `__init__.py` 版本号
2. 更新 `CHANGELOG.md`
3. 更新 `release-notes.md`
4. 提交推送，打 tag `git tag vX.Y.Z && git push origin vX.Y.Z`
5. `gh release create vX.Y.Z --notes-file release-notes.md INSTALL.md hermes-fry-cards-skill.md`
6. Release 正文末尾补 Full Changelog 对比链接

## 已知遗留问题（基线失败，非新引入）

- `test_multiple_tool_segments_merged_into_single_bottom_panel`
- `test_footer_present_by_default`
- `test_split_flushes_pending_actions_then_moves_to_next_card`
- `test_batch_update_recovers_stale_segment_on_missing_element`

## 架构要点（0.1.1）

- **session 生命周期统一**：注册走 `_register_session`、清理走 `_dispose_session`（幂等）；interrupt 接管后旧 session 清理不会误删新映射；同 message_id 终态 session 可重建
- **FlushController 竞态防护**：完成态快照防误重刷；立即 flush 先取消遗留 timer 防双刷
- **fallback 单一决策点**：卡片无法收尾统一走 `_yield_to_gateway(reason=...)`
- **失败原因可追溯**：`mark_failed(reason=...)` 记录首次原因且幂等保留
- **日志事件标准化**：session_created / card_created / card_reply_failed / fallback_to_text / stale_pruned / session_disposed

## 踩坑记录

### 2026-08-21：完成态 reasoning Duplicate ID 导致卡片卡 loading

**现象**：拆卡/完成态卡片一直 loading，日志报 `ElementID reasoning_text: Code 1001: Duplicate ID`（300301），重试 3 次后放弃。

**根因**：`build_complete_card` 收集 reasoning rounds 时丢弃了 `seg.text_el_id`，完成态 update 用默认 `reasoning_text`（REASONING_TEXT_ELEMENT_ID），与流式阶段已创建的 text element 冲突。CardKit update 是 merge-style，重复 ID 报错 → 完成 update 失败 → 卡片无法封印，loading 残留。

**修复**：收集 reasoning rounds 时保留 `text_el_id`，构建 reasoning panel 时传入（fallback 用带索引唯一 ID，绝不回落固定 `reasoning_text`）：
```python
reasoning_rounds.append({"text": seg.text, "elapsed_ms": seg.elapsed_ms, "text_el_id": seg.text_el_id})
...
text_el_id = rnd.get("text_el_id") or f"reasoning_text_{i}"  # i 为轮次索引
_build_reasoning_panel(text=..., text_element_id=text_el_id)
```

**注意回归根源**：`git checkout <commit>` 恢复 builder.py 时**会丢掉后续的修复**。本次 Duplicate ID 就是 builder.py 重写/恢复后，丢失了 `69943aa` 的 text_el_id 复用逻辑导致的。改代码务必用 targeted patch，不要整文件 checkout/重写。

**教训**：完成态重建卡片时，所有元素 ID 必须复用流式阶段已有的 ID，绝不能用固定默认 ID（CardKit merge-style 下固定 ID 会 Duplicate）。

### 2026-08-21：重写 builder.py 导致 150+ 测试失败

**原因**：用 `write_file` 重写了整个 `builder.py`，破坏了原有结构和测试兼容性。

**修复**：恢复原始文件，用 `patch` 做 targeted 修改。

**教训**：永远不要重写文件，只做 targeted patch。

### 2026-08-21：config.py 覆盖导致测试失败

**原因**：重写 `config.py` 时丢失了 `card_duration_sec` 属性。

**修复**：恢复原始文件，只添加新属性。

**教训**：配置文件也要增量修改。
