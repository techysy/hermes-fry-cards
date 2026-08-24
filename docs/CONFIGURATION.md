# 卡片设置指南

所有配置写在 Hermes 主配置 `~/.hermes/config.yaml`，改完即生效（无需重启 gateway，插件每次从磁盘重读）。

---

## 配置结构总览

```yaml
streaming:
  enabled: true              # 总开关：是否启用流式卡片
  panel_expanded: false      # 完成态卡片中面板（工具/推理）是否保持展开
  card_ttl_sec: 600          # 卡片存活检测超时（秒）
  width_mode: default        # 卡片宽度: default / compact / fill

  header:
    enabled: true            # 流式/完成态卡片顶部状态条（蓝=流式 绿=完成 红=中断）

  body:
    text_size: normal_v2     # 正文文字大小

  footer:
    enabled: false           # 完成态卡片底部信息栏
    text_size: notation      # footer 文字大小
    show_label: false        # 是否显示字段标签（如 "模型:"）
    fields:                  # 字段布局（二维数组 = 分行显示）
      - [status, elapsed, model, context]

display:
  # ↓ 全局默认，可被 display.platforms.feishu 覆盖
  show_reasoning: true       # 展示推理过程（思考面板）
  show_tool_use: true        # 展示工具调用面板
  show_context: true         # 统一面板 header 显示上下文窗口用量
  max_reasoning_panels: 3    # 最多保留的独立推理面板数（防元素溢出）
  unified_panel_min_duration: 5   # 回复≤该秒数时不展示统一面板（短回复免冗余头部）
  truncate_model_name: true  # 截断模型名前缀（or/ → ⇲）
  context_display_mode: text # 上下文显示模式（见下）

  platforms:
    feishu:                  # 仅飞书平台覆盖上面的全局值
      show_reasoning: true
      context_display_mode: bar
```

**优先级**：`display.platforms.feishu.*` → `display.*` → 内置默认值

---

## 常用场景

### 极简风格（只看回答）

```yaml
streaming:
  header:
    enabled: false
  footer:
    enabled: false
display:
  show_reasoning: false
  show_tool_use: false
```

### 完整信息风（默认增强版）

```yaml
streaming:
  footer:
    enabled: true
display:
  show_reasoning: true
  context_display_mode: text_bar
```

### 短回复免头部

```yaml
display:
  unified_panel_min_duration: 10   # 10 秒内的回复不显示模型/工具统一面板
```

---

## 各配置项详解

### streaming 段

| 键 | 默认 | 说明 |
|----|------|------|
| `enabled` | `true` | 插件总开关 |
| `panel_expanded` | `false` | 完成后工具/推理面板是否展开（false=收起更干净） |
| `card_ttl_sec` | `600` | 卡片存活检测超时 |
| `width_mode` | `default` | `compact`=紧凑 / `fill`=撑满 |
| `header.enabled` | `true` | 顶部状态色条 |
| `body.text_size` | `normal_v2` | 正文文字大小 |
| `footer.enabled` | `false` | 底部信息栏 |
| `footer.text_size` | `notation` | footer 小字号 |
| `footer.show_label` | `false` | 显示字段名标签 |
| `footer.fields` | `[[status, elapsed, model, context]]` | footer 字段布局 |

**footer 可用字段**：`status` `elapsed` `model` `reasoning_count` `tool_count` `context`

二维数组每行一个子数组，如：

```yaml
fields:
  - [model, status]
  - [elapsed, context]
```

### display 段

| 键 | 默认 | 说明 |
|----|------|------|
| `show_reasoning` | `true` | 思考过程面板；运行时可用 `/reasoning` 命令切换 |
| `show_tool_use` | `true` | 工具调用合并进底部统一面板 |
| `show_context` | `true` | 面板 header 显示上下文用量 |
| `max_reasoning_panels` | `3` | 超出后推理片段合并进最后一个面板（防飞书 200 元素上限） |
| `unified_panel_min_duration` | `5` | 秒。耗时≤该值或无工具调用 → 不显示统一面板 |
| `truncate_model_name` | `true` | `nvidia/moonshotai/kimi-k3` → `⇲kimi-k3` |

### 模型别名配置

独立于 `config.yaml`，别名写在 `~/.hermes/model_aliases.json`：

```json
{
  "longcat": "哈基米",
  "gemini": "哈基米"
}
```

- **匹配**：key 对完整模型名做大小写不敏感子串匹配
- **优先级**：别名命中 → 显示别名；未命中 → 回落截断逻辑
- **热更新**：每次渲染重读，改完文件即生效

> 完整路径：`~/.hermes/model_aliases.json`
| `context_display_mode` | `text` | 见下表 |

### context_display_mode 可选值

| 值 | 效果 |
|----|------|
| `text` | `55.6k/1.0m (5%)` |
| `bar` | `█▓▒░░░░░░░` |
| `text_bar` | `20k/1.0m [██░░░░░░] 21%` |
| `block` | `▪▪▪▫▫▫▫▫▫▫` |
| `block_text` | `20k/1.0m [▪▪▪▫▫▫▫▫▫▫] 21%` |

---

## 运行时切换

- `/reasoning` — 切换推理显示开关（直接改 config.yaml，立即生效）

## 排障

见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
