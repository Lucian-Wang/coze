# -*- coding: utf-8 -*-
"""Volcengine Ark /responses 封装：文本决策模型 + vision OCR 模型。

增强点：
- Ark 报 4xx/5xx 时输出 response body（最关键，否则无法定位 400）
- vision 请求增加一次 fallback payload（兼容不同字段结构）
"""

import os
import json
import logging
import requests
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ark_client")

ARK_URL = os.environ.get("ARK_URL", "https://ark.cn-beijing.volces.com/api/v3/responses")
ARK_BEARER = os.environ.get("ARK_BEARER", "")
ARK_TIMEOUT = int(os.environ.get("ARK_TIMEOUT", "60"))

ARK_VISION_MODEL = os.environ.get("ARK_VISION_MODEL", "doubao-seed-1-6-vision-250815")
ARK_DECISION_MODEL = os.environ.get("ARK_DECISION_MODEL", "doubao-seed-2-0-pro-260215")

# 控制日志长度，避免打爆日志
ARK_DEBUG_MAXLEN = int(os.environ.get("ARK_DEBUG_MAXLEN", "6000"))


def _require_env(name: str, v: str) -> None:
    if not v:
        raise RuntimeError(f"{name} 为空（请设置环境变量 {name}）")


def _headers() -> Dict[str, str]:
    _require_env("ARK_BEARER", ARK_BEARER)
    return {"Authorization": f"Bearer {ARK_BEARER}", "Content-Type": "application/json"}


def _truncate(s: str, n: int) -> str:
    if s is None:
        return ""
    return s if len(s) <= n else s[:n] + "...(truncated)"


def _safe_dump_payload(payload: Dict[str, Any]) -> str:
    # payload 里通常没有 bearer，但防御性脱敏一下
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        text = str(payload)
    # 脱敏：避免不小心把 token 打出去
    if ARK_BEARER:
        text = text.replace(ARK_BEARER, "***ARK_BEARER***")
    return _truncate(text, ARK_DEBUG_MAXLEN)


def _post(payload: Dict[str, Any]) -> Dict[str, Any]:
    h = _headers()
    r = requests.post(ARK_URL, headers=h, json=payload, timeout=ARK_TIMEOUT)

    if r.status_code >= 400:
        # 关键：把 Ark 返回 body 打出来（通常包含具体字段错误）
        body = ""
        try:
            body = r.text or ""
        except Exception:
            body = "<no response text>"
        logger.warning(
            "Ark request failed: status=%s url=%s body=%s",
            r.status_code,
            ARK_URL,
            _truncate(body, ARK_DEBUG_MAXLEN),
        )
        logger.warning("Ark payload(debug): %s", _safe_dump_payload(payload))

    r.raise_for_status()
    return r.json()


def call_ark_text(prompt: str, model: str) -> Dict[str, Any]:
    """调用 Ark 文本模型（用于沉默决策/画像判定）。"""
    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
    }
    return _post(payload)


def _build_vision_payload_v1(image_url: str, prompt: str) -> Dict[str, Any]:
    # 你当前使用的结构（先保持不变）
    return {
        "model": ARK_VISION_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": image_url},
                    {"type": "input_text", "text": prompt},
                ],
            }
        ],
    }


def _build_vision_payload_v2(image_url: str, prompt: str) -> Dict[str, Any]:
    # 常见兼容结构：input_image 里用 url 字段（有些实现是这样）
    return {
        "model": ARK_VISION_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "url": image_url},
                    {"type": "input_text", "text": prompt},
                ],
            }
        ],
    }


def call_ark_vision(image_url: str, prompt: str) -> Dict[str, Any]:
    """调用 Ark 视觉模型（用于图片 OCR/要点提取）。"""
    # 先按 v1 尝试
    payload1 = _build_vision_payload_v1(image_url, prompt)
    try:
        return _post(payload1)
    except requests.HTTPError as e:
        # 只有 400 才做一次 fallback（避免把 401/429/500 也重试两遍）
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        if status != 400:
            raise

        # 尝试根据 body 文本判断是否值得换 payload（粗略判断）
        body = ""
        try:
            body = resp.text or ""
        except Exception:
            body = ""

        # 如果报错与 image 字段/类型有关，尝试 v2
        body_l = body.lower()
        if ("image" in body_l) or ("input_image" in body_l) or ("image_url" in body_l) or ("url" in body_l):
            logger.warning("Ark vision 400 likely payload mismatch, retry with payload v2...")
            payload2 = _build_vision_payload_v2(image_url, prompt)
            return _post(payload2)

        # 否则原样抛出
        raise


def extract_output_text(data: Dict[str, Any]) -> str:
    """尽可能从 Ark 返回中提取文本输出。"""
    parts: List[str] = []

    ot = data.get("output_text")
    if isinstance(ot, str) and ot.strip():
        parts.append(ot.strip())

    out = data.get("output")
    if isinstance(out, list):
        for item in out:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict):
                        t = c.get("text")
                        if isinstance(t, str) and t.strip():
                            parts.append(t.strip())

    return "\n".join(parts).strip()