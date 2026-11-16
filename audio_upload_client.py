# -*- coding: utf-8 -*-
"""
音频上传客户端 - 火山 TOS 版本
将语音文件上传到火山引擎 TOS，返回可访问的 URL
"""

import os
import uuid
from datetime import datetime
from tos import TosClientV2
from tos.enum import HttpMethodType

# 火山 TOS 配置：从部署环境读取，不在源码保存凭据
TOS_ACCESS_KEY = os.environ.get("TOS_ACCESS_KEY", "").strip()
TOS_SECRET_KEY = os.environ.get("TOS_SECRET_KEY", "").strip()
TOS_ENDPOINT = os.environ.get("TOS_ENDPOINT", "tos-cn-beijing.volces.com").strip()
TOS_BUCKET = os.environ.get("TOS_BUCKET", "").strip()
TOS_REGION = os.environ.get("TOS_REGION", "cn-beijing").strip()

# 文件过期时间（默认 7 天，兼顾 ASR 访问和后台播放）
TOS_URL_EXPIRE_HOURS = 24 * 7


def _get_tos_client() -> TosClientV2:
    """获取 TOS 客户端实例"""
    if not all([TOS_ACCESS_KEY, TOS_SECRET_KEY, TOS_ENDPOINT, TOS_BUCKET]):
        raise RuntimeError("TOS 配置不完整，请检查环境变量")
    
    return TosClientV2(
        region=TOS_REGION,
        ak=TOS_ACCESS_KEY,
        sk=TOS_SECRET_KEY,
        endpoint=TOS_ENDPOINT
    )


def _generate_object_key(filename: str) -> str:
    """生成唯一的对象存储 key"""
    # 格式: audio/20240310/uuid_filename.wav
    date_str = datetime.now().strftime("%Y%m%d")
    unique_id = str(uuid.uuid4())[:8]
    return f"audio/{date_str}/{unique_id}_{filename}"


def upload_audio_bytes(filename: str, content_bytes: bytes, mime: str = "audio/wav") -> str:
    """
    上传音频字节到火山 TOS，返回永久 URL
    
    Args:
        filename: 文件名（如 voice.wav）
        content_bytes: 文件内容字节
        mime: MIME 类型
    
    Returns:
        可访问的 URL（永久 URL，桶已开启公共读）
    """
    client = _get_tos_client()
    object_key = _generate_object_key(filename)
    
    try:
        # 上传文件到 TOS
        client.put_object(
            bucket=TOS_BUCKET,
            key=object_key,
            content=content_bytes,
            content_type=mime
        )
        
        # 返回永久 URL（桶已开启公共读）
        # 格式: https://{bucket}.{endpoint}/{key}
        return f"https://{TOS_BUCKET}.{TOS_ENDPOINT}/{object_key}"
        
    except Exception as e:
        raise RuntimeError(f"上传音频失败: {e}")
