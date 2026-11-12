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
