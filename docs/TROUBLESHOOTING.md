# 排障指南 / Troubleshooting

---

## 1. 与 hermes-feishu-card (hfc) 插件冲突

### 症状

- 卡片出现两张（一张 hfc 中文卡 + 一张 fry-cards 卡 + 可能还有 Hermes 原生英文卡）
- 日志反复报 `[hermes-feishu-card] hook failed: ModuleNotFoundError: No module named 'hermes_feishu_card'`
- 卡片降级为默认文本回复

### 根因

hfc (baileyh8/hermes-feishu-streaming-card) 插件通过 AST patch 注入了以下位置：

| 文件 | patch 内容 |
|------|-----------|
| `gateway/platforms/base.py` | `finalize_exact_base_no_text` + `prepare_exact_base_final_delivery` |
| `gateway/run.py` | 多处 hook（被 fry-cards 覆盖后无冲突） |
| `cron/scheduler.py` | cron 推送 hook |

即使 hfc 的 pip 包已卸载，**base.py 里的残留 patch 仍会触发 import**，每次飞书消息到达时都会报错。

### 排查

```bash
# 1. 检查 base.py 是否有 hfc 残留
grep -n 'hermes_feishu_card' ~/.hermes/hermes-agent/gateway/platforms/base.py

# 2. 检查是否有 hfc manifest
ls -la ~/.hermes/hermes-agent/.hermes_feishu_card_manifest

# 3. 检查是否有旧插件目录
ls ~/.hermes/plugins/hermes-lark-streaming/
```

### 修复

```bash
# 1. 移除 base.py 中的 hfc patch（搜索 HERMES_FEISHU_CARD 标记）
#    删除 BEGIN 到 END 之间的整个 try/except 块

# 2. 清理 hfc 残留文件
rm -f ~/.hermes/hermes-agent/.hermes_feishu_card_manifest
rm -f ~/.hermes/hermes-agent/gateway/platforms/base.py.hermes_feishu_card.bak
rm -f ~/.hermes/hermes-agent/gateway/run.py.hermes_feishu_card.bak
rm -f ~/.hermes/hermes-agent/cron/scheduler.py.hermes_feishu_card.bak

# 3. 删除旧插件目录
rm -rf ~/.hermes/plugins/hermes-lark-streaming

# 4. 重启 gateway（必须从外部终端执行）
hermes gateway restart
```

### 预防

- 安装 fry-cards 前，先确认没有 hfc 残留：`pip list | grep -iE 'streaming|lark|fry|card'`
- 不要同时启用两个飞书卡片插件

---

## 2. 卡片不显示 / 降级为文本

### 可能原因

1. **飞书 App 权限不足** — 确保应用有 `im:message`、`im:message:send_as_bot` 权限
2. **CardKit API 未开启** — 飞书开放平台 → 应用 → 卡片相关权限
3. **token 过期** — 检查 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 是否正确
4. **hfc 冲突** — 见上方第 1 节

### 排查

```bash
# 检查 sidecar 状态
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards status

# 检查飞书连接
ss -tnp | grep ':443' | grep -i feishu

# 查看最近日志
journalctl --user -u hermes-gateway --since "5 min ago" | grep -iE 'fry|card|feishu|error'
```

---

## 3. 安装后 hook 未生效

### 原因

patcher 使用 AST 注入，需要特定函数名存在于 `gateway/run.py`。

### 修复

```bash
# 验证兼容性
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards verify

# 重新安装（先卸载再安装）
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards uninstall
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards install
```

---

## 4. Cron 推送不显示卡片

### 原因

cron hook 需要单独注入 `cron/scheduler.py`。

### 修复

```bash
# 检查 cron patch 状态
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards status

# 如果 cron 未 patch，重新安装
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards install
```

---

## 5. Hermes 大版本升级后卡片不生效

> 实测场景：Hermes 从旧版升到 **0.21.0（modular gateway 布局）** 后，飞书消息只有纯文本回复、**卡片不出现**。适用于任何 `hermes update` 大版本跨级后。

### 症状

- 飞书消息收到正常文字回复，但**没有流式卡片**（或卡片时有时无）
- `fry-cards status` 显示 `Patched: yes` 且 15 个 hook 全 installed——**源码层注入是好的**
- 网关日志里 `on_message_started: missing message_id` 或干脆无 fry-cards 活动

### 根因：hook 注入时间晚于网关启动

`hermes update` 大版本升级的流程通常是：

1. `hermes update` 拉新代码 → **自动重启 gateway**（或由升级脚本重启）
2. 之后才单独执行 `fry-cards install` 把 hook 注入 `gateway/run*.py`（0.21 是 `run_inbound.py` / `run_turn.py` / `run_turn_runner.py` / `run_busy.py`）与 `cron/scheduler_delivery.py`

如果**第 2 步的 hook 注入晚于第 1 步的网关启动**，那么运行中的网关进程加载的是**注入前/不完整**的源码，卡片 hook 根本没进内存。

判定方法（对比 mtime 与网关启动时间）：

```bash
# 注入文件 mtime
stat -c '%y %n' ~/.hermes/hermes-agent/gateway/run_turn.py \
                 ~/.hermes/hermes-agent/gateway/run_busy.py \
                 ~/.hermes/hermes-agent/cron/scheduler_delivery.py

# 网关启动时间 —— 若网关启动 < 上面任一注入时间 = 网关没加载最新 hook
systemctl --user show hermes-gateway -p ActiveEnterTimestamp
```

### 修复

**重启网关**，让它在 hook 已注入之后重新加载源码：

```bash
# ⚠️ 必须从网关进程外部的独立终端执行（网关内无法重启自己，会被 SIGTERM 拦）
hermes gateway restart
# 或
systemctl --user restart hermes-gateway
```

重启后发一条消息验证卡片。若重启后仍不生效，则可能是 hook 注入位置没命中 0.21 modular 布局的实际执行路径，需 `uninstall` + `install` 重新注入：

```bash
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards uninstall
~/.hermes/hermes-agent/venv/bin/python -m hermes_fry_cards install
```

### 预防（升级正确顺序）

1. **先**跑 `hermes update` 让 Hermes 升到新版
2. **再** `fry-cards install` 注入/重注入 hook（针对新布局）
3. **最后** `hermes gateway restart`

> 关键：**hook 注入必须发生在网关启动之前**，否则当前网关进程加载不到。升级后不要只装插件不重启网关。Hermes 0.21 起 gateway 采用 modular 布局（`run_inbound/run_turn/run_turn_runner/run_busy` + `cron/scheduler_delivery`），需 fry-cards ≥ 0.2.0 才支持（见 CHANGELOG）。
