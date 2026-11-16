# -*- coding: utf-8 -*-
"""
qiweapi 客户端封装：
- doApi：发送文本、发送图片、下载云文件（wxDownload / wxWorkDownload）
- doFileApi：上传图片（cdnBigUpload）
- Coze 回复若含图片 URL 或 Markdown 图片，则自动下载->上传->sendImage，再补发文本

增强点（当前版本）：
1) 发送节流（全局 + 每用户最小间隔），避免过快触发风控
2) 拟人延迟
   - 秒级：按字数估算打字时间（base + per_char + jitter + max）
   - 分钟级：1~3 分钟随机延迟（推荐只对首段启用）
3) 长文本分段发送（自然分段优先：空行 -> 单换行 -> 句末 -> 字数兜底）
4) 支持 Markdown autolink：<https://example.com> 正常发送、不截断
5) 图片 URL/Markdown 图片解析、清洗与发送

注意：
- 所有涉及 guid 的调用，都支持传入 guid 参数（优先用回调消息里的 guid）
- QIWE_API_GUID 环境变量仅做兜底；建议不要依赖它

2026-03-08 修复：
- 增加 get_env_guid_for_marketing()：用于沉默运营/后台主动触达场景（无回调 guid）
"""

import os
import re
import time
import random
import logging
import threading
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger("qiwe_client")

QIWE_API_BASE_URL = os.environ.get("QIWE_API_BASE_URL", "http://manager.qiweapi.com/qiwe")
QIWE_API_TOKEN = os.environ.get("QIWE_API_TOKEN", "")
QIWE_API_GUID = os.environ.get("QIWE_API_GUID", "")

QIWE_SEND_TIMEOUT = int(os.environ.get("QIWE_SEND_TIMEOUT", "10"))
QIWE_UPLOAD_TIMEOUT = int(os.environ.get("QIWE_UPLOAD_TIMEOUT", "30"))
QIWE_DOWNLOAD_TIMEOUT = int(os.environ.get("QIWE_DOWNLOAD_TIMEOUT", "30"))

QIWE_METHOD_WX_DOWNLOAD = os.environ.get("QIWE_METHOD_WX_DOWNLOAD", "/cloud/wxDownload")              # GM (msgType=101)
QIWE_METHOD_WXWORK_DOWNLOAD = os.environ.get("QIWE_METHOD_WXWORK_DOWNLOAD", "/cloud/wxWorkDownload")  # WW (msgType=14/16)

QIWE_DOFILE_METHOD = os.environ.get("QIWE_DOFILE_METHOD", "/cloud/cdnBigUpload")
QIWE_IMAGE_FILETYPE_UPLOAD = int(os.environ.get("QIWE_IMAGE_FILETYPE_UPLOAD", "1"))

DIRECT_DOWNLOAD_TIMEOUT = int(os.environ.get("DIRECT_DOWNLOAD_TIMEOUT", "30"))
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(20 * 1024 * 1024)))

SEND_TEXT_AFTER_IMAGE = os.environ.get("SEND_TEXT_AFTER_IMAGE", "1") not in ("0", "false", "False")
SEND_IMAGE_FROM_COZE_REPLY = os.environ.get("SEND_IMAGE_FROM_COZE_REPLY", "1") not in ("0", "false", "False")

# ===== 发送节流（.env 可配）=====
# 每个用户两条消息之间最小间隔（秒）
QIWE_MIN_INTERVAL_PER_USER_SEC = float(os.environ.get("QIWE_MIN_INTERVAL_PER_USER_SEC", "1.0"))
# 全局最小间隔（秒）：例如 0.2 => 全局 5 QPS
QIWE_MIN_INTERVAL_GLOBAL_SEC = float(os.environ.get("QIWE_MIN_INTERVAL_GLOBAL_SEC", "0.2"))
# 是否开启节流
QIWE_SEND_THROTTLE_ENABLED = os.environ.get("QIWE_SEND_THROTTLE_ENABLED", "1") not in ("0", "false", "False")

_send_lock = threading.Lock()
_last_send_at_global = 0.0
_last_send_at_user: Dict[str, float] = {}


def get_env_guid_for_marketing() -> str:
    """
    沉默运营/后台主动触达场景：没有回调 msg_item.guid，因此只能用环境变量兜底。

    - 单企微实例/单租户：设置 QIWE_API_GUID 即可。
    - 多企微实例/多租户：需要改成“按用户/实例映射 guid”，不能只靠单个 env。

    抛异常是刻意设计：让问题在日志里显式暴露（否则默默不发更难排障）。
    """
    g = (QIWE_API_GUID or "").strip()
    if not g:
        raise RuntimeError(
            "QIWE_API_GUID 为空：沉默运营/后台主动发送需要配置 QIWE_API_GUID（或自行实现按用户/实例映射 guid）"
        )
    return g


def _throttle_send(to_id: str) -> None:
    """
    节流：阻塞式 sleep（简单可靠）
    - per-user: 控制同一用户的发送间隔
    - global: 控制全局 QPS
    """
    global _last_send_at_global

    if not QIWE_SEND_THROTTLE_ENABLED:
        return

    now = time.time()
    with _send_lock:
        wait_g = (_last_send_at_global + QIWE_MIN_INTERVAL_GLOBAL_SEC) - now
        last_u = _last_send_at_user.get(to_id, 0.0)
        wait_u = (last_u + QIWE_MIN_INTERVAL_PER_USER_SEC) - now
        wait = max(wait_g, wait_u, 0.0)
        if wait > 0:
            time.sleep(wait)

        t2 = time.time()
        _last_send_at_global = t2
        _last_send_at_user[to_id] = t2


# ===== 拟人化延迟（秒级：打字 + 抖动）=====
QIWE_HUMAN_DELAY_ENABLED = os.environ.get("QIWE_HUMAN_DELAY_ENABLED", "1") not in ("0", "false", "False")
QIWE_HUMAN_DELAY_BASE_MS = int(os.environ.get("QIWE_HUMAN_DELAY_BASE_MS", "150"))
QIWE_HUMAN_DELAY_PER_CHAR_MS = int(os.environ.get("QIWE_HUMAN_DELAY_PER_CHAR_MS", "5"))
QIWE_HUMAN_DELAY_MAX_MS = int(os.environ.get("QIWE_HUMAN_DELAY_MAX_MS", "800"))
QIWE_HUMAN_DELAY_JITTER_MS = int(os.environ.get("QIWE_HUMAN_DELAY_JITTER_MS", "100"))
QIWE_HUMAN_DELAY_IMAGE_BONUS_MS = int(os.environ.get("QIWE_HUMAN_DELAY_IMAGE_BONUS_MS", "50"))

# 新用户首次消息延迟（毫秒），设为0立即响应
QIWE_FIRST_MESSAGE_DELAY_MS = int(os.environ.get("QIWE_FIRST_MESSAGE_DELAY_MS", "0"))

# ===== 拟人化延迟（分钟级：1~3 分钟随机）=====
QIWE_HUMAN_LONG_DELAY_ENABLED = os.environ.get("QIWE_HUMAN_LONG_DELAY_ENABLED", "0") not in ("0", "false", "False")
QIWE_HUMAN_LONG_DELAY_MIN_SEC = int(os.environ.get("QIWE_HUMAN_LONG_DELAY_MIN_SEC", "60"))
QIWE_HUMAN_LONG_DELAY_MAX_SEC = int(os.environ.get("QIWE_HUMAN_LONG_DELAY_MAX_SEC", "180"))
QIWE_HUMAN_LONG_DELAY_FIRST_PART_ONLY = os.environ.get("QIWE_HUMAN_LONG_DELAY_FIRST_PART_ONLY", "1") not in (
    "0",
    "false",
    "False",
)


def _human_delay_ms(text: str, *, has_image: bool = False) -> int:
    if not QIWE_HUMAN_DELAY_ENABLED:
        return 0

    t = (text or "").strip()
    n = len(t)
    ms = QIWE_HUMAN_DELAY_BASE_MS + n * QIWE_HUMAN_DELAY_PER_CHAR_MS
    if has_image:
        ms += QIWE_HUMAN_DELAY_IMAGE_BONUS_MS

    if QIWE_HUMAN_DELAY_JITTER_MS > 0:
        ms += random.randint(0, QIWE_HUMAN_DELAY_JITTER_MS)

    ms = max(0, min(ms, QIWE_HUMAN_DELAY_MAX_MS))
    return ms


def _sleep_human_delay(text: str, *, has_image: bool = False, is_first_part: bool = False, is_new_user: bool = False) -> None:
    """
    分两层延迟：
    1) 长延迟：1~3分钟随机（更像真人“看到了，想一想再回”）
    2) 短延迟：按字数估算的“打字延迟”（秒级）
    """
    # 新用户首次消息：使用独立延迟配置（更快响应）
    if is_new_user and is_first_part:
        if QIWE_FIRST_MESSAGE_DELAY_MS > 0:
            time.sleep(QIWE_FIRST_MESSAGE_DELAY_MS / 1000.0)
        return
    
    if QIWE_HUMAN_LONG_DELAY_ENABLED:
        if (not QIWE_HUMAN_LONG_DELAY_FIRST_PART_ONLY) or is_first_part:
            lo = max(0, int(QIWE_HUMAN_LONG_DELAY_MIN_SEC))
            hi = max(lo, int(QIWE_HUMAN_LONG_DELAY_MAX_SEC))
            sec = random.randint(lo, hi)
            time.sleep(sec)

    ms = _human_delay_ms(text, has_image=has_image)
    if ms > 0:
        time.sleep(ms / 1000.0)


# ===== 长文本分段发送 =====
QIWE_SPLIT_LONG_TEXT_ENABLED = os.environ.get("QIWE_SPLIT_LONG_TEXT_ENABLED", "1") not in ("0", "false", "False")
QIWE_SPLIT_MAX_CHARS_PER_MSG = int(os.environ.get("QIWE_SPLIT_MAX_CHARS_PER_MSG", "140"))
QIWE_SPLIT_MAX_PARTS = int(os.environ.get("QIWE_SPLIT_MAX_PARTS", "6"))


def _split_long_text(text: str) -> List[str]:
    """
    拆分长文本（更像真人聊天）——规则：

    优先级（从高到低）：
    1) 空行分段：\\n\\n（段落）
    2) 单换行分段：\\n（更像微信一行一行发）
    3) 句号/问号/感叹号分句：。！？!?
    4) 最后兜底：按最大字数硬切

    并限制最多段数，防止刷屏。
    """
    t = (text or "").strip()
    if not t:
        return []
    if (not QIWE_SPLIT_LONG_TEXT_ENABLED) or len(t) <= QIWE_SPLIT_MAX_CHARS_PER_MSG:
        return [t]

    # 1) 先按空行分段落
    paras = [p.strip() for p in re.split(r"\n{2,}", t) if p.strip()]

    # 2) 再按单换行拆成“行”（更像聊天）
    lines: List[str] = []
    for p in paras:
        for ln in p.split("\n"):
            s = (ln or "").strip()
            if s:
                lines.append(s)

    chunks: List[str] = []

    def push_piece(piece: str) -> None:
        s = (piece or "").strip()
        if s:
            chunks.append(s)

    # 3) 逐行处理：行太长就按句子拼/切
    for ln in lines:
        if len(ln) <= QIWE_SPLIT_MAX_CHARS_PER_MSG:
            push_piece(ln)
            continue

        sents = re.split(r"(?<=[。！？!?])\s*", ln)
        buf = ""
        for s in sents:
            s = (s or "").strip()
            if not s:
                continue
            if not buf:
                buf = s
                continue
            if len(buf) + 1 + len(s) <= QIWE_SPLIT_MAX_CHARS_PER_MSG:
                buf = buf + " " + s
            else:
                push_piece(buf)
                buf = s
        if buf:
            push_piece(buf)

    # 4) 兜底：仍然超长则硬切
    final: List[str] = []
    for c in chunks:
        if len(c) <= QIWE_SPLIT_MAX_CHARS_PER_MSG:
            final.append(c)
            continue
        s = c
        while s:
            final.append(s[:QIWE_SPLIT_MAX_CHARS_PER_MSG].strip())
            s = s[QIWE_SPLIT_MAX_CHARS_PER_MSG:].strip()

    # 5) 限制最多段数
    if len(final) > QIWE_SPLIT_MAX_PARTS:
        trimmed = final[:QIWE_SPLIT_MAX_PARTS]
        if trimmed:
            trimmed[-1] = trimmed[-1].rstrip("。") + "…"
        return trimmed

    return final


# ===== URL / Markdown 解析 =====
IMAGE_URL_RE = re.compile(
    r"<?(https?://[^\s<>]+?\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s<>]*)?)>?",
    re.IGNORECASE,
)

MD_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(([^)\s]+)\)",
    re.IGNORECASE,
)

MD_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")

URL_TRAIL_TRIM_RE = re.compile(r"""[)\]}'"，。；;：:,!?！…]+$""")


def _require_env(name: str, v: str) -> None:
    if not v:
        raise RuntimeError(f"{name} 为空（请设置环境变量 {name}）")


def _require_guid(guid: str) -> str:
    g = (guid or "").strip()
    if not g:
        raise RuntimeError("guid 为空（应从回调消息 msg_item.guid 传入）")
    return g


class QiweApiClient:
    def __init__(self, token: str, base_url: str):
        self.token = token
        self.base_url = base_url
        self.do_api_url = f"{base_url}/api/qw/doApi"
        self.do_file_api_url = f"{base_url}/api/qw/doFileApi"

    def _headers_json(self) -> Dict[str, str]:
        return {"X-QIWEI-TOKEN": self.token, "Content-Type": "application/json"}

    def _headers_file(self) -> Dict[str, str]:
        return {"X-QIWEI-TOKEN": self.token}

    def do_api(self, method: str, params: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        payload = {"method": method, "params": params}
        resp = requests.post(self.do_api_url, headers=self._headers_json(), json=payload, timeout=timeout)
        return resp.json() if resp.content else {}

    def send_hyper_text(self, to_id: str, content: str, guid: str) -> Dict[str, Any]:
        g = _require_guid(guid)
        return self.do_api(
            method="/msg/sendHyperText",
            params={"guid": g, "content": [{"subtype": 0, "text": content}], "toId": to_id},
            timeout=QIWE_SEND_TIMEOUT,
        )

    def send_image(self, to_id: str, guid: str, file_info: Dict[str, Any], filename: str) -> Dict[str, Any]:
        g = _require_guid(guid)
        params = {
            "guid": g,
            "fileAesKey": file_info["fileAesKey"],
            "fileId": file_info["fileId"],
            "fileKey": file_info["fileKey"],
            "fileMd5": file_info["fileMd5"],
            "fileSize": int(file_info["fileSize"]),
            "filename": filename,
            "toId": to_id,
        }
        return self.do_api(method="/msg/sendImage", params=params, timeout=QIWE_SEND_TIMEOUT)

    def upload_image_bytes_for_sendimage(self, filename: str, content_bytes: bytes, mime: str, guid: str) -> Dict[str, Any]:
        g = _require_guid(guid)
        files = [("file", (filename, content_bytes, mime))]
        data = {"method": QIWE_DOFILE_METHOD, "guid": g, "fileType": str(QIWE_IMAGE_FILETYPE_UPLOAD)}
        resp = requests.post(
            self.do_file_api_url,
            headers=self._headers_file(),
            data=data,
            files=files,
            timeout=QIWE_UPLOAD_TIMEOUT,
        )
        return resp.json() if resp.content else {}


_client: Optional[QiweApiClient] = None


def qiwe_client() -> QiweApiClient:
    global _client
    _require_env("QIWE_API_TOKEN", QIWE_API_TOKEN)
    if _client is None:
        _client = QiweApiClient(QIWE_API_TOKEN, QIWE_API_BASE_URL)
    return _client


def extract_cloud_url(resp: Dict[str, Any]) -> Optional[str]:
    data = resp.get("data")
    if isinstance(data, dict):
        for k in ("cloudUrl", "httpUrl", "url", "fileHttpUrl", "downloadUrl", "fileUrl"):
            v = data.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v
    return None


def pick_gm_url_and_filetype(msg_data: Dict[str, Any]) -> Tuple[Optional[str], int]:
    big = msg_data.get("fileBigHttpUrl")
    if isinstance(big, str) and big.startswith("http"):
        return big, 1
    mid = msg_data.get("fileMiddleHttpUrl")
    if isinstance(mid, str) and mid.startswith("http"):
        return mid, 2
    th = msg_data.get("fileThumbHttpUrl")
    if isinstance(th, str) and th.startswith("http"):
        return th, 3
    return None, 2


def inbound_file_to_cloudurl(msg_type: int, msg_data: Dict[str, Any], file_type: int, guid: str) -> Optional[str]:
    client = qiwe_client()
    g = _require_guid(guid)

    if msg_type == 101:
        direct_url, _auto_type = pick_gm_url_and_filetype(msg_data)
        params = {
            "guid": g,
            "fileAesKey": msg_data.get("fileAeskey") or msg_data.get("fileAesKey"),
            "fileAuthkey": msg_data.get("fileAuthkey") or msg_data.get("fileAuthKey"),
            "fileSize": msg_data.get("fileBigSize")
            or msg_data.get("fileMiddleSize")
            or msg_data.get("fileThumbSize")
            or msg_data.get("fileSize"),
            "fileType": int(file_type),
            "fileUrl": direct_url or msg_data.get("fileUrl") or "sss",
        }
        params = {k: v for k, v in params.items() if v not in (None, "", [])}
        resp = client.do_api(QIWE_METHOD_WX_DOWNLOAD, params=params, timeout=QIWE_DOWNLOAD_TIMEOUT)
        if resp.get("code") != 0:
            logger.error("wxDownload failed: user msgType=101 resp=%s params=%s", resp, params)
            return None
        return extract_cloud_url(resp)

    if msg_type in (14, 16):
        params = {
            "guid": g,
            "fileAesKey": msg_data.get("fileAeskey") or msg_data.get("fileAesKey"),
            "fileId": msg_data.get("fileId"),
            "fileSize": msg_data.get("fileSize"),
            "fileType": int(file_type),
        }
        params = {k: v for k, v in params.items() if v not in (None, "", [])}
        resp = client.do_api(QIWE_METHOD_WXWORK_DOWNLOAD, params=params, timeout=QIWE_DOWNLOAD_TIMEOUT)
        if resp.get("code") != 0:
            logger.error("wxWorkDownload failed: resp=%s params=%s", resp, params)
            return None
        return extract_cloud_url(resp)

    return None


def inbound_voice_to_cloudurl(msg_type: int, msg_data: Dict[str, Any], guid: str) -> Optional[str]:
    return inbound_file_to_cloudurl(msg_type=msg_type, msg_data=msg_data, file_type=5, guid=guid)


def inbound_image_to_cloudurl(msg_type: int, msg_data: Dict[str, Any], guid: str) -> Optional[str]:
    if msg_type == 101:
        _direct_url, file_type = pick_gm_url_and_filetype(msg_data)
        return inbound_file_to_cloudurl(msg_type=msg_type, msg_data=msg_data, file_type=file_type, guid=guid)

    if msg_type == 14:
        image_has_hd = bool(msg_data.get("imageHasHd"))
        file_type = 1 if image_has_hd else 2
        return inbound_file_to_cloudurl(msg_type=msg_type, msg_data=msg_data, file_type=file_type, guid=guid)

    return None


def clean_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith("<") and u.endswith(">"):
        u = u[1:-1].strip()
    u = u.strip("\"'")
    u = URL_TRAIL_TRIM_RE.sub("", u)
    return u


def _normalize_md_autolinks(text: str) -> str:
    return MD_AUTOLINK_RE.sub(r"\1", text or "")


def extract_image_urls(text: str) -> List[str]:
    t = text or ""
    found: List[str] = []

    for u in MD_IMAGE_RE.findall(t):
        cu = clean_url(u)
        if cu and cu.lower().startswith("http"):
            found.append(cu)

    for u in IMAGE_URL_RE.findall(t):
        cu = clean_url(u)
        if cu and cu.lower().startswith("http"):
            found.append(cu)

    seen = set()
    out: List[str] = []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def strip_image_markdown_and_urls_from_text(text: str, urls: List[str]) -> str:
    out = (text or "")
    out = MD_IMAGE_RE.sub("", out)
    for u in urls:
        if not u:
            continue
        out = out.replace(f"<{u}>", "")
        out = out.replace(u, "")
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def guess_filename_from_url(url: str) -> str:
    try:
        p = urlparse(url)
        base = (p.path or "").rsplit("/", 1)[-1]
        base = base.split("?", 1)[0] or "image.jpg"
        if not base.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return base + ".jpg"
        return base
    except Exception:
        return "image.jpg"


def guess_mime_from_filename(filename: str) -> str:
    fn = filename.lower()
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith(".jpg") or fn.endswith(".jpeg"):
        return "image/jpeg"
    if fn.endswith(".gif"):
        return "image/gif"
    if fn.endswith(".webp"):
        return "image/webp"
    return "application/octet-stream"


def download_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=DIRECT_DOWNLOAD_TIMEOUT, stream=True)
    r.raise_for_status()
    chunks: List[bytes] = []
    total = 0
    for chunk in r.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_FILE_BYTES:
            raise RuntimeError("文件过大，拒绝下载")
        chunks.append(chunk)
    return b"".join(chunks)


def send_reply_to_qiwe(to_id: str, reply_text: str, guid: str, is_new_user: bool = False) -> None:
    client = qiwe_client()
    g = _require_guid(guid)

    reply_text = _normalize_md_autolinks(reply_text)

    # 不发图：纯文本（分段）
    if not SEND_IMAGE_FROM_COZE_REPLY:
        parts = _split_long_text(reply_text)
        for idx, part in enumerate(parts):
            _sleep_human_delay(part, has_image=False, is_first_part=(idx == 0), is_new_user=is_new_user)
            _throttle_send(to_id)
            ret = client.send_hyper_text(to_id=to_id, content=part, guid=g)
            if ret.get("code") != 0:
                raise RuntimeError(f"qiwe send text failed: {ret}")
        return

    urls = extract_image_urls(reply_text)

    # 没有图片：纯文本（分段）
    if not urls:
        parts = _split_long_text(reply_text)
        for idx, part in enumerate(parts):
            _sleep_human_delay(part, has_image=False, is_first_part=(idx == 0), is_new_user=is_new_user)
            _throttle_send(to_id)
            ret = client.send_hyper_text(to_id=to_id, content=part, guid=g)
            if ret.get("code") != 0:
                raise RuntimeError(f"qiwe send text failed: {ret}")
        return

    # 有图片：先发图
    for i, img_url in enumerate(urls):
        _sleep_human_delay("（发送图片）", has_image=True, is_first_part=(i == 0), is_new_user=is_new_user)
        _throttle_send(to_id)

        try:
            img_bytes = download_bytes(img_url)
            filename = guess_filename_from_url(img_url)
            mime = guess_mime_from_filename(filename)

            up = client.upload_image_bytes_for_sendimage(filename=filename, content_bytes=img_bytes, mime=mime, guid=g)
            if up.get("code") != 0 or not isinstance(up.get("data"), dict):
                fb = client.send_hyper_text(to_id=to_id, content=img_url, guid=g)
                if fb.get("code") != 0:
                    raise RuntimeError(f"qiwe fallback send url failed: {fb}")
                continue

            send_ret = client.send_image(to_id=to_id, guid=g, file_info=up["data"], filename=filename)
            if send_ret.get("code") != 0:
                fb = client.send_hyper_text(to_id=to_id, content=img_url, guid=g)
                if fb.get("code") != 0:
                    raise RuntimeError(f"qiwe fallback send url failed: {fb}")
        except Exception:
            fb = client.send_hyper_text(to_id=to_id, content=img_url, guid=g)
            if fb.get("code") != 0:
                raise

    # 再发剩余文字（分段）
    if SEND_TEXT_AFTER_IMAGE:
        rest = strip_image_markdown_and_urls_from_text(reply_text, urls)
        if rest:
            parts = _split_long_text(rest)
            for idx, part in enumerate(parts):
                _sleep_human_delay(part, has_image=True, is_first_part=(idx == 0), is_new_user=is_new_user)
                _throttle_send(to_id)
                ret = client.send_hyper_text(to_id=to_id, content=part, guid=g)
                if ret.get("code") != 0:
                    raise RuntimeError(f"qiwe send rest text failed: {ret}")