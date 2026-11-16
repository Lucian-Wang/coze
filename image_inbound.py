# -*- coding: utf-8 -*-
"""
图片入站处理：
1) qiwe 下载 -> cloudUrl
2) Ark Vision OCR -> vision_text（脱敏摘要）
3) 把 vision_text 发给 Coze -> 生成最终回复
4) 回复发回 qiwe（支持 Coze 回复内图片 URL 转图片发送）
"""

import os
import json
import logging
from typing import Any, Dict

import db
from ark_client import call_ark_vision, extract_output_text
from coze_client import call_coze_collect_answer
from qiwe_client import inbound_image_to_cloudurl, send_reply_to_qiwe

logger = logging.getLogger("image_inbound")


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "y", "on")


STORE_DERIVED_TEXT = _env_bool("STORE_DERIVED_TEXT", True)


def build_coze_prompt_for_image(vision_text: str) -> str:
    return (
        "你是教育咨询老师/助教，用微信私聊的自然语气回复用户。\n"
        "已拿到图片的【脱敏摘要】如下（可能有识别误差）。\n"
        "要求：\n"
        "- 不要使用固定模板标题（不要“我从图片中读到/建议/1)2)”）。\n"
        "- 不要夸张推销/制造紧迫感。\n"
        "- 用“看起来像/如果没看错”这类措辞避免过度确定。\n"
        "- 回复控制在 1-3 句话；最后如果需要就问 1 个问题确认用户需求。\n"
        "\n"
        f"【脱敏摘要】\n{vision_text}\n"
        "\n"
        "请输出你要发给用户的回复："
    )


def handle_inbound_image(user_id: str, msg_type: int, msg_data: Dict[str, Any], guid: str) -> None:
    """
    注意：guid 必传
    - inbound_image_to_cloudurl 需要 guid 才能去 qiwe 拉取云文件
    - send_reply_to_qiwe 需要 guid 才能把消息回发到同一个企业微信实例
    """
    db.touch_inbound(user_id)

    cloud_url = inbound_image_to_cloudurl(msg_type, msg_data, guid)
    file_id = msg_data.get("fileId")

    # 1) 入库：图片元信息
    try:
        db.insert_message(
            user_id=user_id,
            direction="in",
            msg_type="image_meta",
            text="",
            source_type="qiwe_image",
            ref_id=str(file_id or ""),
            extra_json=json.dumps({"cloudUrl": cloud_url, "msgType": msg_type, "fileId": file_id}, ensure_ascii=False),
        )
    except Exception:
        logger.exception("insert image_meta failed: user_id=%s", user_id)

    if not cloud_url:
        reply = "我收到了图片，但暂时无法获取可用的图片链接。你可以重发一下或描述下图片内容吗？"
        send_reply_to_qiwe(user_id, reply, guid)
        db.touch_outbound(user_id)
        db.insert_message(user_id, "out", "text", reply, source_type="coze_reply", ref_id="", extra_json="")
        return

    ocr_prompt = (
        "请对这张图片进行OCR与关键信息提取，但必须遵守隐私与安全规则：\n"
        "1) 不要逐字/逐行完整输出图片原文。\n"
        "2) 只输出“摘要 + 关键信息要点”。\n"
        "3) 若图片中包含任何敏感信息（包括但不限于手机号、身份证号、银行卡号、具体家庭住址、微信号/QQ号、邮箱、车牌、订单号、学号工号等），"
        "请用“***”替换敏感字段，仅保留必要的非敏感上下文。\n"
        "4) 如果图片主要内容是题目/知识点/表格，请只提取题干关键条件与问题，不要复刻整页文本。\n"
        "\n"
        "输出格式（严格遵守）：\n"
        "【图片内容摘要】用1-3句话概括图片在讲什么。\n"
        "【关键信息】列出3-8条要点（已脱敏）。\n"
        "【可能的用户意图】用1句话推测用户想问什么（不确定就写“不确定”）。\n"
        "如果无法识别，请说明原因（如模糊/遮挡/过暗）。\n"
    )

    try:
        ark_raw = call_ark_vision(cloud_url, ocr_prompt)
        vision_text = (extract_output_text(ark_raw) or "").strip()
        if not vision_text:
            vision_text = str(ark_raw)
    except Exception as e:
        logger.warning("Ark OCR 失败：%s", str(e), exc_info=True)
        vision_text = f"(图片链接：{cloud_url})\n(视觉识别失败：请用户补充文字描述/重新发送更清晰图片)"

    max_chars = int(os.environ.get("ARK_MAX_CHARS", "2500"))
    if len(vision_text) > max_chars:
        vision_text = vision_text[:max_chars] + "\n...(截断)"

    # 2) 入库：OCR/视觉摘要（派生文本）
    if STORE_DERIVED_TEXT:
        try:
            db.insert_message(
                user_id=user_id,
                direction="in",
                msg_type="image_ocr",
                text=vision_text,
                source_type="ark_ocr",
                ref_id="",
                extra_json="",
            )
        except Exception:
            logger.exception("insert image_ocr failed: user_id=%s", user_id)

    coze_prompt = build_coze_prompt_for_image(vision_text)

    reply = call_coze_collect_answer(
        user_id,
        coze_prompt,
        system_text=(
            "你是教育咨询老师/助教，用微信私聊自然语气回复。\n"
            "禁止：宝子/亲/家人们/表情；禁止 1)2)/【标题】模板；禁止复述图片内容清单。\n"
            "最多2句话；若需澄清，只问1个关键问题。\n"
        ),
    )

    if reply:
        send_reply_to_qiwe(user_id, reply, guid)
        db.touch_outbound(user_id)
        db.insert_message(user_id, "out", "text", reply, source_type="coze_reply", ref_id="", extra_json="")