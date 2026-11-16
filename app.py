# -*- coding: utf-8 -*-
"""
主应用：
- webhook 回调：快速 ACK，把耗时任务丢线程池
- 文本：Coze 实时回复（可含图片 URL）
- 图片：qiwe 下载→Ark OCR→Coze 回复（可含图�� URL）
- 语音：qiwe 下载云文件(SILK/AMR等) → 本地转码 WAV → 上传到自建接口 → ASR → 文本 → Coze 回复
- 定时任务：沉默运营（Ark 判定 + Coze 生成）
- 管理后台：只读 + 配置 + 触发重新评估（禁止手工发送）
- webhook 鉴权：query token（/qiweapi/callback?token=xxxx）

关键修复：
- 所有需要 guid 的调用（图片下载/语音下载/消息发送）优先使用回调消息里的 guid（msg_item.guid）
  而不是写死或只读环境变量 QIWE_API_GUID
"""

import os
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict
from collections import defaultdict

# 从 .env 文件加载环境变量（Windows/Linux 都支持）
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify
from flask_cors import CORS

import db
from qiwe_client import qiwe_client
from realtime import handle_inbound_text
from image_inbound import handle_inbound_image
from voice_inbound import handle_inbound_voice
from marketing import should_opt_out_by_text
from scheduler import start_scheduler
from admin import register_admin_routes
from webhook_auth import is_authorized_webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("app")

app = Flask(__name__)
CORS(app)

WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "5001"))
QIWEAPI_CALLBACK_PATH = os.environ.get("QIWEAPI_CALLBACK_PATH", "/qiweapi/callback")

TASK_THREADPOOL_SIZE = int(os.environ.get("TASK_THREADPOOL_SIZE", "8"))
_executor = ThreadPoolExecutor(max_workers=TASK_THREADPOOL_SIZE)

_user_locks = defaultdict(threading.Lock)


def user_lock(user_key: str) -> threading.Lock:
    return _user_locks[user_key]


def init_app_once() -> None:
    try:
        db.init_db()
    except Exception as e:
        logger.exception("db.init_db failed: %s", e)
        raise

    try:
        start_scheduler()
    except Exception as e:
        logger.exception("start_scheduler failed: %s", e)

    try:
        _ = qiwe_client()
    except Exception as e:
        logger.exception("qiwe_client init failed: %s", e)
        raise


init_app_once()


@app.route("/health", methods=["GET"])
def health():
    # 使用 db.now_iso() 保证与数据库时间/时区一致
    return jsonify({"status": "ok", "timestamp": db.now_iso()})


register_admin_routes(app)


@app.route(QIWEAPI_CALLBACK_PATH, methods=["POST"])
def qiweapi_callback():
    if not is_authorized_webhook():
        return jsonify({"code": 401, "msg": "Unauthorized", "data": []}), 401

    payload = request.get_json(force=True, silent=True) or {}
    msg_list = payload.get("data", [])
    if not msg_list:
        return jsonify({"code": 0, "msg": "成功", "data": []})

    response_data = []

    for msg_item in msg_list:
        if msg_item.get("cmd") != 15000:
            continue
        if msg_item.get("fromRoomId", 0) != 0:
            continue

        sender_id = msg_item.get("senderId")
        if not sender_id:
            continue
        sender_id = str(sender_id)

        guid = (msg_item.get("guid") or "").strip()
        if not guid:
            # 按现网数据，guid 应该总会有；没有则直接跳过，避免后续下载/发送失败
            logger.warning("忽略消息：缺少 guid user_id=%s", sender_id)
            continue

        msg_type = msg_item.get("msgType")
        msg_data: Dict[str, Any] = msg_item.get("msgData", {}) or {}

        def _task(sender_id_=sender_id, guid_=guid, msg_type_=msg_type, msg_data_=msg_data):
            with user_lock(sender_id_):
                try:
                    if msg_type_ in (0, 2):
                        text = (msg_data_.get("content") or "").strip()
                        if text:
                            logger.info("收到文本：user_id=%s len=%s", sender_id_, len(text))
                            if should_opt_out_by_text(text):
                                db.set_user_opt_out(sender_id_)
                            # 关键修复：传 guid（回复企微需要 guid）
                            handle_inbound_text(sender_id_, text, guid_)

                    elif msg_type_ in (14, 101):
                        logger.info("收到图片：user_id=%s msgType=%s", sender_id_, msg_type_)
                        # 关键：传 guid（下载/发送都用这个 guid）
                        handle_inbound_image(sender_id_, msg_type_, msg_data_, guid_)

                    elif msg_type_ in (16,):
                        logger.info("收到语音：user_id=%s msgType=%s", sender_id_, msg_type_)
                        # 关键：传 guid（下载/发送都用这个 guid）
                        handle_inbound_voice(sender_id_, msg_type_, msg_data_, guid_)

                    else:
                        logger.info("忽略消息：user_id=%s msgType=%s", sender_id_, msg_type_)

                except Exception as e:
                    logger.error("处理消息失败：user_id=%s err=%s", sender_id_, str(e), exc_info=True)

        _executor.submit(_task)

        response_data.append(
            {
                "guid": guid,
                "cmd": 15000,
                "msgServerId": 0,
                "timestamp": int(datetime.now().timestamp()),
            }
        )

    return jsonify({"code": 0, "msg": "成功", "data": response_data})


if __name__ == "__main__":
    logger.info("启动：port=%s path=%s threadpool=%s", WEBHOOK_PORT, QIWEAPI_CALLBACK_PATH, TASK_THREADPOOL_SIZE)
    logger.info("Webhook URL 示例：http://<host>:%s%s?token=***", WEBHOOK_PORT, QIWEAPI_CALLBACK_PATH)
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False, threaded=True)