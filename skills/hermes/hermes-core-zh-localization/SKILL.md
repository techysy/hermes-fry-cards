---
name: hermes-core-zh-localization
description: "汉化 Hermes 核心系统消息（网关状态、工具进度、错误提示）为中文。hermes update 后需重新应用。"
version: 1.1.0
author: techysy
platforms: [linux]
metadata:
  hermes:
    tags: [hermes, i18n, localization, chinese, zh-cn, core, patch]
---

# Hermes 核心汉化补丁

将 Hermes 核心代码中的英文硬编码系统消息替换为中文。适用于中文用户环境。

## ⚠️ 注意事项

- **每次 `hermes update` 后需重新运行此补丁**
- 补丁只修改用户可见的状态消息，不影响核心逻辑
- 建议更新前先 `uninstall` 恢复英文，更新后重新 `install`
- 需配合 `config.yaml` 中 `language: zh` 使用（系统级语言设置）

## 与系统语言设置的联动

补丁安装时会检查 `~/.hermes/config.yaml` 中的 `language` 设置：

- `language: zh`（或 `zh-cn`、`zh-tw`、`auto`）→ 允许安装
- `language: en`（或其他非中文值）→ **拒绝安装**，提示用户先修改语言设置

这是为了避免补丁将系统消息汉化后，与系统层的英文配置产生冲突。

```bash
# 若 config.yaml 为 language: en，安装时会提示：
$ python3 patch.py install
⚠️  config.yaml 中 language 当前为 'en'，非中文设置。
   补丁会将系统消息汉化，与语言设置冲突。
   如需汉化，请先在 config.yaml 中设置 language: zh
   或运行: hermes config set language zh
   若需恢复英文，请运行: python3 patch.py uninstall
```

## 安装 / 卸载

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
PATCH=$HOME/.hermes/skills/hermes/hermes-core-zh-localization/scripts/patch.py

# 安装（汉化）
$HERMES_PYTHON $PATCH install

# 卸载（恢复英文）
$HERMES_PYTHON $PATCH uninstall

# 重启网关生效
hermes gateway restart
```

## 汉化内容

### 网关状态消息
| 英文 | 中文 |
|---|---|
| `Gateway is restarting` | `网关正在重启中` |
| `Gateway is shutting down` | `网关正在关闭中` |
| `not accepting new work right now` | `暂时无法处理新任务` |
| `queued for the next turn` | `恢复后将在下一轮处理` |

### 运行状态
| 英文 | 中文 |
|---|---|
| `⏳ Working — N min` | `⏳ 运行中 — N 分钟` |
| `iteration X/Y` | `迭代 X/Y` |
| `receiving stream response` | `正在接收流式响应` |
| `starting/completed API call` | `开始/完成 API 调用` |
| `executing tool / completed` | `执行工具 / 工具完成` |
| `stream retry` | `流式重试` |

### 忙碌/中断消息
| 英文 | 中文 |
|---|---|
| `Steered into current run` | `已引导至当前运行` |
| `Redirected current run` | `已重定向当前运行` |
| `Subagent working` | `子代理运行中` |
| `Compressing context` | `压缩上下文` |
| `Queued for the next turn` | `已排队等待下一轮` |
| `Interrupting current task` | `中断当前任务` |
| `Agent is running` | `代理正在运行` |

### 网关重启通知
| 英文 | 中文 |
|---|---|
| `♻ Gateway restarted successfully. Your session continues.` | `♻ 网关重启成功，会话继续。` |

### 上下文压缩
| 英文 | 中文 |
|---|---|
| `Compacting context` | `压缩上下文` |
| `✓ Context compaction complete — continuing turn...` | `✓ 上下文压缩完成，继续处理...` |
| `⠋ compressing N messages` | `⠋ 压缩中 N 条消息` |

### 会话错误
| 英文 | 中文 |
|---|---|
| `Session storage temporarily unavailable` | `会话存储暂时不可用` |
| `Session too large for context window` | `会话内容超出上下文窗口限制` |
| `Message interrupted before processing` | `消息在处理前被中断` |
| `Processing stopped / no response` | `处理已停止 / 未生成响应` |

### 提供商错误
| 英文 | 中文 |
|---|---|
| `Provider authentication failed` | `提供商认证失败` |
| `Model provider rejected request` | `模型提供商拒绝了请求` |
| `Model provider rate-limiting` | `模型提供商正在限流` |
| `Model server not responding` | `模型服务器未响应` |

### 其他
| 英文 | 中文 |
|---|---|
| `Hermes update finished/failed` | `Hermes 更新完成/失败` |
| `Cron job interrupted` | `定时任务被中断` |
| `Steer failed/queued/rejected` | `引导失败/已排队/被拒绝` |
| `Agent draining for maintenance` | `代理正在维护中` |
| `Context compression timed out/aborted` | `上下文压缩超时/已中止` |

## 重新安装流程

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
PATCH=$HOME/.hermes/skills/hermes/hermes-core-zh-localization/scripts/patch.py

# 1. 卸载旧补丁
$HERMES_PYTHON $PATCH uninstall

# 2. 更新 Hermes
hermes update

# 3. 重新安装补丁
$HERMES_PYTHON $PATCH install

# 4. 重启网关
hermes gateway restart
```

## 与 hermes-fry-cards 插件配合

本补丁汉化 Hermes 核心层状态消息，`hermes-fry-cards` 插件汉化飞书卡片层文案（如模型名截断提示、卡片状态等）。两者独立工作，互不冲突。

插件层文案（如 `⇲kimi-k3`、`哈基米` 等）在插件自身代码中配置，不受本补丁影响。
