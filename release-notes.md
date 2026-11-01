## 🍟 hermes-fry-cards v0.6.2

> 安全专项：群聊非管理员工具隔离（#55）——群友的 Prompt 诱导再强，也调不到没下发的
> 写入/执行类工具；附带 #56 的封卡诊断改进（全树递归元素数）。

### 🛡️ 群聊非管理员工具隔离（#55）

飞书群聊里，普通群友通过自然语言 Prompt 驱动 Agent 时，此前依然拿到宿主机的全量
工具集——群友发个链接加一句「去抄吧」就能诱导连续 8 次 `execute_code` 跑 2 分钟
（#55 的踩坑现场）。斜杠命令有 `allow_admin_from` 分权，自然语言工具调用没有。
本版补上这道硬边界：

- **查询类可用、写入/执行类默认禁**：边界开了（`enabled: true`）即生效，非管理员
  群聊轮次默认剔除 `execute_code` / `terminal` / `file` / `computer_use`，查询类
  （web_search、memory、clarify …）照常
- **黑名单永远优先于白名单**：`non_admin_allowed_toolsets` 是严格白名单，
  `non_admin_disabled_toolsets` 在默认黑名单之上追加；显式写进白名单的写入类
  工具集也会被剔除，配置笔误打不开边界
- **一处注入，两条路径全覆盖**：挂在 `gateway/run_turn.py` 的
  `_resolve_turn_toolsets` 返回前（可选注入点 `TOOL_GATING`，modular 专用）——
  前台轮次与后台任务的工具集都从这一个解析口出去，高危工具集根本不进
  function calling 注册表
- **管理员判定不另设名单**：复用 Hermes 斜杠命令的同一套 `allow_admin_from`
  （`gateway.slash_access`）；判不了（旧版无模块 / scope 未配置 / 异常）一律不
  隔离——宁可漏防，不把群里所有人（含机主）锁死
- **豁免群共用** `allow_chats`（开发/协作群不受影响）；过滤结果同步并入
  `agent.disabled_toolsets`，agent 侧双保险
- Studio 校验白名单与状态采集接通；注入块缺失锚点时自动跳过（可选注入点）

配置示例：

```yaml
gateway:
  group_security_boundary:
    enabled: true                      # 开了即生效：非管理员默认只给查询类工具
    allow_chats: ["oc_dev_group"]      # 豁免群，可选
    non_admin_allowed_toolsets: []     # 可选：严格白名单
    non_admin_disabled_toolsets: []    # 可选：追加禁用项
```

**两点注意**：

1. 管理员判定要求为群聊 scope 配好 `allow_admin_from`（未配置 = Hermes 口径全员
   无限制，工具隔离随之不生效）；
2. 已建会话的 agent 按构建时工具集缓存（Hermes 自身设计），启用后需**重启网关**
   （或对群聊会话 `/new`）才收紧存量会话。

### 🔍 封卡诊断：全树递归元素数（#56）

飞书 300305 校验的是**全树递归节点总数**，此前日志的 `card_elements` 只统计顶层
`len(body.elements)`——统一面板里嵌的工具组/思考面板每个都带一串子元素，现场
`card_elements=2` 实际全树超 200，一直误导排查。封卡失败与剥离面板重发的日志现带
`recursive=` 字段（`count_elements_recursive`）。

### ⚙️ 其他

- 全量 **1079 passed**，ruff / mypy 相对 v0.6.1 零新增

### 📦 升级

**本版新增注入点（`TOOL_GATING`），必须重跑 `uninstall + install`**；启用工具隔离
后重启网关使缓存 agent 收紧。

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards     # PyPI，或源码方式 git pull + pip install -U .
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
hermes gateway restart
```

### 🔍 升级后自查

| 现象 | 含义 |
| --- | --- |
| `$HERMES_PYTHON -m hermes_fry_cards status` | `tool_gating: installed` 即落位（0.21 以下 modular 缺解析口时为 skipped，功能降级） |
| 非管理员群账号发诱导执行的消息 | 日志 `group tool gating applied: chat=… toolsets=[…]`，Agent 侧无 execute_code/terminal/file 可调 |
| 管理员群账号 / 私聊 | 工具集原样，不受影响 |
| 群聊 scope 未配 `allow_admin_from` | 隔离不生效（Hermes 口径全员无限制），需先配管理员名单 |
| 工具密集长轮次封卡失败（若有） | 日志 `card_elements=… recursive=…` 两个数都有，`recursive` 才是贴近 300305 阈值的那个 |

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.6.1...v0.6.2](https://github.com/techysy/hermes-fry-cards/compare/v0.6.1...v0.6.2)
