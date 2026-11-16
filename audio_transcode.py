# -*- coding: utf-8 -*-

import os
import subprocess
import tempfile
import shutil

from silk_decode import silk_bytes_to_pcm_s16le


def _find_ffmpeg() -> str:
    env_bin = (os.environ.get("FFMPEG_BIN") or "").strip()
    if env_bin:
        return env_bin
    p = shutil.which("ffmpeg")
    if p:
        return p
    raise FileNotFoundError("ffmpeg not found. Install ffmpeg or set env FFMPEG_BIN=/usr/bin/ffmpeg")


def _is_silk(b: bytes) -> bool:
    # 你的实际数据头：b"\x02#!SILK_V3..."
    return b"#!SILK_V3" in (b[:32] if b else b"")


def _pcm_s16le_to_wav_16k_mono(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """
    PCM(s16le, mono, sample_rate) -> WAV(16k, mono, s16)
    """
    ffmpeg = _find_ffmpeg()

    with tempfile.TemporaryDirectory() as d:
        pcm_path = os.path.join(d, "in.pcm")
        wav_path = os.path.join(d, "out.wav")

        with open(pcm_path, "wb") as f:
            f.write(pcm_bytes)

        subprocess.check_call(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-i",
                pcm_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                wav_path,
            ]
        )

        with open(wav_path, "rb") as f:
            return f.read()


def any_audio_bytes_to_wav_16k_mono(audio_bytes: bytes) -> bytes:
    """
    - SILK_V3：decoder -> PCM -> ffmpeg 输出 16k wav
    - 其他：ffmpeg 自动探测 -> 16k wav
    """
    if _is_silk(audio_bytes):
        pcm, sr = silk_bytes_to_pcm_s16le(audio_bytes)
        return _pcm_s16le_to_wav_16k_mono(pcm, sample_rate=sr)

    ffmpeg = _find_ffmpeg()
    with tempfile.TemporaryDirectory() as d:
        in_path = os.path.join(d, "in.bin")
        out_path = os.path.join(d, "out.wav")

        with open(in_path, "wb") as f:
            f.write(audio_bytes)

        subprocess.check_call(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                in_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                out_path,
            ]
        )

        with open(out_path, "rb") as f:
            return f.read()


def amr_bytes_to_wav_bytes(amr_bytes: bytes) -> bytes:
    # 兼容旧函数名：现在表示“把企微语音 bytes 转成可识别的 wav”
    return any_audio_bytes_to_wav_16k_mono(amr_bytes)