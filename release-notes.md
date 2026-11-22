## 🍟 hermes-fry-cards v0.7.0

> 统一面板厂商 logo（PNG）+ 官方模型名显示——原生体验，无 emoji。

### 🏷️ 厂商 logo 上卡

命中模型家族时，统一面板 header 的**前缀图标槽位**（原 🍟 品牌位）渲染厂商 PNG logo：

- `icon_position: "left"`（header 层级字段，原 🍟 位置，logo 在文字前面）
- `icon_expanded_angle: 0`——不随面板展开旋转，展开/收起箭头让位于 logo
- 16px 尺寸（默认 10px 过小）
- footer 的 model 字段行首内联同款 PNG
- 未命中 / 上传失败 / 开关关闭：header 回落默认箭头，footer 回落 🍟 + 原样模型名

logo 资产来自 [@lobehub/icons-static-png](https://github.com/lobehub/lobe-icons)，随包分发 20 家：
DeepSeek / Qwen / Zhipu / Claude / Gemini / Kimi / Moonshot / MiniMax / Doubao / **Hunyuan** /
StepFun / OpenAI / Grok / LongCat / MiMo / dots / Muse / Ling / Upstage / CodeBuddy。
首次渲染懒上传换取 img_key（进程内缓存 + 并发去重），失败静默降级，不影响出卡。

### 🪪 官方模型名

logo 命中家族时，model 字段显示厂商官网口径名（剥渠道前缀与 `:tag`，官方家族拼写 + 变体尾巴）：

```
tencent/hy4-flash:free  →  Hy4 flash      （腾讯混元官网口径）
deepseek-v4.1-flash     →  DeepSeek v4.1 flash
qwen4-max               →  Qwen 4-max
```

未命中家族时维持 v0.6.7 起的实际服务模型直显。家族映射修正：`hy4` → 腾讯混元 Hunyuan
（hy = Hy 家族，CodeBuddy 只是分发渠道——此前误挂 CodeBuddy logo）。

### ⚙️ 其他

- `img.alt` 必须是 PlainText 对象（裸字符串 CardKit 报 10002，API 实测定位修复）
- `display.vendor_logo` 开关（默认开启），Studio 统一面板区已接线
- 全量 **1134 passed**

### 📦 升级

纯运行时改动，注入体不变——`git pull` / `pip install -U` 后重启服务即可：

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
systemctl --user restart hermes-gateway hermes-fry-cards-studio
```

（从 0.6.8 之前的版本升级，或 `status` 提示 hook 过期时，仍需 `uninstall + install`。）

---

## 🍟 hermes-fry-cards v0.6.8

> v0.6.7 路由可见性实机回归修复：捕获在真实 OpenAI SDK 上从未生效，现已修复。

### 🔧 修复（v0.6.7 回归）

实机验证发现 v0.6.7 的 body 模型捕获在真实环境**全程未生效**（卡片仍显示组合名），
两个独立根因：

1. **SDK 包装器误判**：OpenAI SDK 的 `chat.completions.create` 被 `required_args`
   同步包装器包过（`__wrapped__` 存在、`iscoroutinefunction` 返回 False），安装器
   把这个检测当门控，真实客户端被整只跳过。改为双模式包装——按「返回值是否
   awaitable」分流，不再依赖协程检测。
2. **START 安装点缓存键不匹配**：START 注入点处 locals 只有 `_quick_key`，
   与 `_agent_cache` 的键不匹配，查找落空。新增 `ensure_served_model_capture_ctx`：
   经 `turn_ctx.agent_holder` 直通本轮 agent，接入 tool / answer 钩子——首个
   delta 回调即安装，多步回合从第二次 API 调用起全部捕获（含最终响应）。

### ⚙️ 其他

- 全量 **1119 passed**，新增 3 项回归测试

### 📦 升级

**注入体有改动，必须重跑 `uninstall + install`**：

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
systemctl --user restart hermes-gateway
```

---

## 🍟 hermes-fry-cards v0.6.7

> 路由可见性：卡片直接显示本轮实际服务的模型——纯插件侧实现，10R/LiteLLM 式代理零改动。

### 🛰️ 卡片显示实际路由模型

10Router 等路由代理会把**实际服务的模型名**回写进响应体 / 流式 chunk 的 `model` 字段
（实测：请求 `cbcn/hy4-preview`，回来 `model: hy4-preview`——渠道前缀剥掉露出实际模型），
但响应头里没有路由信息，此前卡片上的 🍟 model 字段只能看到配置名。

本版在插件侧补齐捕获链（利用 Hermes 自带的 `agent.last_served_model` 槽位，#54864）：

- **双面包裹**：非流式主路径（`agent.client.chat.completions.create`）与流式每请求客户端
  （`_create_request_openai_client` 工厂）全部覆盖，create 返回 / 首 chunk 到达时捕获；
- **与 Hermes 头捕获共存**：头捕获每响应先跑（无头置 None），body 捕获后写获胜；
- **START 钩子回合开始即装**（幂等），暖缓存会话从本轮第一次 API 调用起就覆盖；
- **完成态渲染**：footer 与统一面板的 model 字段在实际模型与配置名不同时显示
  `🍟 配置名 → 实际模型`；相同 / 缺失与旧版渲染逐字一致；
- **全程 fail-open**：同步面、anthropic api_mode、旧版 Hermes、会话冷启动首次回复
  ——一律无值不加箭头，不影响任何现有行为。

**路由代理无需任何改动**；Hermes 核心无需改动。

### ⚙️ 其他

- 全量 **1116 passed**，新增 8 项专项测试，ruff / mypy 零新增

### 📦 升级

**本版改动了 START 钩子注入体，必须重跑 `uninstall + install`**：

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
systemctl --user restart hermes-gateway               # 或 hermes gateway restart
```

### 🔍 升级后自查

| 检查项 | 正常表现 |
| --- | --- |
| `$HERMES_PYTHON -m hermes_fry_cards status` | 所有钩子 installed |
| 带工具调用的多步回复（会话第二轮起） | 完成态卡片 model 字段显示 `🍟 配置名 → 实际模型` |
| 单轮单次调用且会话冷启动的首次回复 | 无箭头（agent 未进缓存，预期 fail-open），之后恢复 |

---

## 🍟 hermes-fry-cards v0.6.6

> 健壮性复查专项：修复 v0.6.5 热重载在生产上失效的回归，加固配置重载与流式降级边缘。

### 🚀 重点修复

1. **热重载生产回归修复（P1）**：
   - v0.6.5 的 `_reload()` 无条件短路返回 `_raw`，而生产中 `_platform_cfg()` 读凭据会经
     `_load()` 把磁盘配置固化进 `_raw`，导致样式热重载（#71）与 `show_reasoning` 热重载（#17）
     在生产上永久失效。
   - 修复后 `_load()` 落盘时标记 `_raw_is_disk`，生产一律走 `(mtime_ns, size)` 热重载；
     `_raw` 短路仅保留给测试注入。
2. **`_reload` 边缘加固**：
   - 顶层非 dict YAML（列表/标量）回落 `{}`，不再把 `None` 抛给调用方；
   - 坏 YAML 解析失败记录 key，防逐 delta 重解析刷日志；用户保存完成后自动恢复；
   - `stat` 失败（编辑器原子替换窗口）回落上次有效配置。
3. **换卡重置流式降级标记**：拆卡 / 切卡 / 重定向建新卡时重置 `stream_closed`，
   新卡是新的 CardKit 流式会话，不继承旧卡的 300309 降级状态。
4. **`_interrupted_tails` 容量上限**：打断尾巴记录 FIFO 64 条，防长期运行无界增长。
5. **合并重复的 `panel_expanded` 定义**（v0.6.5 引入的重名覆盖）。

### ⚙️ 其他

- `test_studio` 对代理环境免疫（loopback 无代理 opener）
- 全量 **1103 passed**，新增 7 项健壮性回归测试，ruff / mypy 零新增

### 📦 升级

本版注入点无变化，常规升级即可（无需重跑 `uninstall + install`）：

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
systemctl --user restart hermes-gateway               # 或 hermes gateway restart
```

---

## 🍟 hermes-fry-cards v0.6.5

> 体验与热重载升级：消灭 Hermes 核心独立 reasoning 补发重复消息；支持样式配置免重启热重载与无色带清爽标题栏（#71）。

### 🚀 重点修复与改进

1. **消除 Hermes 核心补发的重复独立 reasoning post 消息**：
   - Hermes 0.23+（commit `67664e2`，#7251）在流式结束后会自动将 reasoning 作为独立 post 消息补发到聊天。
   - 由于卡片内已完整流式渲染 💭 思考折叠面板，此补发会导致同一份思考双显且卡外多一条消息。
   - 补丁器在卡片完成收尾时自动清除 `agent_result["last_reasoning"]`，彻底杜绝卡外多余的独立思考消息。
2. **卡片样式配置热重载（#71）**：
   - `_streaming_sec` 全面接入 `_reload()` 结合 `(mtime_ns, size)` 动态重载体系，在 `config.yaml` 中修改任何 `streaming.*` 样式配置后，**无需重启网关进程**，下一张新建的卡片立刻按新配置渲染。
   - 正在运行的卡片在生命周期内使用自己的配置快照，流式更新到封卡始终保持风格一致，杜绝跑一半突变。
3. **无色带标题栏支持（#71）**：
   - 支持配置 `streaming.header.template: default`，关掉彩色彩条标题栏（blue/green/red），仅保留状态图标与文字，视觉清爽高级。
4. **过程框默认展开控制（#71）**：
   - 支持通过 `streaming.process_expanded: true/false` 自由指定思考/工具过程框默认是展开还是收起。

### ⚙️ 其他

- 全量 **1096 passed**，ruff / mypy 零新增

### 📦 升级

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
systemctl --user restart hermes-gateway
```

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.6.4...v0.6.5](https://github.com/techysy/hermes-fry-cards/compare/v0.6.4...v0.6.5)
