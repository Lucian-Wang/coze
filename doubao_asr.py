# -*- coding: utf-8 -*-

import os
import json
import time
import uuid
import logging
from typing import Any, Dict, Tuple, Optional

import requests

logger = logging.getLogger("doubao_asr")

DOUBAO_ASR_SUBMIT_URL = os.environ.get(
    "DOUBAO_ASR_SUBMIT_URL",
    "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit",
).strip()

DOUBAO_ASR_QUERY_URL = os.environ.get(
    "DOUBAO_ASR_QUERY_URL",
    "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query",
).strip()

DOUBAO_ASR_APP_KEY = os.environ.get("DOUBAO_ASR_APP_KEY", "").strip()
DOUBAO_ASR_ACCESS_KEY = os.environ.get("DOUBAO_ASR_ACCESS_KEY", "").strip()
DOUBAO_ASR_RESOURCE_ID = os.environ.get("DOUBAO_ASR_RESOURCE_ID", "volc.seedasr.auc").strip()

DOUBAO_ASR_MODEL_NAME = os.environ.get("DOUBAO_ASR_MODEL_NAME", "bigmodel").strip()
DOUBAO_ASR_AUDIO_FORMAT = os.environ.get("DOUBAO_ASR_AUDIO_FORMAT", "wav").strip()
DOUBAO_ASR_AUDIO_LANGUAGE = os.environ.get("DOUBAO_ASR_AUDIO_LANGUAGE", "").strip()

DOUBAO_ASR_HTTP_TIMEOUT = int(os.environ.get("DOUBAO_ASR_HTTP_TIMEOUT", "30"))
DOUBAO_ASR_POLL_INTERVAL = float(os.environ.get("DOUBAO_ASR_POLL_INTERVAL", "1"))
DOUBAO_ASR_MAX_POLL_SECONDS = int(os.environ.get("DOUBAO_ASR_MAX_POLL_SECONDS", "60"))


def _require_env(name: str, v: str) -> None:
    if not v:
        raise RuntimeError(f"{name} is empty; please set env {name}")


def _headers(request_id: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Api-App-Key": DOUBAO_ASR_APP_KEY,
        "X-Api-Access-Key": DOUBAO_ASR_ACCESS_KEY,
        "X-Api-Resource-Id": DOUBAO_ASR_RESOURCE_ID,
        "X-Api-Request-Id": request_id,
    }


def submit_asr_task(audio_url: str, audio_format: Optional[str] = None, language: Optional[str] = None) -> Tuple[str, str]:
    _require_env("DOUBAO_ASR_APP_KEY", DOUBAO_ASR_APP_KEY)
    _require_env("DOUBAO_ASR_ACCESS_KEY", DOUBAO_ASR_ACCESS_KEY)

    task_id = str(uuid.uuid4())
    headers = _headers(task_id)
    headers["X-Api-Sequence"] = "-1"

    fmt = (audio_format or DOUBAO_ASR_AUDIO_FORMAT).strip()
    if not fmt:
        raise ValueError("audio_format is empty")

    lang = (language if language is not None else DOUBAO_ASR_AUDIO_LANGUAGE).strip()

    audio_obj: Dict[str, Any] = {"url": audio_url, "format": fmt}
    if lang:
        audio_obj["language"] = lang

    payload = {
        "user": {"uid": "qiwe_bot"},
        "audio": audio_obj,
        "request": {
            "model_name": DOUBAO_ASR_MODEL_NAME,
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "enable_speaker_info": False,
            "enable_channel_split": False,
            "corpus": {
                "correct_table_name": "",
                "context": "",
            },
        },
    }

    resp = requests.post(DOUBAO_ASR_SUBMIT_URL, data=json.dumps(payload), headers=headers, timeout=DOUBAO_ASR_HTTP_TIMEOUT)

    code = resp.headers.get("X-Api-Status-Code", "")
    msg = resp.headers.get("X-Api-Message", "")
    x_tt_logid = resp.headers.get("X-Tt-Logid", "")

    if code != "20000000":
        raise RuntimeError(f"ASR submit failed: code={code} msg={msg} logid={x_tt_logid} body={resp.text[:200]}")

    return task_id, x_tt_logid


def query_asr_task(task_id: str) -> Tuple[str, str, Dict[str, Any]]:
    headers = _headers(task_id)
    resp = requests.post(DOUBAO_ASR_QUERY_URL, data=json.dumps({}), headers=headers, timeout=DOUBAO_ASR_HTTP_TIMEOUT)

    code = resp.headers.get("X-Api-Status-Code", "")
    msg = resp.headers.get("X-Api-Message", "")

    try:
        js = resp.json() if resp.content else {}
    except Exception:
        js = {}
    return code, msg, js


def extract_text(query_json: Dict[str, Any]) -> str:
    if not isinstance(query_json, dict):
        return ""
    result = query_json.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


def asr_url_to_text(audio_url: str, audio_format: Optional[str] = None, language: Optional[str] = None) -> str:
    task_id, x_tt_logid = submit_asr_task(audio_url, audio_format=audio_format, language=language)
    logger.info("ASR submitted: task_id=%s logid=%s", task_id, x_tt_logid)

    deadline = time.time() + DOUBAO_ASR_MAX_POLL_SECONDS
    last_code = ""
    last_msg = ""

    while time.time() < deadline:
        code, msg, js = query_asr_task(task_id)
        last_code, last_msg = code, msg

        if code == "20000000":
            return extract_text(js)

        if code in ("20000001", "20000002"):
            time.sleep(DOUBAO_ASR_POLL_INTERVAL)
            continue

        if code == "20000003":
            logger.warning("ASR silent audio: task_id=%s msg=%s", task_id, msg)
            return ""

        raise RuntimeError(f"ASR task failed: task_id={task_id} code={code} msg={msg} body={str(js)[:200]}")

    raise TimeoutError(f"ASR poll timeout: task_id={task_id} last_code={last_code} last_msg={last_msg}")