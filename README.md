# 🍟 薯条卡片 (hermes-fry-cards)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-%E2%89%A50.14.0-2463eb)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-blue)](https://www.python.org/)
[![当前版本](https://img.shields.io/badge/Release-v0.3.0-2463eb?logo=github&logoColor=white)](https://github.com/techysy/hermes-fry-cards/releases)

> 🍟 Hermes Gateway 飞书流式卡片插件 — CardKit v2.0 实时流式消息

- [English](README.en.md) · [安装指南](INSTALL.md) · [文档目录](docs/README.md) · [优化方案](docs/OPTIMIZATION_PLAN.md)

![A compact chat card on a pale gray background with a circular profile image on the left. The top status line reads 回复 余师评：晚上好 with a green checkmark and the label 已完成 beside it. Below, the assistant message says 晚上好！已切换到 nvidia/moonshotai/kimi-k3，有什么需要我处理的？ A footer row shows the model name, reasoning count, tool count, context usage, and elapsed time. The overall mood is calm and polished, with soft green accents and a minimal messaging interface.](assets/collapse.png)            

---

## ✨ 核心特性

| 能力 | 说明 |
|------|------|
| 🎴 **流式卡片** | AI 回复实时打字机效果，按事件顺序动态渲染思考 / 工具 / 回答 |
| 🔧 **工具调用合并** | 多个工具调用合并到统一面板，保持卡片紧凑不散乱 |
| 🧠 **推理展示** | 显示模型思考/推理内容，支持 `<thinking>` / `Reasoning:` / 原生 API 推理块 |
| 🎯 **统一面板** | 推理 + 工具合并成底部统一面板，Header 显示模型名、轮次、工具数、耗时 |
| 📊 **终态卡片** | 完成后展示完整结果 — token 用量、耗时、上下文窗口 |
| 🖼️ **图片解析** | 自动识别 markdown 图片引用，下载上传后替换为飞书 img_key |
| 🛡️ **消息保护** | 消息被删除/撤回后自动终止更新，避免无效 API 调用 |
| 📤 **卡片自动拆分** | 接近飞书 200 元素上限时自动拆分为多张卡片 |
| 🔔 **Cron 推送** | 定时任务结果以飞书卡片推送，保留 Markdown 渲染 |
| 🔙 **后台任务推送** | `/background`（`/btw`）任务完成后以卡片形式推送 |
| 🌐 **中英双语** | 卡片文本根据飞书客户端语言自动切换 |
| 🎨 **可定制样式** | header/footer、文字大小、宽度模式、字段布局均可配置 |
| 🎯 **状态色框** | 顶部 header 根据状态自动着色：流式中蓝色、完成绿色、中断/错误红色 |
| ⏱️ **快捷回复去标题** | 无工具调用且耗时低于阈值时隐藏顶部状态栏，回复更干净 |
| 🛡️ **群聊安全边界** | 群内 @bot 自动注入安全提示，不泄露 key/密码/内网 IP（modular Hermes 0.21+，Studio 可视化开关） |
| ❓ **Clarify 按钮卡片** | 选项提问渲染为可点击按钮卡片（单选/多选勾选/「其他」内嵌输入框 + toast 反馈），补齐飞书适配器缺失的 `send_clarify` 原生交互 |
| 🔐 **审批卡片点击修复** | 审批按钮点击改用交互回调鉴权（原版误用群消息准入策略导致点击被吞），并为受理/过期/无权限/拒绝补全 toast 反馈 |

## 🏗️ 架构总览

![hermes-fry-cards 架构：Hermes Gateway 事件经 AST hook 注入层进入 StreamCardController，streaming/ 运行时节流编排，cardkit/ 构建卡片 JSON，经飞书 CardKit v2.0 API 交付用户端流式卡片；cron 推送与失败回落路径独立标注](assets/architecture.svg)

> v0.3.0 新增：快捷回复去标题（`header.min_duration`）按耗时条件隐藏顶部状态栏；群聊安全边界（modular Hermes 0.21+）向 ephemeral system prompt 注入输出边界，私聊不受影响。

---

## 🚀 快速安装

### 一行安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/install.sh | bash
```

脚本自动完成：定位 Hermes 的 venv Python → 安装包 → `verify` 兼容性检查 → 注入 hook，最后提示你重启网关。

```bash
# 指定版本（默认 main）
curl -fsSL .../install.sh | FRY_REF=v0.3.3 bash
# 自动探测失败时手动指定解释器
curl -fsSL .../install.sh | HERMES_PYTHON=/path/to/python3 bash
```

> ⚠️ 脚本**不会**替你重启网关（从网关进程内部触发的重启会杀掉自己），装完请自行执行 `hermes gateway restart`。

### AI Agent 一键安装

```
curl https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/INSTALL.md
```

### 手动安装

```bash
# GitHub（主仓库）
git clone https://github.com/techysy/hermes-fry-cards.git
# 或国内 Gitee 镜像（更快）
git clone https://gitee.com/techysy/hermes-fry-cards.git

cd hermes-fry-cards
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -e .
$HERMES_PYTHON -m hermes_fry_cards verify
$HERMES_PYTHON -m hermes_fry_cards install
hermes gateway restart
```

---

## ⚙️ 配置

```yaml
streaming:
  enabled: true
  chat_types: [dm, group]  # 允许发流式卡片的聊天类型；缺省 = 全部类型都发。
                           # 例如只想要私聊卡片、群聊回落纯文本：chat_types: [dm]
  header:
    enabled: true
    min_duration: 0   # 快捷回复去标题阈值(秒)；无工具调用且耗时 < 此值 → 完成态不显示顶部状态栏。0=不启用
  footer:
    enabled: false
    fields:
      - [status, elapsed, model, context]
display:
  platforms:
    feishu:
      show_tool_use: true        # 展示工具调用面板
      show_reasoning: false      # 展示推理过程
      show_context: true         # 统一面板 header 显示上下文窗口
      context_display_mode: text # text / bar / text_bar
      max_reasoning_panels: 3    # 最多保留的独立推理面板数（超出后合并，防元素溢出）
      unified_panel_min_duration: 5  # 统一面板最小展示耗时（秒）
      truncate_model_name: true  # 截断模型名（nvidia/moonshotai/kimi-k3 → ⇲kimi-k3）
```

### 上下文显示格式

开启 `show_context` 后，统一面板 header 会显示上下文窗口使用量：

| `context_display_mode` | 显示效果 |
|------------------------|----------|
| `text`（默认） | `55.6k/1.0m (5%)` |
| `bar` | `[███▓▒░░░] 35%`（渐变阴影） |
| `text_bar` | `20k/1.0m [███▓▒░░░] 21%` |

> `block` / `block_text` 样式在桌面端/移动端显示不一致，已废弃，自动回落到 `bar` / `text_bar`。

### 配置项一览

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `header.enabled` | 顶部状态栏 | `true` |
| `header.min_duration` | 快捷回复去标题阈值（秒）；无工具调用且耗时 `<` 此值 → 完成态不显示顶部状态栏；`0` = 不启用（始终显示） | `0` |
| `footer.enabled` | 底部元数据栏 | `false` |
| `panel_expanded` | 完成态面板保持展开 | `false` |
| `chat_types` | 允许发流式卡片的聊天类型（`source.chat_type`，如 `dm`/`group`）；缺省全部类型都发，列表外类型回落纯文本 | 缺省 = 全部 |
| `content_lang` | 内容层提示语文案语言（表格转换引导、超长截断提示等正文内嵌文案）：`zh` / `en`。UI 词条走卡片级 i18n 由飞书客户端自动选语言，正文内嵌文案无法如此，故按部署方偏好定死 | `zh` |
| `width_mode` | 卡片宽度 (`default` / `compact` / `fill`) | `default` |
| `show_tool_use` | 展示工具调用面板 | `true` |
| `show_reasoning` | 展示推理过程 | `false` |
| `show_context` | 统一面板 header 显示上下文窗口 | `true` |
| `context_display_mode` | 上下文显示格式：`text` / `bar` / `text_bar` | `text` |
| `max_reasoning_panels` | 最多保留的独立推理面板数（超出后合并，防元素溢出） | `3` |
| `unified_panel_min_duration` | 统一面板最小展示耗时（秒）；无工具调用或耗时 ≤ 此值不显示统一面板 | `5` |
| `truncate_model_name` | 截断模型名（`nvidia/moonshotai/kimi-k3` → `⇲kimi-k3`） | `true` |
| `model_aliases_enabled` | 模型别名总开关；`false` 时忽略别名整体回落截断（配置保留） | `true` |
| `gateway.group_security_boundary.enabled` | 群聊安全边界总开关（群聊回复套输出边界，私聊不受影响） | `false` |
| `gateway.group_security_boundary.allow_chats` | 豁免群白名单（`oc_xxx`，一行一个；这些群不套边界） | `[]` |

### 模型别名配置

独立于 `config.yaml`，别名写在 `~/.hermes/model_aliases.json`（**Studio → 配置 → 模型别名** 可视化编辑，无需手写）：

```json
{
  "longcat": "哈基米",
  "mimo": "小虾米",
  "deepseek": {
    "name": "梁文谷⚡️",
    "timeAliases": [
      { "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "12:00", "name": "梁文锋⚡️" },
      { "days": [1, 2, 3, 4, 5], "start": "14:00", "end": "18:00", "name": "梁文锋⚡️" }
    ]
  }
}
```

- **匹配**：key 对完整模型名做大小写不敏感子串匹配，按插入顺序第一条命中（`longcat` → `or/lc/LongCat-2.0`）
- **时段人设**：值可为对象，按**北京时间（UTC+8）** HH:MM + 星期自动切换显示名——典型用途是
  DeepSeek **峰谷价标识**（峰段梁文锋⚡️ / 谷段梁文谷⚡️）。`days` 支持数组 `[1,2,3,4,5]`（0=周日）
  或字符串 `"1-5"` / `"0,6"` / `"1-5,0"`；`start`/`end` 为 `[start, end)` 左闭右开、支持跨午夜、
  起止相等 = 全天；规则都不命中回落 `name`（即"其他时间"）。**格式与 openclaw/claw-fry-cards 的
  `modelAliases` 配置逐字兼容，同一份 JSON 两边通用**
- **优先级**：别名命中 → 显示别名；未命中 → 回落截断逻辑；`display.model_aliases_enabled: false`
  可整体关闭别名（回落截断，配置保留）
- **热更新**：每次渲染重读，改文件或 Studio 保存即生效，无需重启网关

> 完整路径：`~/.hermes/model_aliases.json`
| [模型别名](#-与上游-hermes-lark-streaming-的差异) | `~/.hermes/model_aliases.json` 子串匹配 + **时段人设（北京时间自动切换）**，命中优先于截断 | 无 |

### 样式效果示例

统一面板 header 的完整形态（完成态卡片底部折叠面板标题）：

![A compact chat card on a pale gray background with a circular profile image on the left. The top status line reads 回复 余师评：晚上好 with a green checkmark and the label 已完成 beside it. Below, the assistant message says 晚上好！已切换到 nvidia/moonshotai/kimi-k3，有什么需要我处理的？ A footer row shows the model name, reasoning count, tool count, context usage, and elapsed time. The overall mood is calm and polished, with soft green accents and a minimal messaging interface.](assets/collapse.png)

| 配置组合 | header 效果 |
|---------|------------|
| `truncate_model_name: false`, `show_context: true` 纯文本 | `🍟 nvidia/moonshotai/kimi-k3 · 💭2 · 🔧3 · 152.6k/1.0m (15%) · ⏱️ 45.2s` |
| 默认（`truncate_model_name: true`） | `🍟 ⇲kimi-k3 · 💭2 · 🔧3 · 152.6k/1.0m (15%) · ⏱️ 45.2s` |
| `context_display_mode: bar` | `🍟 ⇲kimi-k3 · 💭2 · 🔧3 · [███▓▒░░░] 15% · ⏱️ 45.2s` |
| `context_display_mode: text_bar` | `🍟 ⇲kimi-k3 · 💭2 · 🔧3 · 152.6k/1.0m [███▓▒░░░] 15% · ⏱️ 45.2s` |
| `show_context: false` | `🍟 ⇲kimi-k3 · 💭2 · 🔧3 · ⏱️ 45.2s` |

> header 各部分含义：`🍟` 模型名 · `💭n` 推理轮次 · `🔧n` 工具调用数 · 上下文窗口 · `⏱️` 耗时。

> 无工具调用或回复 ≤ `unified_panel_min_duration` 秒时，整个统一面板（含 header）不显示。

![A minimal chat card on a pale gray background with a circular profile image on the left. The status line reads 回复 余师评：那不是什么 是高清背景图, with a green checkmark and the label 已完成 above the assistant response. The message body is in Chinese and discusses a background image, with a highlighted sentence mentioning LICENCE and repo-audit-fix. The overall tone is calm and professional, with soft green styling and a clean messaging layout.](assets/quick_reply.png)

## 🎛️ Studio 可视化配置工作坊

本地 Web UI 调卡片配置 + 实时预览 + 状态诊断。零第三方依赖（纯 stdlib `http.server` + 原生 HTML/CSS/JS）：

```bash
$HERMES_PYTHON -m hermes_fry_cards studio                 # 默认 http://127.0.0.1:8765，自动开浏览器
$HERMES_PYTHON -m hermes_fry_cards studio --port 9000 --no-browser
```

| 页签 | 能力 |
|------|------|
| **配置** | 流式开关 / **群聊安全边界**（开关 + 豁免群白名单）/ **统一面板**（模型·推理·工具·上下文的全套开关，元数据默认由它承载）/ **模型别名**（含时段人设星期芯片编辑器 + 总开关）/ 状态栏（Header·Footer 合并组）等**白名单键**的表单化编辑 |
| **预览** | 服务端**真实 builder** 渲染（与线上卡片同一代码路径，非前端模拟）：快捷回复 / 工作流交错 / 多表格压缩 / 超长截断 四场景 × 流式·完成·出错态 |
| **状态** | hook 注入状态、verify 兼容性、飞书凭据、Hermes 环境一览 + 一键重启网关 |

**写回安全五件套**：服务端严格校验（未知键/非法值 → 400）→ config.yaml 解析失败即拒写（409，保护凭证）→
写前自动备份（`~/.hermes/backups/fry_studio/`，轮转保留 20 份）→ 只深合并白名单键（手写配置原样保留）→
原子落盘（tmp + fsync + rename）。安全面：仅监听 `127.0.0.1`、Host 门防 DNS rebinding、无 CORS 头、
body ≤1MB、`nosniff`。

> ⚠️ 保存会重写整个 config.yaml，**YAML 注释会丢失**（UI 有显著提示）；结构类配置保存后仍需
> `hermes gateway restart`（见下方热更新说明）。`display.*` 展示类键保存后免重启即时生效。

---

## 🛡️ 群聊安全边界（modular Hermes 0.21+）

群友 @ bot 时，回复默认会带输出边界：**不透露 API key / 密码 / 令牌 / 服务器内网 IP / 凭据 /
私人信息**，也不主动执行敏感查询（账户 / 余额 / 凭据 / 内部状态）；被群友索要这类信息时婉拒并
引导私聊。**你的私聊 DM 不受影响**。

### 配置（`~/.hermes/config.yaml`）

```yaml
gateway:
  group_security_boundary:
    enabled: true    # 总开关（默认关）
    allow_chats: []  # 豁免群白名单：这些群不套边界，放行自由交流
```

- `enabled`：总开关。`true` 时所有群聊（除 `allow_chats` 白名单）套边界。
- `allow_chats`：**豁免群**。适合多 Agent 协作开发群——群里多个 Agent 互相交流、
  需要传凭据 / 内部状态干活时不加约束。填入群的 `chat_id`（飞书 `oc_xxx`）即放行。

> 也可以在 **Studio → 配置 → 🛡️ 群聊安全边界** 直接开关和编辑豁免群（白名单只写
> `enabled`/`allow_chats` 两键，你手写的 `text` 自定义边界文案与 `gateway` 下其他配置原样保留）。

> 实现：对 `gateway/run_turn_runner.py::_combined_ephemeral_prompt` 注入 hook，走 ephemeral
> system prompt（不碰持久缓存）。**逻辑在插件内**，`hermes update` 不会覆盖——升级后重跑
> `hermes_fry_cards install` 即重打。

---

## 🖥️ CLI 命令

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_fry_cards verify     # 验证兼容性
$HERMES_PYTHON -m hermes_fry_cards install    # 注入 hook
$HERMES_PYTHON -m hermes_fry_cards uninstall  # 移除 hook
$HERMES_PYTHON -m hermes_fry_cards status     # 查看状态
$HERMES_PYTHON -m hermes_fry_cards studio     # 可视化配置工作坊（127.0.0.1:8765）
```

---

## 📝 更新 / 卸载

### 更新

```bash
cd hermes-fry-cards && git pull
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -e .
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
hermes gateway restart
```

### 卸载

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m pip uninstall hermes-fry-cards
```

---

## 🧠 工作原理

```
用户发送消息
  → 创建卡片会话
  → 流式更新（工具状态、文本增量 — 节流调度）
  → 图片 URL 异步解析替换
  → 终态卡片（token/耗时/上下文）
```

### 🛡️ 稳定性保障（v0.1.1）

- **session 生命周期统一管理**：注册/清理单一入口、幂等可重入；中断接管不误删新会话映射；异常残留的终态会话允许重建，不再需要重启网关恢复
- **flush 竞态防护**：完成标记与刷新请求交叉时不会误触发多余 CardKit 调用；同一份数据不会被重复刷新
- **失败原因可追溯**：卡片失败时记录首次失败原因（`mark_failed(reason=...)`），回退网关纯文本是统一决策点

排查问题时可直接按以下标准化日志事件在 `~/.hermes/logs/agent.log` 中检索：

| 事件 | 含义 |
|------|------|
| `session_created` | 卡片会话创建 |
| `card_created` | 占位卡片发送成功 |
| `card_reply_failed` | 建卡失败（含飞书错误码），将回退纯文本 |
| `fallback_to_text` | 卡片无法收尾，交还网关默认回复（含原因 reason=...） |
| `card_complete_failed` | 完成重试 3 次耗尽 |
| `session_disposed` / `cleanup_idempotent` | 会话清理 / 重复清理被幂等吸收 |

---

## 📄 文档与报告入口

- [优化方案](docs/OPTIMIZATION_PLAN.md) — 稳定性与维护性优化方案
- [卡片设置指南](docs/CONFIGURATION.md) — 全部配置项与常用场景
- [排障指南](docs/TROUBLESHOOTING.md) — 与 hfc 插件冲突、卡片不显示、hook 未生效等常见问题
- [文档目录](docs/README.md) — 项目文档入口
- [测试报告目录](tests/reports/README.md) — 报告存放规范与入口
- **核心汉化补丁**：`skills/hermes-core-zh-localization/`（仓库内附带，安装方式见 SKILL.md）

> 说明：测试报告建议统一保存在 `tests/reports/`，并按日期/主题命名，便于后续回归对比和审计。

---

## 🔄 热更新 & 网关重启说明

本插件通过 AST 注入 hook 到 Hermes 的 `gateway/run.py` 和 `cron/scheduler.py`。**部分配置支持热更新（无需重启），但插件代码改动和部分结构类配置需要重启网关才能生效**。

### ✅ 支持热更新（改 config.yaml 后下一条消息即生效）

以下 `display` 显示 / 样式类配置项，每次渲染都会从磁盘重读，**无需重启**（通过 Studio 保存这些键同样免重启）：

| 配置项 | 说明 |
|--------|------|
| `show_reasoning` | 展示推理过程（`/reasoning` 命令即运行时切换此配置） |
| `show_tool_use` | 展示工具调用面板 |
| `show_context` | 统一面板 header 显示上下文窗口 |
| `context_display_mode` | 上下文显示格式：`text` / `bar` / `text_bar` |
| `max_reasoning_panels` | 最多独立推理面板数（防元素溢出） |
| `unified_panel_min_duration` | 统一面板最小展示耗时（秒） |
| `truncate_model_name` | 截断模型名 |
| 模型别名总开关 | `display.model_aliases_enabled` 每次渲染重读 |
| 模型别名 | `~/.hermes/model_aliases.json` 每次渲染重读，改文件即生效 |

### ⚠️ 需要重启网关（`hermes gateway restart`）

以下改动必须重启，运行中的进程不会热加载：

- **插件代码修改**（`git pull` 更新、改源码）
- `streaming.enabled` 开关
- `streaming.chat_types` 聊天类型过滤（群聊/私聊是否发卡片）
- `streaming.content_lang` 内容层提示语语言
- `streaming.header` / `footer` / `body` / `width_mode` 等 streaming 结构类配置
- 飞书凭据 `app_id` / `app_secret`

```bash
hermes gateway restart
```

> ⚠️ 注意：`hermes gateway restart` 需要**在 gateway 进程之外的独立 shell** 中执行。
> 若从 gateway 进程内部（如通过 AI 对话让 agent 执行）触发，SIGTERM 会传播到子进程，
> 重启命令本身会被杀掉。请在本地终端手动运行。

> ✅ 重启不会损坏任何东西，可放心反复执行（**可以无限重启**）。

重启后的效果验证：`hermes_fry_cards status` 显示所有 hook `installed`，飞书渠道连接正常即可。

---

## 故障排查

| 现象 | 原因 | 解决方案 |
|------|------|----------|
| CardKit `300313` 报错 | 卡片元素接近飞书 200 上限 | 等待自动拆分 |
| Hook 丢失 | Hermes 升级覆盖了已 patch 的文件 | `verify` + `install` + 重启 |
| 流式卡片变纯文本 | CardKit 创建失败 | 日志检索 `card_reply_failed` / `fallback_to_text` 看具体原因，检查飞书凭据是否正确 |
| `status` 显示 `warning` | CLI 使用了错误的 Python 解释器 | 用 `$HERMES_PYTHON` 重新执行 |
| 卡片一直 loading 不收尾 | 完成更新失败（如元素 Duplicate ID） | 日志检索 `card_complete_failed`，确认版本 ≥ v0.1.1（含竞态修复） |

---

## 📦 镜像仓库

- **GitHub 主仓库**：[techysy/hermes-fry-cards](https://github.com/techysy/hermes-fry-cards)
- **Gitee 国内镜像**：[techysy/hermes-fry-cards](https://gitee.com/techysy/hermes-fry-cards) — 国内访问更快，与 GitHub 同步

---

## 🔗 相关项目

| 项目 | 说明 | 特性 | Stars |
|------|------|------|-------|
| [🍟 hermes-fry-cards](https://github.com/techysy/hermes-fry-cards) | 薯条卡片 — Hermes 飞书流式卡片插件 | 🎯 工具调用合并 · 统一面板 · 状态色框 | ⭐6 |
| [👸🏻 aiduPOP](https://github.com/monkey2jack/aiduPOP) | 爱嘟泡波卡 — Hermes 飞书流式卡片插件 | 🫧 泡波样式 · 透明治愈 · 灵动 UI | ⭐11 |
| [🎭 lark-hls-v2](https://github.com/BcubBo/lark-hls-v2) | 飞书 CardKit v2.0 流式卡片插件 for Hermes Agent | 🎭 二次元画风 · 动态台词 · 场景检测 · Fisher-Yates 洗牌 | ⭐8 |

---

## 🔄 与上游 hermes-lark-streaming 的差异

本项目基于 [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) v0.12.0 独立开发。核心流式引擎沿用上游 AST hook 注入架构，在此之上做了三层增强：

### ✨ 新增功能

| 功能 | 说明 | 上游状态 |
|------|------|----------|
| **统一面板** | 推理 + 工具调用合并到底部统一面板；`unified_panel_min_duration` 控制无工具且耗时短时自动隐藏 | ❌ 推理面板独立散排，多轮对话卡片冗长 |
| **上下文进度条** | `show_context` 独立开关 + `context_display_mode` 三种模式（`text` / 渐变阴影 `bar` / `text_bar`），智能单位（<1M 用 k） | ❌ 仅 footer 纯文本百分比，无开关 |
| **推理面板上限** | `max_reasoning_panels`（默认 3），超出合并进最后一个面板——兼容 deepseek-v4-flash 等不分段思考模型，防 300305 元素溢出 | ❌ 无限制，长思考必溢出 |
| **模型名截断** | `truncate_model_name`：`nvidia/moonshotai/kimi-k3` → `⇲kimi-k3`，修复移动端换行 | ❌ 全称显示 |
| **模型别名** | `~/.hermes/model_aliases.json` 独立 JSON 配置：`{"longcat": "哈基米"}` 子串匹配 + **时段人设对象**（北京时间 HH:MM + 星期自动切换，如峰谷 梁文锋⚡️/梁文谷⚡️，与 openclaw/claw-fry-cards 格式逐字兼容），带总开关；每次渲染重读，Studio 可视化编辑 | ❌ 无 |

行为默认值：`show_reasoning` 默认 **true**（上游默认 false）。

### 🔧 关键修复（上游存在的坑）

- **300305 元素超限强制拆卡恢复** — 卡片实际元素总数超飞书硬上限时，自动封印旧卡、未创建 segment 迁移新卡续流，不再永久卡「处理中」
- **远程图片 URL 过滤** — 规避 CardKit 拒绝远程 URL（`200570 invalid image keys`），无法上传的引用先 strip 并包代码围栏
- **完成态 Duplicate ID 修复体系** — reasoning `text_el_id` 全链路复用 + 带索引唯一 ID 兜底；上游完成态用固定 `reasoning_text` 会与流式阶段元素冲突，导致卡片卡 loading
- **seal/complete 失败清理 loading 图标** — 收尾失败不再残留「处理中」状态

### 🏛️ 内部稳定性重构（v0.1.1）

- session 生命周期统一入口（注册/清理幂等）、interrupt 后不误删新会话映射、终态会话可重建
- FlushController 竞态防护：完成态快照防误重刷、遗留 timer 取消防双刷
- `mark_failed(reason=...)` 失败原因可追溯 + 结构化日志事件 + `_yield_to_gateway` 单一回落决策点
- 测试规模 15 个文件、新增 9 个稳定性回归测试；配套 CONFIGURATION / TROUBLESHOOTING 文档

---

## 📄 归属说明

灵感来自 [hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)（作者 Cheerwhy），原项目使用 MIT 协议。本项目为独立开发版本，已进行架构重写和功能重构。

## 📄 许可证

[MIT](LICENSE)
