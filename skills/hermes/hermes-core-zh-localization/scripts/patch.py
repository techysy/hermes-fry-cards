#!/usr/bin/env python3
"""Hermes 核心汉化补丁 — 安装/卸载英文硬编码系统消息的中文替换。

用法:
    python3 patch.py install    # 汉化
    python3 patch.py uninstall  # 恢复英文
"""
import sys
import os
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
RUN_PY = HERMES_HOME / "hermes-agent" / "gateway" / "run.py"
AGENT_FILES = {
    "chat_completion_helpers": HERMES_HOME / "hermes-agent" / "agent" / "chat_completion_helpers.py",
    "codex_runtime": HERMES_HOME / "hermes-agent" / "agent" / "codex_runtime.py",
    "conversation_loop": HERMES_HOME / "hermes-agent" / "agent" / "conversation_loop.py",
    "stream_diag": HERMES_HOME / "hermes-agent" / "agent" / "stream_diag.py",
    "tool_executor": HERMES_HOME / "hermes-agent" / "agent" / "tool_executor.py",
    "conversation_compression": HERMES_HOME / "hermes-agent" / "agent" / "conversation_compression.py",
    "tui_session": HERMES_HOME / "hermes-agent" / "tui_gateway" / "methods_session.py",
}

# ── 汉化映射表: (文件, 英文, 中文) ──────────────────────────────
PATCHES = [
    # ── gateway/run.py ──
    # 网关状态 gerund
    ('run.py', 'return "restarting" if self._restart_requested else "shutting down"',
     'return "正在重启" if self._restart_requested else "正在关闭"'),
    # 网关忙碌消息 (queue_during_drain)
    ('run.py',
     'f"⏳ Gateway {self._status_action_gerund()} — queued for the next turn after it comes back."',
     'f"⏳ 网关{self._status_action_gerund()}中 — 恢复后将在下一轮处理。"'),
    ('run.py',
     'f"⏳ Gateway is {self._status_action_gerund()} and is not accepting another turn right now."',
     'f"⏳ 网关{self._status_action_gerund()}中，暂时无法处理新任务。"'),
    # 网关 draining
    ('run.py',
     'f"⏳ Gateway is {self._status_action_gerund()} and is not accepting new work right now."',
     'f"⏳ 网关{self._status_action_gerund()}中，暂时无法处理新任务。"'),
    # 忙碌消息 — steer/redirect/queue/interrupt
    ('run.py',
     'f"⏩ Steered into current run{status_detail}. "\n                f"Your message arrives after the next tool call."',
     'f"⏩ 已引导至当前运行{status_detail}。"\n                f"你的消息将在下一次工具调用后到达。"'),
    ('run.py',
     'f"↪ Redirected current run{status_detail}. "\n                f"I\'ll adjust using your correction."',
     'f"↪ 已重定向当前运行{status_detail}。"\n                f"我会根据你的纠正进行调整。"'),
    ('run.py',
     'f"⏳ Subagent working{status_detail} — your message is queued for "\n                f"when it finishes (use /stop to cancel everything)."',
     'f"⏳ 子代理运行中{status_detail} — 你的消息已排队等待"\n                f"完成（使用 /stop 取消所有任务）。"'),
    ('run.py',
     'f"⏳ Compressing context{status_detail} — your message is queued for "\n                f"when it finishes (use /stop to cancel everything)."',
     'f"⏳ 压缩上下文{status_detail} — 你的消息已排队等待"\n                f"完成（使用 /stop 取消所有任务）。"'),
    ('run.py',
     'f"⏳ Queued for the next turn{status_detail}. "\n                f"I\'ll respond once the current task finishes."',
     'f"⏳ 已排队等待下一轮{status_detail}。"\n                f"当前任务完成后我会回复你。"'),
    ('run.py',
     'f"⚡ Interrupting current task{status_detail}. "\n                f"I\'ll respond to your message shortly."',
     'f"⚡ 中断当前任务{status_detail}。"\n                f"我很快会回复你的消息。"'),
    # 迭代计数
    ('run.py',
     'f"iteration {_a[\'api_call_count\']}/{_a[\'max_iterations\']}"',
     'f"迭代 {_a[\'api_call_count\']}/{_a[\'max_iterations\']}"'),
    # heartbeat
    ('run.py',
     'f"⏳ Working — {_elapsed_mins} min{_status_detail}"',
     'f"⏳ 运行中 — {_elapsed_mins} 分钟{_status_detail}"'),
    # Queued for the next turn
    ('run.py', '"Queued for the next turn."', '"已排队等待下一轮。"'),
    ('run.py', 'f"Queued for the next turn. ({depth} queued)"', 'f"已排队等待下一轮。({depth} 条排队中)"'),
    # Cron interrupted
    ('run.py',
     'f"⚠️ Cron job \'{job.get(\'name\') or job_id}\' was interrupted — "\n                f"the gateway is {action} and killed the run before it "\n                "finished. No result was produced for this run."',
     'f"⚠️ 定时任务 \'{job.get(\'name\') or job_id}\' 被中断 — "\n                f"网关正在{action}，在任务完成前终止了运行。本次运行未产生结果。"'),
    # Steer messages
    ('run.py', 'f"⚠️ Steer failed: {exc}"', 'f"⚠️ 引导失败: {exc}"'),
    ('run.py',
     'f"⏩ Steer queued — arrives after the next tool call: \'{preview}\'"',
     'f"⏩ 引导已排队 — 将在下一次工具调用后到达: \'{preview}\'"'),
    ('run.py', '"Steer rejected (empty payload)."', '"引导被拒绝（空内容）。"'),
    # Agent is running
    ('run.py', '"model": "Agent is running — wait or /stop first, then switch models."',
     '"model": "代理正在运行 — 请等待或先 /stop，然后切换模型。"'),
    ('run.py',
     '"codex-runtime": ("Agent is running — wait or /stop first, then "\n                          "change runtime.")',
     '"codex-runtime": ("代理正在运行 — 请等待或先 /stop，然后 "\n                          "切换运行时。")'),
    ('run.py', '"moa": "Agent is running — wait or /stop first, then run /moa."',
     '"moa": "代理正在运行 — 请等待或先 /stop，然后运行 /moa。"'),
    ('run.py',
     'f"⏳ Agent is running — `/{name}` can\'t run "\n            f"mid-turn. Wait for the current response or `/stop` first."',
     'f"⏳ 代理正在运行 — `/{name}` 无法在运行中执行 "\n            f"请等待当前响应完成或先 /stop。"'),
    ('run.py', '"Agent is running — use /goal status / pause / clear / wait mid-run, or /stop before setting a new goal."',
     '"代理正在运行 — 使用 /goal status / pause / clear / wait 在运行中操作，或先 /stop 再设置新目标。"'),
    ('run.py', '"Agent is running — use /loop status / pause / stop mid-run, or /stop before setting a new loop."',
     '"代理正在运行 — 使用 /loop status / pause / stop 在运行中操作，或先 /stop 再设置新循环。"'),
    # Gateway restart notification
    ('run.py', '"♻ Gateway restarted successfully. Your session continues."',
     '"♻ 网关重启成功，会话继续。"'),
    # Drain
    ('run.py',
     '"⏳ This agent is draining for a maintenance action and isn\'t "\n                "accepting new turns right now. It\'ll be back in a moment — "\n                "please resend shortly."',
     '"⏳ 此代理正在维护中，暂时无法处理新任务。"\n                "很快会恢复 — 请稍后重新发送。"'),
    # Another turn running
    ('run.py',
     '"⏳ Another turn is still running on this session. To "\n                    "protect the transcript, this message was not processed. "\n                    "Wait for the active turn to finish, then resend it."',
     '"⏳ 此会话仍有其他任务在运行。为保护对话记录，此消息未被处理。"\n                    "请等待当前任务完成后重新发送。"'),
    # Session errors
    ('run.py',
     '"⚠️ Session storage was temporarily unavailable, so this "\n                    "turn was stopped to protect your conversation history. "\n                    "Please check available disk space, then send your "\n                    "message again."',
     '"⚠️ 会话存储暂时不可用，为保护对话历史已停止本轮处理。"\n                    "请检查磁盘空间，然后重新发送消息。"'),
    ('run.py',
     '"⚠️ Session storage was temporarily unavailable, so this "\n                "turn was stopped to protect your conversation history. "\n                "Your message should already be saved — please send it "\n                "again in a moment."',
     '"⚠️ 会话存储暂时不可用，为保护对话历史已停止本轮处理。"\n                "你的消息应该已保存 — 请稍后重新发送。"'),
    ('run.py',
     '"⚠️ Session too large for the model\'s context window.\\n"\n                "Use /compact to compress the conversation, or "\n                "/reset to start fresh."',
     '"⚠️ 会话内容超出模型上下文窗口限制。\\n"\n                "使用 /compact 压缩对话，或 /reset 重新开始。"'),
    ('run.py',
     'f"The request failed: {str(error_detail)[:300]}\\n"\n            "Try again or use /reset to start a fresh session."',
     'f"请求失败: {str(error_detail)[:300]}\\n"\n            "请重试或使用 /reset 开始新会话。"'),
    ('run.py',
     '"⚠️ Your message was interrupted before processing started "\n                "(likely by a recent /stop). Please send it again."',
     '"⚠️ 你的消息在处理开始前被中断（可能是之前的 /stop 导致的）。请重新发送。"'),
    ('run.py', 'f"⚠️ Processing stopped: {str(err)[:200]}. Try again."',
     'f"⚠️ 处理已停止: {str(err)[:200]}。请重试。"'),
    ('run.py',
     '"⚠️ Processing completed but no response was generated. "\n            "This may be a transient error — try sending your message again."',
     '"⚠️ 处理已完成但未生成响应。"\n            "可能是临时错误 — 请重新发送消息。"'),
    ('run.py',
     '"⚠️ Your message wasn\'t processed (the previous turn was still "\n            "being cleaned up). Please send it again."',
     '"⚠️ 你的消息未被处理（上一轮仍在清理中）。请重新发送。"'),
    # Provider errors
    ('run.py',
     '"⚠️ Provider authentication failed. Check the configured credentials; "\n            "raw provider details are in the gateway logs."',
     '"⚠️ 提供商认证失败。请检查配置的凭据；"\n            "原始错误详情请查看网关日志。"'),
    ('run.py',
     '"⚠️ The model provider rejected the request. I kept the raw provider "\n            "error out of chat; check gateway logs for details or try rephrasing."',
     '"⚠️ 模型提供商拒绝了请求。已将原始错误信息"\n            "从聊天中隐藏；请查看网关日志获取详情或尝试重新表述。"'),
    ('run.py', '"⏱️ The model provider is rate-limiting requests. Please wait a moment and try again."',
     '"⏱️ 模型提供商正在限流。请稍等片刻后重试。"'),
    ('run.py',
     '"⚠️ The model server is not responding — it looks like the configured "\n            "model endpoint is not running or is unreachable."',
     '"⚠️ 模型服务器未响应 — 看起来配置的"\n            "模型端点未运行或不可达。"'),
    ('run.py',
     '"⚠️ The model provider failed after retries. I kept raw provider details "\n        "out of chat; check gateway logs for diagnostics."',
     '"⚠️ 模型提供商在重试后仍然失败。已将原始错误详情"\n        "从聊天中隐藏；请查看网关日志获取诊断信息。"'),
    ('run.py', 'f"⚠️ Provider authentication failed: {exc}"',
     'f"⚠️ 提供商认证失败: {exc}"'),
    # Update messages
    ('run.py', '"✅ Hermes update finished."', '"✅ Hermes 更新完成。"'),
    ('run.py', '"❌ Hermes update failed (exit code {}).".format(exit_code)',
     '"❌ Hermes 更新失败（退出码 {}）。".format(exit_code)'),
    ('run.py', 'f"✅ Hermes update finished.\\n\\n```\\n{output}\\n```"',
     'f"✅ Hermes 更新完成。\\n\\n```\\n{output}\\n```"'),
    ('run.py', 'f"❌ Hermes update failed.\\n\\n```\\n{output}\\n```"',
     'f"❌ Hermes 更新失败。\\n\\n```\\n{output}\\n```"'),
    ('run.py', '"✅ Hermes update finished successfully."', '"✅ Hermes 更新成功完成。"'),
    ('run.py', '"❌ Hermes update failed. Check the gateway logs or run `hermes update` manually for details."',
     '"❌ Hermes 更新失败。请查看网关日志或运行 `hermes update` 获取详情。"'),
    # Context compression
    ('run.py',
     '"⚠️ Context compression timed out "\n                                                f"after {_hyg_timeout_seconds:.1f}s "\n                                                "with no output from the summary model. "\n                                                "No messages were dropped — continuing without "\n                                                "compression. Run /compress to retry, /reset for "\n                                                "a clean session, or check your "\n                                                "auxiliary.compression model configuration."',
     '"⚠️ 上下文压缩超时 "\n                                                f"（{_hyg_timeout_seconds:.1f}s 内摘要模型无输出）。"\n                                                "未丢失任何消息 — 继续不压缩运行。"\n                                                "运行 /compress 重试，/reset 开始新会话，"\n                                                "或检查 auxiliary.compression 模型配置。"'),
    ('run.py',
     '"⚠️ Context compression aborted "\n                                            f"({_err}). No messages were dropped — "\n                                            "conversation is unchanged. Run /compress "\n                                            "to retry, /reset for a clean session, or "\n                                            "check your auxiliary.compression model "\n                                            "configuration."',
     '"⚠️ 上下文压缩已中止 "\n                                            f"（{_err}）。未丢失任何消息 — "\n                                            "对话内容未改变。运行 /compress "\n                                            "重试，/reset 开始新会话，"\n                                            "或检查 auxiliary.compression 模型 "\n                                            "配置。"'),

    # ── agent files ──
    ('chat_completion_helpers', 'agent._touch_activity("receiving stream response")',
     'agent._touch_activity("正在接收流式响应")'),
    ('codex_runtime', 'agent._touch_activity("receiving stream response")',
     'agent._touch_activity("正在接收流式响应")'),
    ('conversation_loop', 'agent._touch_activity(f"starting API call #{api_call_count}")',
     'agent._touch_activity(f"开始 API 调用 #{api_call_count}")'),
    ('conversation_loop', 'agent._touch_activity(f"API call #{api_call_count} completed")',
     'agent._touch_activity(f"API 调用 #{api_call_count} 已完成")'),
    ('conversation_loop', 'agent._touch_activity(f"tool results posted, continuing iteration #{api_call_count}")',
     'agent._touch_activity(f"工具结果已提交，继续迭代 #{api_call_count}")'),
    ('stream_diag',
     'f"stream retry {attempt}/{max_attempts} "\n            f"after {type(error).__name__}"',
     'f"流式重试 {attempt}/{max_attempts} "\n            f"（{type(error).__name__}）"'),
    ('tool_executor', 'agent._touch_activity(f"executing tool: {function_name}")',
     'agent._touch_activity(f"执行工具: {function_name}")'),
    ('tool_executor', 'agent._touch_activity(f"executing {num_tools} tools concurrently: {tool_names_str}")',
     'agent._touch_activity(f"并发执行 {num_tools} 个工具: {tool_names_str}")'),
    ('tool_executor', 'agent._touch_activity(f"tool completed: {name} ({tool_duration:.1f}s){_status_suffix}")',
     'agent._touch_activity(f"工具完成: {name} ({tool_duration:.1f}s){_status_suffix}")'),
    ('tool_executor', 'agent._touch_activity(f"tool completed: {function_name} ({tool_duration:.1f}s){_status_suffix}")',
     'agent._touch_activity(f"工具完成: {function_name} ({tool_duration:.1f}s){_status_suffix}")'),
    # Context compression
    ('conversation_compression', 'COMPACTION_STATUS_MARKER = "Compacting context"',
     'COMPACTION_STATUS_MARKER = "压缩上下文"'),
    ('conversation_compression', 'COMPACTION_DONE_STATUS = "✓ Context compaction complete — continuing turn..."',
     'COMPACTION_DONE_STATUS = "✓ 上下文压缩完成，继续处理..."'),
    ('tui_session', 'f"⠋ compressing {before_count} messages "',
     'f"⠋ 压缩中 {before_count} 条消息 "'),
]


def _apply_patches(mode: str) -> int:
    """Apply or revert patches. Returns count of changes made."""
    file_map = {"run.py": RUN_PY}
    file_map.update(AGENT_FILES)

    # Build reverse patches for uninstall
    patches = PATCHES
    if mode == "uninstall":
        patches = [(f, zh, en) for f, en, zh in PATCHES]

    changed = 0
    for file_key, old, new in patches:
        path = file_map.get(file_key)
        if not path or not path.exists():
            print(f"  ⚠️  跳过 {file_key}: 文件不存在")
            continue
        content = path.read_text(encoding="utf-8")
        if old in content:
            content = content.replace(old, new, 1)
            path.write_text(content, encoding="utf-8")
            changed += 1
            print(f"  ✅ {file_key}")
        else:
            print(f"  ⏭️  {file_key}: 未找到匹配（可能已应用或版本不同）")
    return changed


def _check_language_config() -> None:
    """检查 config.yaml 的 language 设置，非中文时拒绝安装.

    补丁将系统消息静态替换为中文，若 config.yaml 中 language 设置为
    英文（en）或其他非中文值，两者会冲突。此时应先卸载补丁或修改语言设置。
    """
    config_path = HERMES_HOME / "config.yaml"
    if not config_path.exists():
        return  # 无配置文件时默认为中文环境，允许安装

    try:
        content = config_path.read_text(encoding="utf-8")
        # 匹配 language: xxx 行（忽略注释）
        import re

        m = re.search(r"^\s*language:\s*(\S+)", content, re.MULTILINE)
        if m:
            lang = m.group(1).strip().lower()
            if lang and lang not in ("zh", "zh-cn", "zh-tw", "zh-hans", "zh-hant", "auto"):
                print(f"⚠️  config.yaml 中 language 当前为 '{m.group(1).strip()}'，非中文设置。")
                print("   补丁会将系统消息汉化，与语言设置冲突。")
                print("   如需汉化，请先在 config.yaml 中设置 language: zh")
                print("   或运行: hermes config set language zh")
                print("   若需恢复英文，请运行: python3 patch.py uninstall")
                sys.exit(1)
    except Exception as e:
        print(f"⚠️  读取 config.yaml 失败: {e}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("install", "uninstall"):
        print("用法: python3 patch.py install|uninstall")
        sys.exit(1)

    mode = sys.argv[1]
    label = "汉化" if mode == "install" else "恢复英文"
    print(f"🔧 Hermes 核心{label}补丁 — {mode}")
    print(f"   HERMES_HOME: {HERMES_HOME}")

    if mode == "install":
        _check_language_config()

    if not RUN_PY.exists():
        print(f"❌ 未找到 {RUN_PY}")
        sys.exit(1)

    count = _apply_patches(mode)
    print(f"\n{'✅' if count else '⏭️'} 共处理 {count} 处替换")
    print("💡 请运行 hermes gateway restart 使改动生效")


if __name__ == "__main__":
    main()
