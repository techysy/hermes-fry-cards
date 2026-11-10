## 🍟 hermes-fry-cards v0.6.4

> 综合稳定性升级：修复后台回执、上下文压缩、Clarify 批量签名、流式写入丢字、卡片删除动作格式等系列核心缺陷。

### 🚀 重点修复与改进

1. **后台完成回执不截断父会话唤醒（#58）**：
   - 修复 `on_background_completion` 在呈现回执卡后避免过早 `return True`，确保网关继续注入内部消息并顺利唤醒父会话 Agent 继续工作。
2. **上下文压缩期间保留流式卡片（#59）**：
   - 修复 `on_compression` 在压缩发生时不强行提前关闭卡片，避免本轮正常完成事件因卡片已关闭而被迫降级纯文本重复投递。
3. **Clarify 回调批量问题签名与冷启动韧性（#62）**：
   - `_clarify_hook` 支持通用形参透传 `(*args, **kwargs)`，完美适配 Hermes 0.23 的批量问题签名；冷启动时按需懒加载回调 SDK 类。
4. **流式写入并发安全与防丢字（#63）**：
   - 写入前记录文本快照并在写完后对比更新 `seg.dirty`，彻底杜绝 await 网络等待期间到达的增量被静默丢弃的问题。
5. **降级路径 CardKit loading 图标删除格式修复（#64）**：
   - 将 `_remove_loading_icon` 改为 CardKit 标准动作 `delete_elements`。
6. **打断状态保留与旧回合完成事件隔离（#65）**：
   - 封卡钩子透传 `interrupted`，被打断的轮次封卡显示为停止状态并保留已有文本；普通打断丢弃旧回合尾巴，防止迟到的完成事件错误覆盖新卡。
7. **配置并发安全重载与坏 YAML 容错（#66）**：
   - `Config._reload` 加入 `RLock` 保护防并发乱序，并在文件正在保存或语法错误时安全回退保留上次有效配置。
8. **300313 思考正文缺失自愈补建（#67）**：
   - 捕获 300313 且定位到正文元素缺失时自动执行 `_restore_reasoning_body` 重建元素并写回文本。
9. **收尾等待唤醒机制加固（#68）**：
   - `FlushController` 在刷新任务完成回调中统一触发等待中的 future，根治特定时序下收尾挂死超时的问题。
10. **流式模式超时关闭降级（#69）**：
    - 捕获 CardKit 约 10 分钟后的服务端关闭码（300309），平滑降级为 `partial_update_element` 增量更新，卡片不再卡死。

### ⚙️ 其他

- 全量 **1091 passed**，新增专项目标回归测试集 `tests/test_issues_fixes.py`

### 📦 升级

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -U hermes-fry-cards
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
systemctl --user restart hermes-gateway
```

---

完整明细见 [CHANGELOG.md](https://github.com/techysy/hermes-fry-cards/blob/main/CHANGELOG.md)。

**Full Changelog**: [v0.6.3...v0.6.4](https://github.com/techysy/hermes-fry-cards/compare/v0.6.3...v0.6.4)
