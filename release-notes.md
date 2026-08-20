## 🍟 hermes-fry-cards v0.1.0-rc1

> 灵感来自 [hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)，独立开发版本。

### 主要功能

- **流式卡片** — 打字机效果实时输出，按事件顺序动态渲染思考 / 工具 / 回答
- **工具调用合并** — 多个工具调用更新同一 pending 面板，保持卡片紧凑
- **推理展示** — 多轮推理复用面板，支持展开/收起
- **状态色框** — 顶部 header 根据状态自动着色（蓝/绿/红）
- **终态卡片** — token 用量、耗时、上下文窗口
- **图片解析** — markdown 图片自动上传为飞书 img_key
- **卡片拆分** — 接近 200 元素上限时自动分卡
- **Cron 推送** — 定时任务结果以卡片推送
- **中英双语** — 根据飞书客户端语言自动切换

### 安装

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

### 后续计划

- 完善测试覆盖
- 支持更多自定义样式
- 优化性能

---

[MIT License](LICENSE) · [安装文档](INSTALL.md) · [English](README.en.md)
