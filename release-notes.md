## 🍟 hermes-fry-cards v0.7.4

> 认不出模型不再等于没有图标：厂商未命中时回退智能体图标，连智能体也认不出就用本宿主品牌图兜底；同时修掉 7 张在白底卡片上等于隐形的厂商 Logo。

### 🎯 未识别模型兜底智能体图标

面板头左侧原来只在命中厂商家族时才有图标。代理池 / 自建网关的模型名（`qd/qfmodel`、`10router/10r-free`、`space-bunny-free` 一类）名字里没有厂商词，左侧就一片空白——用户看到的「模型那头空着」有一半是这个原因。

- **三级取图**：厂商命中优先（口径不变，厂商图 + 官方模型名）→ 模型串里的智能体词（Hermes / OpenClaw / Claude Code / Codex / ZCode / kimi-code / Antigravity）→ **本宿主品牌图**兜到最后一档
- **新增 7 张 `assets/agent-logos/`**，640×640 / 8 位调色板，与厂商资产同规格，随包分发
- **兜底图不改写模型名**：新增 footer 字段 `logo_kind`，官方名映射只跟随厂商命中；兜底档仍走 `model_aliases` 自定义别名与 `⇲` 截断，不会把你给路由名起的人设别名吞掉
- **开关语义不变**：`display.vendor_logo` 关掉时厂商图与兜底图**都**不出，模型名回落 🍟 品牌位

### 🐛 7 张厂商 Logo 白底上看不见

`openai` / `moonshot` / `grok` / `dotsstudio` / `upstage` / `xiaomimimo` / `kimi` 取的是 lobehub 的 **dark 变体**（纯白或近纯白），画在浅色面板头上等于没画。全部换成白底可见版本，并加测试锁住「每张资产都必须有足够深色像素」——此前的资产守卫只验 PNG 文件头，这正是白图能一路绿灯发到 v0.7.3 的根因。

- **上传缓存改按内容失效**：进程内 memo 的键原来只有裸文件名，换过像素仍命中旧 `image_key`，修好的图在早已发过卡的进程里永远显不出来。键改为 `app|目录/文件名|sha256[:12]`，换图即重传
- **打包漏带资产**：`pyproject.toml` 的 package-data 此前只登记 `assets/vendor-logos/*.png`。新增兜底目录已登记，实测 wheel 内 **vendor-logos 20 张 + agent-logos 7 张**齐全（不登记的话 editable 安装能跑、一发版就瞎）
- `logo_asset_bytes` 只接受纯文件名与两个固定目录，`../` 与路径分隔符一律拒绝

### 🖥 Studio 预览同口径

- 预览与线上**共用 `resolve_logo_asset`**，不会各说一套
- 新增 `/assets/agent-logos/` 静态路由，与厂商目录同等做纯文件名校验：`../`、跨目录取厂商文件名一律 404
- 模型速选补 `10router/10r-free` / `combo/Hermes` / `chat-mode-pool` 三档，兜底效果可直接目视验收
- 英文 README 的版本徽章是静态的、停在 `v0.4.1`（落后 3 个小版本），改为与中文 README 同源的动态 shields
- `docs/CONFIGURATION.md` 的 display 段补上一直缺失的 `vendor_logo` 说明

### ✅ 验证

- 全量 **1282 passed**（上版 1270，新增 12 例）：兜底解析 / 厂商优先 / 宿主品牌兜底 / 空模型不出图 / 资产白底可见性（纯标准库解 PLTE + IDAT 判亮度，**不引入 Pillow**）/ 两目录文件名零交集 / 路径穿越拒绝 / 缓存按内容失效 / 开关关闭时无任何图标
- ruff **139 → 138** errors、mypy **17** errors 不变，与 HEAD 基线**逐条比对零新增**
- 真浏览器实测：15 张图标在 **16px 面板头缩略图**下浅底 / 深底均清晰可辨（此前 7 张在该尺寸下完全空白）
- Studio 实测五档：`qd/qfmodel`、`combo/Hermes`、`10router/10r-free` → 兜底图；`tencent/hy4-flash:free`、`kimi-k5` → 厂商图 + 官方名；开关关掉 → 零 logo + 🍟 回落

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

> 本次改了 `streaming/controller.py` 与 `cardkit/`（**不涉及 AST 注入锚点，无需 uninstall/install 重打补丁**），
> 但需要 `hermes gateway restart` 让新代码生效。已经发过卡的机器无需清缓存——缓存键含内容摘要，换图自动重传。

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.7.3...v0.7.4](https://github.com/techysy/hermes-fry-cards/compare/v0.7.3...v0.7.4)