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
