## 🍟 hermes-fry-cards v0.1.0-rc2

> 灵感来自 [hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)，独立开发版本。

### ✨ 主要功能

- **流式卡片** — 打字机效果实时输出，按事件顺序动态渲染思考 / 工具 / 回答
- **工具调用合并** — 多个工具调用更新同一 pending 面板，保持卡片紧凑
- **推理展示** — 多轮推理复用面板，支持展开/收起
- **状态色框** — 顶部 header 根据状态自动着色（蓝/绿/红）
- **终态卡片** — token 用量、耗时、上下文窗口
- **图片解析** — markdown 图片自动上传为飞书 img_key
- **卡片拆分** — 接近 200 元素上限时自动分卡
- **Cron 推送** — 定时任务结果以卡片推送
- **中英双语** — 根据飞书客户端语言自动切换

### 🔧 本次修复（基于 dsh 代码审查）

- 版本号统一为 `0.1.0-rc2`
- footer 显示完整模型名，不再截断供应商
- 修复 `reasoning_text` Duplicate ID 导致完成态卡片无法收尾（统一面板复用流式元素 ID）
- 修复 `CardKit batch update failed: not find elementID: tool_panel`
- 统一面板 header 空耗时不再产生尾部空格
- 简化 `_completion_session` 冗余条件，明确 FAILED 收尾逻辑
- `status` 命令复用 `marker_status()`，避免重复读文件
- cron patcher 失败输出日志
- `feishu.py` User-Agent 动态读取版本号
- 阈值注释修正（余量 18 波动 + 2 footer）

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
