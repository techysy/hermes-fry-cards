## 🍟 hermes-fry-cards v0.7.6

> 本地模型「防卡外泄漏」与「保留 💭 面板」不再二选一：新增卡片级 `streaming.show_reasoning` 覆盖（优先级最高），平台级开关语义与 Hermes 核心门控行为一律不动。顺带把 27 张图标源图从 640×640 降到 256×256，上传体积近乎减半。

### 🎯 卡片级推理面板开关（外部网友反馈）

此前卡片 💭 面板与 Hermes 核心的 💭 前置块 / 卡外 interim 泄漏门控**共用同一条** `display.platforms.feishu.show_reasoning → display.show_reasoning` 链。本地部署模型（vLLM + Qwen3 thinking 一类原生 `reasoning_content` 输出）的用户为了防卡外泄漏必须把平台级设成 `false`，可这同时把卡片的 💭 面板与计数一起关掉了——两个语义相反的需求没法解耦。

现在多了一级**卡片级覆盖**，优先级最高：

```yaml
streaming:
  show_reasoning: true       # 卡片级覆盖：💭 面板照常入卡（优先级最高）
display:
  platforms:
    feishu:
      show_reasoning: false  # 平台级：核心 💭 前置块 / 卡外泄漏门控保持关闭
```

- **解析顺序**：`streaming.show_reasoning` → `display.platforms.feishu.show_reasoning` → `display.show_reasoning` → 默认 `true`（与 `streaming.process_expanded` 覆盖 `panel_expanded` 同一模式）
- **零行为变更**：未设卡片级键时与旧版完全一致；平台级语义不动，Hermes 核心门控代码一行没改
- **免重启热生效**：走 `_reload`（mtime + size 守卫），改完 config.yaml 下一条消息即生效，进行中的卡保持创建时的样式
- 配置说明见 [docs/CONFIGURATION.md](https://github.com/techysy/hermes-fry-cards/blob/main/docs/CONFIGURATION.md) 的「本地模型防泄漏 + 保留 💭」节

### 🔍 门控早退可观测

原生推理增量被配置丢掉时，记一条 `logger.debug` 说清**是哪一级配置键关掉的**（新增 `Config.show_reasoning_value()` 返回「生效值 + 来源键」）。同一来源只记一次、开→关切换后重记，不逐 delta 刷屏：

```
on_reasoning: native reasoning delta dropped — disabled by display.platforms.feishu.show_reasoning
(card-level streaming.show_reasoning overrides the platform chain)
```

排障时把日志级别开到 debug 就能看到，不必再靠猜哪一层开关生效。

### 🖥 Studio 三态控件

- 「统一面板」区新增「卡片推理面板覆盖」下拉：**跟随平台 / 强制开 / 强制关**
- `/api/config` 白名单接受 `streaming.show_reasoning`（`bool`，或 `null` = 清除覆盖回落平台链）；`merge_managed` 支持显式 null 删键
- `/api/state.display.show_reasoning_override` 单独暴露显式覆盖值（`null` = 未设），与合并后的生效值分开，避免 UI 把「跟随平台且平台为开」误显示成「强制开」
- 预览（流式占位卡 💭 面板 + 完成态 💭 段）按卡片级优先的生效值渲染，与线上同点收口

### 🪶 图标源图 640 → 256（v0.7.5 内容，随本次一并发出）

`assets/vendor-logos` 20 张 + `assets/agent-logos` 7 张全部由 640×640 等比重采样为 **256×256**。卡片上图标最大渲染尺寸远小于 640，源图分辨率纯属上传负担。

- 预乘 alpha Lanczos 高质量重采样，仍是 8 位调色板 PNG（行滤波 0 + `tRNS` 透明索引）
- **配色与图源一律不动**：瓦片家族中性底 `(72,75,84)` 与圆角 / inset 几何按 0.4× 等比缩放，Claude Code 品牌橙 `#D97757`、gemini 四色渐变按「误差达标里最小」的色数档位保留
- 27 张合计 **269.2 KB → 135.1 KB（−49.8%）**
- 逐张像素复核（纯标准库读 `PLTE` + `IDAT`，**不引入 Pillow**）：尺寸、主色、白字形在浅色面板上的深色像素占比守卫（agent >0.5 / vendor >0.2）、两目录零重名全部照旧；与未量化 Lanczos 参照物比对，可见像素平均 RGB 误差 ≤0.6（gemini 渐变 1.48 为全库最大）
- 上传缓存键结构不变（`app|目录/文件名|sha256[:12]`）：像素换了摘要就变，每张用过的 logo 重传一次，之后照旧命中缓存

### ✅ 验证

- 全量 **1306 passed**（上版 1282，新增 24 例）：三级优先级解析（卡片 true / 平台 false、卡片 false / 平台 true、缺省回落）、热重载改键 / 删键、门控早退不影响答案入卡、早退日志来源与去重、Studio 校验 / 合并 / null 清除 / 端到端 roundtrip / 预览覆盖 / UI 接线
- ruff **139** errors、mypy **18** errors，与 HEAD 基线**逐条比对零新增**
- 新增 `test_all_logos_are_256_square` 锁住尺寸口径，避免以后混进大图把上传体积悄悄涨回去

### 📦 升级

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
$HERMES_PYTHON -m hermes_fry_cards verify
hermes gateway restart          # 卡片链路改动需重启网关生效
```

源码安装（editable）：

```bash
git clone https://github.com/techysy/hermes-fry-cards.git
cd hermes-fry-cards
$HERMES_PYTHON -m pip install -e . --no-deps
hermes gateway restart
systemctl --user restart hermes-fry-cards-studio   # Studio 侧（8765）
```

> 本次改了 `config.py` / `controller.py` / `studio/`（**不涉及 AST 注入锚点，无需 uninstall/install 重打补丁**），
> 需要 `hermes gateway restart` 让新代码生效。默认配置零改动即可保持旧行为；想用新能力再加 `streaming.show_reasoning` 一行。

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.7.4...v0.7.6](https://github.com/techysy/hermes-fry-cards/compare/v0.7.4...v0.7.6)