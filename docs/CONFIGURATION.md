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
| `ai_summary.enabled` | `false` | 完结卡自动 AI 摘要总开关（**默认关**） |
| `ai_summary.min_chars` | `800` | 触发的最短正文字数：短回复不打扰，一个 token 都不花（`0`=每次都摘） |
| `ai_summary.model` | 空 | 指定摘要模型；留空自动挑端点清单里的 flash 小模型 |
| `ai_summary.timeout_sec` | `20` | 摘要调用硬超时（1~120 秒）；超时只影响摘要迟到，不拖收尾 |
| `ai_summary.max_output_chars` | `160` | 摘要目标字数（40~600），同时折算成输出 token 上限硬约束 |

**footer 可用字段**：`status` `elapsed` `model` `reasoning_count` `tool_count` `context`

二维数组每行一个子数组，如：

```yaml
fields:
  - [model, status]
  - [elapsed, context]
```

**完结卡 AI 摘要（`streaming.ai_summary`）**

开启后，卡片**正常完成并确认封口**，插件会用 `config.yaml` 里现成的模型端点（`model.base_url` +
`api_key`，或 `custom_providers` / `providers` 中带 `api_key` 的条目）调一个小模型，往同一张卡补一段
「🔍 AI 摘要」：

```yaml
streaming:
  ai_summary:
    enabled: true
    min_chars: 1200      # 正文不足 1200 字不调模型（不花 token）
    model: ""            # 留空 = 自动挑 flash 档；也可写 cbcn/deepseek-v4.1-flash
    timeout_sec: 20
    max_output_chars: 160
```

- **落点**：摘要插在正文之后、统一面板之前（面板不存在时落到卡片末尾）；只补一个元素，**不整卡重发**，
  因此不会重复计数面板子元素（#53 的 300305 老路），也不影响流式打字机行为。
- **不拖收尾**：摘要在封卡成功之后异步排队，硬超时 + 失败降级；拿不到摘要就按原样出卡（现状即正确兜底），
  卡片不会停在「处理中」、不会丢面板、不会改 `already_sent`。
- **不打扰残缺卡**：出错或被 `/stop` 掐断的卡片一律不摘要。
- **不撞元素上限**：卡片元素数（递归计数）已逼近飞书 200 硬上限时直接跳过，**连模型都不调**——
  那种卡补元素注定撞 300305，摘要的钱就是白烧。
- **进程重启**：已生成但没确认落卡的摘要记在 `~/.hermes/state/fry-cards-ai-summary.json`，下次启动补投
  （最多 3 次）。这份登记表与网关重启封卡用的 `fry-cards-live.json` 分离，不会把已完成的卡扫成「已中断」。
- **成本参考**（10R 端点实测）：1.2KB 正文 → 160 字摘要约 850~900 token、1.7~3.7 秒。
- **凭据安全**：key 只在服务端读取使用，不回传浏览器、不写进仓库、不进日志（异常消息统一掩码）。

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
