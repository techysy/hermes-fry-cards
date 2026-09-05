# Hermes 大版本升级后卡片不生效 / Card not showing after Hermes major upgrade

> 独立排障文档，供 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 引用。
> 实测场景：Hermes 从旧版升到 **0.21.0（modular gateway 布局）** 后，飞书消息只有纯文本回复、**卡片不出现**。适用于任何 `hermes update` 大版本跨级后。

---

## 症状

- 飞书消息收到正常文字回复，但**没有流式卡片**（或卡片时有时无）
- `fry-cards status` 显示 `Patched: yes` 且 15 个 hook 全 installed——**源码层注入是好的**
- 网关日志里 `on_message_started: missing message_id` 或干脆无 fry-cards 活动

## 根因：hook 注入时间晚于网关启动

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

## 修复

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

## 预防（升级正确顺序）

1. **先**跑 `hermes update` 让 Hermes 升到新版
2. **再** `fry-cards install` 注入/重注入 hook（针对新布局）
3. **最后** `hermes gateway restart`

> 关键：**hook 注入必须发生在网关启动之前**，否则当前网关进程加载不到。升级后不要只装插件不重启网关。
> Hermes 0.21 起 gateway 采用 modular 布局（`run_inbound/run_turn/run_turn_runner/run_busy` + `cron/scheduler_delivery`），需 fry-cards ≥ 0.2.0 才支持（见 CHANGELOG）。
