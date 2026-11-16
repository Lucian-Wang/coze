# -*- coding: utf-8 -*-
"""Webhook 入站鉴权：通过 query token 防止未授权 POST。"""

import os
from flask import request

WEBHOOK_AUTH_TOKEN = os.environ.get("WEBHOOK_AUTH_TOKEN", "")
WEBHOOK_AUTH_MODE = os.environ.get("WEBHOOK_AUTH_MODE", "query")  # query|header
WEBHOOK_AUTH_QUERY = os.environ.get("WEBHOOK_AUTH_QUERY", "token")
WEBHOOK_AUTH_HEADER = os.environ.get("WEBHOOK_AUTH_HEADER", "X-Webhook-Token")


def is_authorized_webhook() -> bool:
    """
    默认使用 query token（你指定的方式）：
      /qiweapi/callback?token=xxxxxx

    也支持 header 模式（可选）：
      X-Webhook-Token: xxxxxx
    """
    if not WEBHOOK_AUTH_TOKEN:
        # 强制要求配置，不配置则拒绝所有请求
        return False

    if WEBHOOK_AUTH_MODE == "header":
        got = (request.headers.get(WEBHOOK_AUTH_HEADER) or "").strip()
        return got == WEBHOOK_AUTH_TOKEN

    # 默认 query
    got = (request.args.get(WEBHOOK_AUTH_QUERY) or "").strip()
    return got == WEBHOOK_AUTH_TOKEN