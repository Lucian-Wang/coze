# -*- coding: utf-8 -*-

import os
import logging
import json
import requests

import db
from qiwe_client import inbound_voice_to_cloudurl
from audio_transcode import amr_bytes_to_wav_bytes
from audio_upload_client import upload_audio_bytes
from doubao_asr import asr_url_to_text
from realtime import handle_inbound_text

logger = logging.getLogger("voice_inbound")


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "y", "on")


# 是否保存 ASR/OCR 等“派生文本”（默认开启）
STORE_DERIVED_TEXT = _env_bool("STORE_DERIVED_TEXT", True)

# 是否保存上传后的 wavUrl（写入 messages.extra_json）；默认开启
STORE_AUDIO_URL = _env_bool("STORE_AUDIO_URL", True)

# 是否把“中间产物（wav url）”单独作为一条消息存下来；默认开启
STORE_VOICE_WAV_STAGE_MESSAGE = _env_bool("STORE_VOICE_WAV_STAGE_MESSAGE", True)


def _download_bytes_with_debug(url: str, timeout: int = 60) -> bytes:
    """
    下载语音，并输出调试信息：
    - status_code / content-type / content-length
    - 前 200 字节（repr）
    这样可以判断是不是音频，还是 JSON/HTML 错误页/重定向页。
    """
    r = requests.get(url, timeout=timeout, allow_redirects=True)
    ct = r.headers.get("Content-Type", "")
    cl = r.headers.get("Content-Length", "")
    head = r.content[:200]

    logger.info(
        "voice download debug: status=%s ct=%s len=%s content_length=%s url=%s head=%r",
        r.status_code,
        ct,
        len(r.content),
        cl,
        url,
        head,
    )

    r.raise_for_status()
    return r.content


def handle_inbound_voice(user_id: str, msg_type: int, msg_data: dict, guid: str) -> None:
    cloud_url = inbound_voice_to_cloudurl(msg_type=msg_type, msg_data=msg_data, guid=guid)
    if not cloud_url:
        logger.error("voice cloudUrl empty: user_id=%s msg_type=%s guid=%s", user_id, msg_type, guid)
        return

    voice_time = msg_data.get("voiceTime")
    file_id = msg_data.get("fileId")

    # 1) 入库：企微入站语音元信息（qiwe -> cloudUrl）
    try:
        db.touch_inbound(user_id)
        db.insert_message(
            user_id=user_id,
            direction="in",
            msg_type="voice_meta",
            text="",
            source_type="qiwe_voice",
            ref_id=str(file_id or ""),
            extra_json=json.dumps(
                {"cloudUrl": cloud_url, "voiceTime": voice_time, "fileId": file_id, "msgType": msg_type},
                ensure_ascii=False,
            ),
        )
    except Exception:
        logger.exception("insert voice_meta failed: user_id=%s", user_id)

    try:
        audio_bytes = _download_bytes_with_debug(cloud_url, timeout=60)
    except Exception as e:
        logger.exception("download voice failed: user_id=%s err=%s url=%s", user_id, e, cloud_url)
        return

    try:
        wav_bytes = amr_bytes_to_wav_bytes(audio_bytes)
    except Exception as e:
        logger.exception("transcode ->wav failed: user_id=%s err=%s", user_id, e)
        return

    try:
        wav_url = upload_audio_bytes("voice.wav", wav_bytes, mime="audio/wav")
    except Exception as e:
        logger.exception("upload wav failed: user_id=%s err=%s", user_id, e)
        return

    # 2) 入库：本地转码 + 上传后的 wav 中间产物（用于后台播放/排障）
    if STORE_VOICE_WAV_STAGE_MESSAGE and STORE_AUDIO_URL:
        try:
            db.insert_message(
                user_id=user_id,
                direction="in",
                msg_type="voice_wav",
                text="",
                source_type="local_pipeline",
                ref_id="",
                extra_json=json.dumps({"wavUrl": wav_url}, ensure_ascii=False),
            )
        except Exception:
            logger.exception("insert voice_wav failed: user_id=%s", user_id)

    try:
        text = (asr_url_to_text(wav_url, audio_format="wav") or "").strip()
    except Exception as e:
        logger.exception("ASR failed: user_id=%s err=%s wav_url=%s", user_id, e, wav_url)
        return

    if not text:
        logger.warning("ASR empty text: user_id=%s voice_time=%s", user_id, voice_time)
        return

    # 3) 入库：豆包 ASR 转写文本（最终语义文本）
    # 注意：这里存 msg_type=asr_text，不再额外存 msg_type=text（避免重复）
    if STORE_DERIVED_TEXT:
        try:
            extra = {"wavUrl": wav_url} if STORE_AUDIO_URL else {}
            db.insert_message(
                user_id=user_id,
                direction="in",
                msg_type="asr_text",
                text=text,
                source_type="doubao_asr",
                ref_id="",
                extra_json=json.dumps(extra, ensure_ascii=False) if extra else "",
            )
        except Exception:
            logger.exception("insert asr_text failed: user_id=%s", user_id)

    logger.info("voice->text done: user_id=%s voice_time=%s text_len=%s", user_id, voice_time, len(text))

    # 只做回复，不重复落库 text（语音场景已落库 asr_text）
    handle_inbound_text(user_id, text, guid, skip_store=True)