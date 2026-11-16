# -*- coding: utf-8 -*-
"""
realtime.py：处理入站文本 -> 调 Coze -> 回复企微 -> 落库

本次版本支持：
- handle_inbound_text(skip_store=True)：用于语音转写/OCR 派生文本触发回复，避免重复写入 msg_type=text
"""

import logging

import db
from coze_client import call_coze_collect_answer
from qiwe_client import send_reply_to_qiwe

logger = logging.getLogger("realtime")

# 新用户阈值：消息数少于等于此值视为新用户
NEW_USER_MESSAGE_THRESHOLD = 2


def build_prompt(user_id: str, user_text: str) -> str:
    # 你也可以替换为更复杂的系统 prompt，这里保持简洁
    return (
        "你是一位教育咨询老师/助教，用微信私聊的自然语气回复。\n"
        "输出长度：1-3 句话为主；需要追问时用一句问题收尾。\n"
        f"用户ID：{user_id}\n"
        f"用户消息：{user_text}\n"
        "请直接给出要发给用户的回复文本："
    )


def handle_inbound_text(user_id: str, text: str, guid: str, skip_store: bool = False) -> None:
    """
    处理文本入站：
    - 默认会把用户原文写入 messages(text)，source_type=qiwe_text
    - 当 skip_store=True 时，不再落库 msg_type=text（用于语音/OCR 已在上游落库 asr_text/image_ocr）
    """
    t = (text or "").strip()
    if not t:
        return

    if not skip_store:
        try:
            db.touch_inbound(user_id)
            db.insert_message(user_id, "in", "text", t, source_type="qiwe_text", ref_id="", extra_json="")
        except Exception:
            logger.exception("insert inbound text failed: user_id=%s", user_id)

    prompt = build_prompt(user_id, t)
    reply = call_coze_collect_answer(user_id, prompt)

    if reply:
        # 判断是否是新用户（消息数 <= 阈值）
        msg_count = db.get_user_message_count(user_id)
        is_new_user = msg_count <= NEW_USER_MESSAGE_THRESHOLD
        if is_new_user:
            logger.info("new user detected: user_id=%s, msg_count=%s", user_id, msg_count)
        
        send_reply_to_qiwe(user_id, reply, guid, is_new_user=is_new_user)
        try:
            db.touch_outbound(user_id)
            db.insert_message(user_id, "out", "text", reply, source_type="coze_reply", ref_id="", extra_json="")
        except Exception:
            logger.exception("insert outbound text failed: user_id=%s", user_id)