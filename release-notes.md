## 🍟 hermes-fry-cards v0.1.1（首个正式版本）

> 灵感来自 [hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)，独立开发版本。

经过 rc2–rc7 七轮预览版迭代，插件核心链路趋于稳定，正式发布 🎉

### 🛡️ 本次更新：内部稳定性优化

对照 OPTIMIZATION_PLAN 差距分析，落地全部 P0 三项与 P1 补齐。**配置项与卡片样式零变化**，升级无需改任何配置。

**session 生命周期统一**

- 新增 `_register_session` / `_dispose_session` 单一入口，清理幂等（重复执行不报错）
- 中断接管后旧 session 清理不再误删新 session 映射
- 同 message_id 旧会话已终态时允许重建（原先永久拒新直到重启网关）

**FlushController 竞态修复**

- 完成标记与重刷请求交叉时，不再对已完成卡片发起多余的 CardKit API 调用
- 消除「timer 触发 + 立即路径」双刷同一份数据的问题

**失败分类与结构化日志**

- 失败原因随 session 记录且不被后续覆盖，排障可直接定位首次失败点
- 建卡失败区分飞书 API 错误码与未知错误
- 日志事件标准化：`session_created` / `card_created` / `card_reply_failed` / `fallback_to_text` / `session_disposed`

**质量保障**

- 新增 9 个回归测试：499 通过 / 仅剩 4 个基线遗留失败
- 自 rc7 起默认值已对齐推荐配置，新装开箱即用

### 🚀 安装

```bash
curl https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/INSTALL.md
```

### 手动安装

```bash
git clone https://github.com/techysy/hermes-fry-cards.git
cd hermes-fry-cards
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -e .
$HERMES_PYTHON -m hermes_fry_cards verify
$HERMES_PYTHON -m hermes_fry_cards install
```

然后重启 gateway（在外部终端执行）：

```bash
hermes gateway restart
```

### 卸载

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m pip uninstall hermes-fry-cards
```

---

[MIT License](LICENSE) · [安装文档](INSTALL.md) · [English](README.en.md)
