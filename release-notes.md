## 🍟 hermes-fry-cards v0.6.3

> 兼容性升级：适配 Hermes 0.23+（及 0.22.6+）后台任务模块拆分（#57）。

### 🛠️ 适配 Hermes 0.23 后台任务模块拆分（#57）

Hermes 从 0.22.6+ / 0.23.0 起，将原先位于 `gateway/run_turn.py` 的 `/bg` 后台任务投递逻辑
`_run_background_task` 迁移到了独立的微模块 `gateway/run_background.py`（`GatewayBackgroundMixin`）。

旧版本补丁器将 `bg_deliver` 锚点硬编码在 `run_turn.py`，升级到 Hermes 0.23+ 后会导致该钩子丢失（状态显示 `bg_deliver: missing`）。

本版完成适配：
- **微模块动态探测**：`patcher.py` 自动探测 `gateway/run_background.py` 并纳入口径，`modular_paths` 增加 `background` 白名单；
- **智能锚点挂载**：`bg_deliver` 优先挂载到独立的 `run_background.py`；在未拆分的老版本 Hermes 环境下自动平滑回退至 `run_turn.py`；
- **真实源码回归测试**：新增 `tests/test_patcher_real_v023.py`，完整覆盖 Hermes v0.23.0 真实源码树下的注入、有效性、幂等性与可逆性验证；
- **实机验证**：已在实机 Hermes v0.23.0 环境下完成注入、状态校验与网关平稳运行验证，所有 24 个钩子全部正常落位。

### ⚙️ 其他

- 全量 **1084 passed**，ruff / mypy 零新增

### 📦 升级

升级至 Hermes 0.23+ 后，建议重跑 `uninstall + install` 使后台投递钩子迁移至新模块：

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards     # PyPI，或源码方式 git pull + pip install -U .
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
systemctl --user restart hermes-gateway               # 或 hermes gateway restart
```

### 🔍 升级后自查

| 检查项 | 正常表现 |
| --- | --- |
| `$HERMES_PYTHON -m hermes_fry_cards status` | `bg_deliver: installed`，所有状态正常 installed |
| 运行 `/bg` 任务（如后台长任务） | 完成后以飞书交互式卡片准确投递至对应会话 / 话题 |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.6.2...v0.6.3](https://github.com/techysy/hermes-fry-cards/compare/v0.6.2...v0.6.3)
