# 群聊安全边界 / Group chat security boundary (modular Hermes 0.21+)

> 技术文档：本插件对 Hermes gateway 注入「群聊输出边界」的**实现、配置与排障**。
> 配套入口：[README.md](../README.md)（功能概述）、[CHANGELOG.md](../CHANGELOG.md)（v0.3.0）。

---

## 1. 功能

当 bot 在**群聊**里被群友 @ 时，默认向回复过程注入一条输出边界：模型**不透露** API key / 密码 /
令牌 / 服务器内网 IP / 凭据 / 私人数据路径，**不主动**执行敏感查询（账户 / 余额 / 凭据 / 内部状态）；
群友索要这类信息时**婉拒**并引导私聊。**私聊 DM 完全不受影响。**

## 2. 为什么做在插件里（不写 hermes 核心）

Hermes 是**独立更新**的（`hermes update` 会覆盖 `gateway/` 下的源码）。若把输出边界直接写进
`gateway/run_turn_runner.py`，一次 `hermes update` 就被还原。

**解决方案**：把逻辑放进本插件（`hermes_fry_cards/patch.py`），经 `install` 用 **AST 注入**打进
`run_turn_runner.py`。插件是独立仓库 + editable 安装，`hermes update` 不碰它；升级 Hermes 后重跑
`hermes_fry_cards install` 即重打。这是「需要改造 Hermes 内部行为的通用能力」应遵循的模式。

## 3. 实现要点

### 3.1 注入点

- **目标文件**：`gateway/run_turn_runner.py`（Hermes 0.21+ **modular** 布局专属；旧单文件布局
  `gateway/run.py` **没有** `_combined_ephemeral_prompt`，故本 hook **modular-only**）。
- **目标方法**：`TurnRunner._combined_ephemeral_prompt()`——ephemeral system prompt 的汇合处，
  每轮临时拼接，**不碰持久（缓存）system prompt**，因此不破坏 Hermes 的 prompt-cache 不变量。
- **注入位置**：方法体 `return combined` 之前。注入代码读 `self._ctx.user_config`（拿开关/
  白名单）与 `self._ctx.source`（拿 `chat_type`/`chat_id`），调
  `apply_group_security_boundary()` **就地改 `combined`**，再 `return`。

### 3.2 判定逻辑（`apply_group_security_boundary`）

```text
enabled == true
  且 chat_type == "group"
  且 chat_id 不在 allow_chats 白名单
→ 追加边界文案到 combined
```

- `enabled`：总开关（默认关）。
- `allow_chats`：**豁免群**（多 Agent 协作开发群放行自由交流）。
- 任何异常 fail-open：返回原 `combined`，不阻断网关。

### 3.3 Modular-only marker 处理（关键坑）

`GROUP_BOUNDARY` marker **没有**加进共享的 `_HOOK_NAMES`/`MARKERS` 列表，而是独立定义
`MK_GROUP_BOUNDARY`。原因：

- 旧单文件 Hermes 布局（`_inject_all` 路径）**不存在** `_combined_ephemeral_prompt`，无法注入，
  若进共享 MARKERS，legacy apply/verify 会因找不到注入点而硬失败。
- modular 布局（`_inject_modular` + `_verify_modular_target`）才注册 `group_boundary`。
- `Patcher.__init__` 检测到 modular 时，把 `(MK_GROUP_BOUNDARY, MK_GROUP_BOUNDARY_END)` 追加进
  `self.MARKERS`，使 `remove()`/`is_fully_patched()`/`status` 能正确清理与报告。

## 4. 配置（`~/.hermes/config.yaml`）

```yaml
gateway:
  group_security_boundary:
    enabled: true    # 总开关（默认关）
    allow_chats: []  # 豁免群 chat_id（飞书 oc_xxx），放行自由交流
    # text: <可选覆盖默认边界文案>
```

## 5. 验证

```bash
# 1) hook 是否注入
grep -n "GROUP_BOUNDARY" ~/.hermes/hermes-agent/gateway/run_turn_runner.py
#   应见 HERMES_LARK_GROUP_BOUNDARY_BEGIN / ..._END 包裹的 apply_group_security_boundary 调用

# 2) 插件状态
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards status   # group_boundary: installed

# 3) 运行态
#    群聊 @ 问服务器 IP / API key → 应婉拒并引导私聊
#    DM 问同样问题 → 不受限
#    allow_chats 里的群 → 不受限
```

改配置后需**重启网关**生效（`systemctl --user restart hermes-gateway`，从网关外终端执行）。
改代码后需重跑 `install` + 重启。

## 6. 升级注意

`hermes update` 大版本升级后，插件 hook（含 group_boundary）会被还原，**需重跑**：

```bash
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards install
hermes gateway restart   # 或 systemctl --user restart hermes-gateway
```

> 正确顺序永远是：`hermes update` → `fry-cards install` → 重启网关。
> 若升级后卡片/边界不生效，见 [HERMES-UPGRADE-CARD-FIX.md](HERMES-UPGRADE-CARD-FIX.md)
> （hook 注入时间晚于网关启动的根因与判定）。
