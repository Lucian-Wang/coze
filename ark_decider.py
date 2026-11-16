# -*- coding: utf-8 -*-
"""
Ark 决策器：
- 将对话 messages 组织成 prompt
- 调用 Ark LLM 生成 JSON 决策（intent/tier/tags/profile/should_followup 等）
- 解析并返回结构化结果

修复点：
1) 决策模型名不再写死 "default"，从环境变量读取（默认 doubao-seed-2-0-pro-260215）
2) call_ark_text 返回的是 dict，需要 extract_output_text 提取文本，再 json.loads
3) 增加日志：打印 Ark 实际看到的 messages（最后 N 条），用于排查“明明说了初三却没识别”
4) 过滤掉元信息类消息（voice_meta/image_meta），只把用户真正“说的话”(text/asr_text/image_ocr) 给 Ark
"""

import os
import json
import logging
from typing import Any, Dict, List, Tuple

from ark_client import call_ark_text, extract_output_text, ARK_DECISION_MODEL as _DEFAULT_DECISION_MODEL

logger = logging.getLogger("ark_decider")


def _brief_messages(messages: List[Dict[str, Any]], n: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in (messages or [])[-n:]:
        out.append(
            {
                "direction": m.get("direction"),
                "msg_type": m.get("msg_type"),
                "created_at": m.get("created_at"),
                "text": (m.get("text") or "")[:160],
            }
        )
    return out


def _is_user_text_like(m: Dict[str, Any]) -> bool:
    """
    只把“用户真实表达”交给 Ark：
    - text: 用户手打文本
    - asr_text: 语音识别结果
    - image_ocr: 图片识别摘要（脱敏）
    """
    if (m.get("direction") or "").strip() != "in":
        return False
    t = (m.get("msg_type") or "").strip()
    return t in ("text", "asr_text", "image_ocr")


def _messages_to_prompt(messages: List[Dict[str, Any]]) -> str:
    """将消息列表转换为 Ark prompt 格式。
    
    只包含用户真实表达（text/asr_text/image_ocr）和助手回复，
    过滤掉元信息类消息（voice_meta/image_meta）。
    """
    lines: List[str] = []
    for m in messages or []:
        direction = (m.get("direction") or "").strip()
        msg_type = (m.get("msg_type") or "").strip()
        
        # 过滤掉元信息类消息
        if msg_type in ("voice_meta", "image_meta"):
            continue
            
        # 用户消息：只保留真实表达
        if direction == "in":
            if msg_type not in ("text", "asr_text", "image_ocr"):
                continue
            role = "用户"
        else:
            # 助手消息：保留所有回复
            role = "助手"

        txt = (m.get("text") or "").strip()
        if not txt:
            continue
        lines.append(f"{role}：{txt}")
    return "\n".join(lines).strip()


def _build_prompt(
    user_id: str,
    messages: List[Dict[str, Any]],
    is_quiet_hours: bool,
    quiet_hours_desc: str,
    daily_limit: int,
    followup_count_today: int,
) -> str:
    convo = _messages_to_prompt(messages)

    prompt = f"""
你是一个电销/私域运营的“沉默跟进决策器”。你需要基于最近对话，输出严格 JSON。

用户ID：{user_id}
是否免打扰时段：{str(is_quiet_hours)}
免打扰说明：{quiet_hours_desc}
今日已触达：{followup_count_today}/{daily_limit}

【对话记录开始】
{convo}
【对话记录结束】

请输出一个 JSON 对象，字段包括：
- has_purchased: boolean
- purchase_confidence: 0~1
- intent_level: "high"|"medium"|"low"|"unknown"
- tier: "A"|"B"|"C"
- should_followup: boolean
- followup_type: "nudge"|"education"|"closing"|"none"
- reason: string
- cooldown_minutes: integer
- tags: string[]
- profile: object (包含 grade/weak_subjects/key_needs_summary 等)
- strategy: object (goal/key_points/questions/tone)

要求：
1) 必须是合法 JSON，不能包含任何额外文本。
2) profile.grade 若用户明确提到“初三/初二/初一”，请识别并填入（例如 "初三"）。
3) profile.weak_subjects 若用户提到“英语/数学/物理/化学”等薄弱科目，请识别填入数组。
""".strip()

    return prompt


def _strip_code_fence(s: str) -> str:
    t = (s or "").strip()
    if t.startswith("```"):
        t = t.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    return t


def ark_decide(
    user_id: str,
    messages: List[Dict[str, Any]],
    is_quiet_hours: bool,
    quiet_hours_desc: str,
    daily_limit: int,
    followup_count_today: int,
) -> Tuple[Dict[str, Any], str, str]:
    """
    返回：
- ark_obj: 解析后的 JSON dict
- ark_text: 提取出来的模型文本（用于调试）
- ark_raw: 原始响应（这里用 json 字符串保存，便于排查）
    """
    prompt = _build_prompt(
        user_id=user_id,
        messages=messages,
        is_quiet_hours=is_quiet_hours,
        quiet_hours_desc=quiet_hours_desc,
        daily_limit=daily_limit,
        followup_count_today=followup_count_today,
    )

    logger.info("ark_decide input messages (last %s): %s", 8, _brief_messages(messages, 8))

    model = (os.environ.get("ARK_DECISION_MODEL") or _DEFAULT_DECISION_MODEL or "").strip()
    if not model:
        # 最后兜底，确保不会再出现 "default"
        model = "doubao-seed-2-0-pro-260215"

    resp_obj = call_ark_text(prompt, model=model)  # dict
    ark_text = extract_output_text(resp_obj) or ""
    ark_text = _strip_code_fence(ark_text)

    # 把原始响应保存为 JSON 字符串（方便落库/排查）
    try:
        ark_raw = json.dumps(resp_obj, ensure_ascii=False)
    except Exception:
        ark_raw = str(resp_obj)

    if not ark_text.strip():
        raise RuntimeError("Ark response has empty output_text/content (无法提取模型文本输出)")

    try:
        ark_obj = json.loads(ark_text)
    except Exception as e:
        # 这里把文本打出来方便排查（限制长度）
        logger.error("Ark output is not valid JSON. text=%s", ark_text[:1200])
        raise e

    return ark_obj, ark_text, ark_raw