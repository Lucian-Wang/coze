# -*- coding: utf-8 -*-

import os
import subprocess
import tempfile
from typing import Tuple


def _decoder_bin() -> str:
    p = (os.environ.get("SILK_DECODER_BIN") or "").strip()
    return p or "/opt/silk-v3-decoder/silk/decoder"


def _pcm_sample_rate() -> int:
    v = (os.environ.get("SILK_PCM_SAMPLE_RATE") or "").strip()
    return int(v) if v else 24000


def silk_bytes_to_pcm_s16le(silk_bytes: bytes) -> Tuple[bytes, int]:
    """
    使用 kn007/silk-v3-decoder 的 decoder：
    input:  SILK_V3 bytes
    output: PCM s16le (mono) bytes + sample_rate(默认 24000)

    decoder 调用方式（你已验证可用）：
      /opt/silk-v3-decoder/silk/decoder in.silk out.pcm
    """
    decoder = _decoder_bin()
    sr = _pcm_sample_rate()

    with tempfile.TemporaryDirectory() as d:
        in_path = os.path.join(d, "in.silk")
        out_path = os.path.join(d, "out.pcm")

        with open(in_path, "wb") as f:
            f.write(silk_bytes)

        subprocess.check_call([decoder, in_path, out_path])

        with open(out_path, "rb") as f:
            return f.read(), sr