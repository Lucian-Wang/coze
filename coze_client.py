# -*- coding: utf-8 -*-
"""Coze SSE stream_run 客户端：发送 prompt（文本），聚合 answer。"""

import os
import json
import requests
from typing import Dict, List, Optional

COZE_STREAM_RUN_URL = os.environ.get("COZE_STREAM_RUN_URL", "")
COZE_TOKEN = os.environ.get("COZE_TOKEN", "")
COZE_PROJECT_ID = os.environ.get("COZE_PROJECT_ID", "")
COZE_SESSION_ID_PREFIX = os.environ.get("COZE_SESSION_ID_PREFIX", "session_")
COZE_TIMEOUT = int(os.environ.get("COZE_TIMEOUT", "60"))


def _require_env(name: str, v: str) -> None:
    if not v:
        raise RuntimeError(f"{name} 为空（请设置环境变量 {name}）")


def _headers() -> Dict[str, str]:
    _require_env("COZE_TOKEN", COZE_TOKEN)
    return {
        "Authorization": f"Bearer {COZE_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


def _payload(session_user_id: str, text: str) -> dict:
    _require_env("COZE_PROJECT_ID", COZE_PROJECT_ID)
    _require_env("COZE_STREAM_RUN_URL", COZE_STREAM_RUN_URL)
    return {
        "content": {"query": {"prompt": [{"type": "text", "content": {"text": text}}]}},
        "type": "query",
        "session_id": f"{COZE_SESSION_ID_PREFIX}{session_user_id}",
        "project_id": COZE_PROJECT_ID,
    }


def parse_sse_events(resp):
    """解析 SSE（Server-Sent Events）事件流。"""
    event_name = None
    data_lines: List[str] = []

    def flush():
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = None
            return None
        data_text = "\n".join(data_lines).strip()
        data_lines = []
        ev = event_name
        event_name = None
        return ev, data_text

    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            item = flush()
            if item:
                yield item
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue

    item = flush()
    if item:
        yield item


def call_coze_collect_answer(session_user_id: str, prompt_text: str, system_text: Optional[str] = None) -> str:
    """调用 Coze stream_run，并聚合最终回复文本（answer 片段）。

    Args:
        session_user_id: 会话用户ID
        prompt_text: 提示文本
        system_text: 可选的系统提示（如果提供，会拼接到 prompt_text 前面）

    Returns:
        聚合后的回答文本
    """
    # 如果提供了 system_text，将其拼接到 prompt 前面
    if system_text:
        full_prompt = f"{system_text}\n\n{prompt_text}"
    else:
        full_prompt = prompt_text

    parts: List[str] = []
    with requests.post(
        COZE_STREAM_RUN_URL,
        headers=_headers(),
        json=_payload(session_user_id, full_prompt),
        stream=True,
        timeout=COZE_TIMEOUT,
    ) as resp:
        resp.raise_for_status()
        for _ev, data_text in parse_sse_events(resp):
            try:
                obj = json.loads(data_text)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            if t == "answer":
                c = obj.get("content") or {}
                if isinstance(c, dict):
                    piece = c.get("answer")
                    if isinstance(piece, str) and piece:
                        parts.append(piece)
            elif t == "message_end":
                break
    return "".join(parts).strip()