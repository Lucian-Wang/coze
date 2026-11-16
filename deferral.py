# -*- coding: utf-8 -*-
"""
识别用户“晚点再说/明天再说/过几天再说”等推迟沟通意图，并返回建议的延后分钟数。
规则优先，稳定可控。

注意：这里不会直接发送消息，只用于推迟沉默运营的评估/触达。
"""

import re
from typing import Optional

RE_TOMORROW = re.compile(r"(明天|明儿|明晚|明早|明天下午|明天晚上)")
RE_LATER = re.compile(r"(晚点|一会儿|等会|稍后|稍等|过会|等一下|等下再说|晚点再说)")
RE_DAYS = re.compile(r"(过几天|改天|下次|以后再说)")
RE_HOURS = re.compile(r"(\d+)\s*(小时|h|H)")
RE_MINUTES = re.compile(r"(\d+)\s*(分钟|min|MIN)")


def infer_deferral_minutes(text: str) -> Optional[int]:
    """
    返回建议推迟分钟数：
    - 识别到明确数字：如 “2小时后再说” → 120
    - 明天：默认 24h
    - 过几天/改天：默认 72h
    - 晚点/稍后：默认 60min
    """
    t = (text or "").strip()
    if not t:
        return None

    m = RE_HOURS.search(t)
    if m:
        try:
            hours = int(m.group(1))
            if 1 <= hours <= 240:
                return hours * 60
        except Exception:
            pass

    m = RE_MINUTES.search(t)
    if m:
        try:
            mins = int(m.group(1))
            if 5 <= mins <= 7 * 24 * 60:
                return mins
        except Exception:
            pass

    if RE_TOMORROW.search(t):
        return 24 * 60

    if RE_DAYS.search(t):
        return 72 * 60

    if RE_LATER.search(t):
        return 60

    return None