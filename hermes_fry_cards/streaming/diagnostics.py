"""Compact logging helpers for streaming CardKit diagnostics."""

from __future__ import annotations

import re
from typing import Any

from ..feishu import FeishuAPIError
from .segments import Segment, SegmentType

_MISSING_ELEMENT_RE = re.compile(r"not find elementID\s*:\s*([A-Za-z0-9_-]+)")
_SUMMARY_LIMIT = 8


def compact_ids(ids: list[str] | set[str]) -> str:
    ordered = sorted(ids) if isinstance(ids, set) else ids
    if len(ordered) <= _SUMMARY_LIMIT:
        return ",".join(ordered) or "-"
    visible = ",".join(ordered[:_SUMMARY_LIMIT])
    return f"{visible},...(+{len(ordered) - _SUMMARY_LIMIT})"


def summarize_actions(actions: list[dict[str, Any]]) -> str:
    add_ids: list[str] = []
    partial_ids: list[str] = []
    for action in actions:
        action_name = str(action.get("action", "unknown"))
        raw_params = action.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        if action_name == "add_elements":
            elements = params.get("elements")
            if isinstance(elements, list):
                add_ids.extend(
                    element_id
                    for element in elements
                    if isinstance(element, dict)
                    and isinstance(element_id := element.get("element_id"), str)
                )
        elif action_name == "partial_update_element":
            element_id = params.get("element_id")
            if isinstance(element_id, str):
                partial_ids.append(element_id)
    return f"add=[{compact_ids(add_ids)}] partial=[{compact_ids(partial_ids)}]"


def extract_missing_element_id(error: FeishuAPIError) -> str:
    match = _MISSING_ELEMENT_RE.search(str(error))
    return match.group(1) if match else ""


def segment_state_for_log(segments: list[Segment], el_id: str) -> str:
    if not el_id:
        return "-"
    for i, seg in enumerate(segments):
        if seg.el_id != el_id:
            continue
        state = f"{i}:{seg.el_id}/{seg.type.value} c={int(seg.created)} d={int(seg.dirty)} est={seg.element_estimate}"
        if seg.type == SegmentType.TOOL:
            state += f" off={seg.tool_offset}:{seg.tool_end_offset}"
        return state
    return "not-in-state"
