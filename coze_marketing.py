# -*- coding: utf-8 -*-
"""根据 Ark 决策生成最终可发送跟进内容（允许包含图片 URL）。"""

import json
from typing import Any, Dict, List

from coze_client import call_coze_collect_answer


def build_followup_prompt(user_id: str, ark_decision: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for m in messages[-30:]:
        role = "用户" if m.get("direction") == "in" else "助手"
        txt = (m.get("text") or "").replace("\n", " ").strip()
        if txt:
            lines.append(f"{role}: {txt}")
    convo = "\n".join(lines)

    return (
        "你是一个教育咨询的运营跟进话术生成助手。\n"
        "你将获得 Ark 输出的结构化决策 JSON（画像/标签/意向/分层/是否需要跟进/策略要点等）。\n"
        "你的任务：生成一条可以直接发送给用户的消息（1-2句为主，必要时可带1个问题）。\n"
        "要求：\n"
        "- 必须严格遵循 Ark 的 should_followup / followup_type / strategy\n"
        "- 克制、真实，不夸大效果，不编造优惠\n"
        "- 若需要发送报名海报/图片资源，可在消息中包含图片 URL（png/jpg/jpeg/webp）\n"
        "- 不要输出 JSON，不要输出 markdown，只输出正文\n"
        "\n"
        f"用户ID：{user_id}\n"
        f"Ark 决策 JSON：{json.dumps(ark_decision, ensure_ascii=False)}\n"
        f"最近对话：\n{convo}\n"
    )


def coze_generate_followup_message(user_id: str, ark_decision: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
    prompt = build_followup_prompt(user_id, ark_decision, messages)
    return call_coze_collect_answer(user_id, prompt)