# -*- coding: utf-8 -*-
"""
沉默运营任务（合并：保留原有决策逻辑 + 后台异步执行 Ark）
- 行为保持你要求的“只在用户静默 idle_minutes 后才触发 Ark；若自上次决策无新消息则不重复调用”等规则
- 为避免 scheduler 被 Ark 网络/模型延时阻塞：scan 阶段做前置检查并把需要 Ark 的用户提交到后台线程池执行
- 后台 worker 完成后负责写回用户画像/next_decision/followups/messages 等
- 配置来自 ENV（支持 KEY 与 MARKETING_KEY 两种形式）
- 控制后台并发：MARKETING_ARK_WORKERS（默认 4）
"""
import os
import json
import logging
import re
import random
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List

import db
from ark_decider import ark_decide
from coze_marketing import coze_generate_followup_message
from qiwe_client import send_reply_to_qiwe, get_env_guid_for_marketing
from deferral import infer_deferral_minutes

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("marketing")

# 后台 Ark 线程池（懒初始化）
_ARK_EXECUTOR: Optional[ThreadPoolExecutor] = None


def _get_ark_executor() -> ThreadPoolExecutor:
    global _ARK_EXECUTOR
    if _ARK_EXECUTOR is None:
        try:
            wk = int(os.environ.get("MARKETING_ARK_WORKERS", "4"))
        except Exception:
            wk = 4
        _ARK_EXECUTOR = ThreadPoolExecutor(max_workers=wk)
        logger.info("marketing ark executor created max_workers=%s", wk)
    return _ARK_EXECUTOR


# 可配置的“免打扰/排除”关键词
_OPT_OUT_PATTERNS = ("退订", "别发了", "不再联系", "stop", "unsubscribe")


def should_opt_out_by_text(text: str) -> bool:
    t = (text or "").lower()
    return any(p.lower() in t for p in _OPT_OUT_PATTERNS)


def _now_local() -> datetime:
    """
    统一“当前时间”的时区口径：优先 Asia/Shanghai（带 tzinfo）
    """
    try:
        from zoneinfo import ZoneInfo  # py>=3.9
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        logger.warning("zoneinfo Asia/Shanghai not available; fallback to datetime.now() (server local timezone)")
        return datetime.now()


def _iso_to_dt(s: Optional[str]) -> Optional[datetime]:
    """
    更健壮的时间解析：支持带 Z / 带偏移的 ISO，或常见无时区格式。
    解析出的 naive datetime 将尝试绑定 Asia/Shanghai（若 zoneinfo 可用）。
    """
    if not s:
        return None
    ss = str(s).strip()
    if not ss:
        return None

    dt = None
    try:
        dt = datetime.fromisoformat(ss.replace("Z", "+00:00"))
    except Exception:
        dt = None

    if dt is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(ss, fmt)
                break
            except Exception:
                continue

    if dt is None:
        return None

    if dt.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except Exception:
            # 区域信息不可用时保留 naive（保持向后兼容）
            pass

    return dt


def _dt_to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---- 仅从 ENV 读取配置（支持 KEY 与 MARKETING_KEY） ----
def _env_only(key: str) -> Optional[str]:
    env_names = [key.upper(), f"MARKETING_{key.upper()}"]
    for name in env_names:
        v = os.environ.get(name)
        if v is not None and v != "":
            return v
    return None


def _get_int(key: str, default: int) -> int:
    v = _env_only(key)
    try:
        return int(float(v)) if v not in (None, "") else default
    except Exception:
        return default


def _get_float(key: str, default: float) -> float:
    v = _env_only(key)
    try:
        return float(v) if v not in (None, "") else default
    except Exception:
        return default


def _get_bool(key: str, default: bool) -> bool:
    v = _env_only(key)
    if v is None or v == "":
        return default
    return str(v).lower() in ("1", "true", "yes", "y", "on")
# --------------------------------------------------------------------


def _parse_hhmm(s: str) -> Tuple[int, int]:
    parts = (s or "").split(":")
    if len(parts) != 2:
        return 0, 0
    return int(parts[0]), int(parts[1])


def _in_quiet(now: datetime, start: str, end: str) -> bool:
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    smin = sh * 60 + sm
    emin = eh * 60 + em
    cmin = now.hour * 60 + now.minute

    if smin == emin:
        return False
    if smin < emin:
        return smin <= cmin < emin
    return cmin >= smin or cmin < emin


def _next_quiet_end(now: datetime, end: str) -> datetime:
    eh, em = _parse_hhmm(end)
    candidate = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def _normalize_tags(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            s = str(x).strip()
            if s:
                out.append(s)
        return out

    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            obj = json.loads(s)
            if isinstance(obj, list):
                return [str(x).strip() for x in obj if str(x).strip()]
        except Exception:
            pass

        parts = re.split(r"[,\n、]+", s)
        return [p.strip() for p in parts if p.strip()]

    s2 = str(v).strip()
    return [s2] if s2 else []


def _last_user_expression_text(messages: List[Dict[str, Any]]) -> str:
    """
    获取用户最近一次“真实表达”的文本，用于 defer 识别。
    """
    if not messages:
        return ""

    for m in reversed(messages):
        if (m.get("direction") or "").strip() != "in":
            continue
        mt = (m.get("msg_type") or "").strip()
        if mt not in ("text", "asr_text", "image_ocr"):
            continue
        txt = (m.get("text") or "").strip()
        if txt:
            return txt

    return ""


def _rand_next_day_9_10(now: datetime) -> datetime:
    """
    生成次日 09:00:00 到 09:59:59 之间的随机时间（保留时区）
    """
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        tz = None

    next_day = (now + timedelta(days=1)).date()
    hour = 9
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    dt = datetime(
        year=next_day.year,
        month=next_day.month,
        day=next_day.day,
        hour=hour,
        minute=minute,
        second=second,
    )
    if tz is not None:
        dt = dt.replace(tzinfo=tz)
    return dt


# ----------------- 后台 Ark worker（原同步逻辑搬到这里） -----------------
def _ark_worker(user_id: str, msgs: List[Dict[str, Any]], is_quiet: bool, quiet_desc: str, daily_limit: int, cnt_today: int):
    """
    后台线程执行 Ark 决策与后续处理（包含更新用户、生成文案、发送、审计）
    该函数保留了原来同步版里所有后处理逻辑（尽量不改变业务判定）
    """
    now = _now_local()
    try:
        ark_obj, _ark_text, _ark_raw = ark_decide(
            user_id=user_id,
            messages=msgs,
            is_quiet_hours=is_quiet,
            quiet_hours_desc=quiet_desc,
            daily_limit=daily_limit,
            followup_count_today=cnt_today,
        )
    except Exception as e:
        logger.exception("ark_decide failed in background: user=%s err=%s", user_id, e)
        # 写审计
        try:
            db.insert_followup_audit(
                user_id=user_id,
                reason="ark_decide_error",
                followup_type="none",
                confidence=0.0,
                content="",
                ark_decision_json="{}",
                status="failed",
                error=str(e),
            )
        except Exception:
            logger.exception("insert followup audit failed after ark error user=%s", user_id)
        return

    try:
        # 解析 ark 返回
        has_purchased = bool(ark_obj.get("has_purchased"))
        purchase_conf = float(ark_obj.get("purchase_confidence") or 0.0)
        intent_level = (ark_obj.get("intent_level") or "unknown").lower()
        tier = (ark_obj.get("tier") or "C").upper()
        should_followup = bool(ark_obj.get("should_followup"))
        followup_type = (ark_obj.get("followup_type") or "none").lower()
        reason = (ark_obj.get("reason") or "ark_decision").strip()
        profile = ark_obj.get("profile") or {}
        tags = _normalize_tags(ark_obj.get("tags"))

        lifecycle = "active"
        if has_purchased and purchase_conf >= _get_float("won_threshold", 0.90):
            lifecycle = "won"
            should_followup = False
            followup_type = "none"

        # 更新用户画像/标签/决策审计
        try:
            db.update_user_from_ark_decision(
                user_id=user_id,
                intent_level=intent_level if intent_level in ("high", "medium", "low", "unknown") else "unknown",
                tier=tier if tier in ("A", "B", "C") else "C",
                lifecycle_status=lifecycle,
                profile_json=json.dumps(profile, ensure_ascii=False),
                tags_json=json.dumps(tags, ensure_ascii=False),
                ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
            )
        except Exception:
            logger.exception("update_user_from_ark_decision failed: user=%s", user_id)

        # 计算 cooldown（下次评估冷却）并写入 next_decision_at
        cooldown_default = _get_int("next_decision_cooldown_minutes_default", 30)
        try:
            cm = int(ark_obj.get("cooldown_minutes") or 0)
        except Exception:
            cm = 0
        cooldown = cm if cm > 0 else cooldown_default
        if cooldown < 5:
            cooldown = 5
        next_decision_dt = now + timedelta(minutes=cooldown)
        logger.info("background: set_next_decision_at (ark_cooldown) user=%s now=%s cooldown=%s target=%s", user_id, now, cooldown, next_decision_dt)
        db.set_next_decision_at(user_id, _dt_to_iso(next_decision_dt))

        if lifecycle == "won":
            # 已成交，不再跟进
            return

        if not should_followup:
            # 仅更新画像/决策，不发送
            db.insert_followup_audit(
                user_id=user_id,
                reason=reason,
                followup_type=followup_type,
                confidence=purchase_conf,
                content="",
                ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                status="skipped",
                error="ark decided no followup",
            )
            return

        # 若需要跟进，检查今日配额（决策和发送之间可能有并发）
        cnt_today2 = db.get_followup_count_today(user_id)
        if cnt_today2 >= daily_limit:
            logger.info("background: skip send due to daily limit after decision: user=%s cnt_today2=%s", user_id, cnt_today2)
            db.insert_followup_audit(
                user_id=user_id,
                reason=reason,
                followup_type=followup_type,
                confidence=purchase_conf,
                content="",
                ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                status="skipped",
                error="daily_limit reached after decision",
            )
            return

        # 生成跟进文案
        msg = coze_generate_followup_message(user_id, ark_obj, msgs).strip()
        if not msg:
            logger.info("background: coze returned empty for user=%s", user_id)
            db.insert_followup_audit(
                user_id=user_id,
                reason=reason,
                followup_type=followup_type,
                confidence=purchase_conf,
                content="",
                ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                status="skipped",
                error="coze_empty",
            )
            return

        # 获取 guid（营销使用）
        try:
            guid = get_env_guid_for_marketing()
        except Exception as e:
            db.insert_followup_audit(
                user_id=user_id,
                reason=reason,
                followup_type=followup_type,
                confidence=purchase_conf,
                content=msg,
                ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                status="failed",
                error=f"missing marketing guid: {str(e)}",
            )
            logger.warning("background send skipped: missing guid user=%s err=%s", user_id, e)
            return

        # 发送
        try:
            send_reply_to_qiwe(user_id, msg, guid)
            db.touch_outbound(user_id)
            db.insert_message(
                user_id=user_id,
                direction="out",
                msg_type="text",
                text=msg,
                source_type="marketing_followup",
                ref_id="",
                extra_json="",
            )
            db.bump_followup_count(user_id)
            db.insert_followup_audit(
                user_id=user_id,
                reason=reason,
                followup_type=followup_type,
                confidence=purchase_conf,
                content=msg,
                ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                status="sent",
                error="",
            )
            logger.info("background: sent marketing followup user=%s", user_id)
        except Exception as e:
            logger.exception("background send failed user=%s err=%s", user_id, e)
            db.insert_followup_audit(
                user_id=user_id,
                reason=reason,
                followup_type=followup_type,
                confidence=purchase_conf,
                content=msg,
                ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                status="failed",
                error=str(e),
            )

    except Exception as e:
        logger.exception("background processing failed user=%s err=%s", user_id, e)
        try:
            db.insert_followup_audit(
                user_id=user_id,
                reason="background_processing_error",
                followup_type="none",
                confidence=0.0,
                content="",
                ark_decision_json=json.dumps(ark_obj if 'ark_obj' in locals() else {}, ensure_ascii=False),
                status="failed",
                error=str(e),
            )
        except Exception:
            pass


# ----------------- 主扫描逻辑（保留你的业务规则） -----------------
def scan_and_process_candidates() -> None:
    """
    扫描主逻辑（前置检查 + 提交后台任务）：
    - 保留所有你在同步版里改动的判定逻辑
    - 通过 executor.submit 把 Ark 的耗时任务移到后台执行
    """
    if not _get_bool("marketing_enabled", True):
        logger.info("scan: marketing_enabled=0, skip")
        return

    quiet_enabled = _get_bool("quiet_hours_enabled", True)
    quiet_start = _env_only("quiet_hours_start") or "00:00"
    quiet_end = _env_only("quiet_hours_end") or "08:00"
    quiet_defer = _get_int("quiet_hours_defer_minutes", 5)

    idle_minutes = _get_int("decision_idle_minutes", 10)
    daily_limit = _get_int("daily_followup_limit", 2)
    min_interval = _get_int("min_followup_interval_minutes", 30)
    cooldown_default = _get_int("next_decision_cooldown_minutes_default", 30)
    won_threshold = _get_float("won_threshold", 0.90)
    max_msgs = _get_int("max_messages_for_decision", 80)
    max_days = _get_int("max_days_for_decision", 14)
    mark_lost_days = _get_int("mark_lost_after_days", 3)

    now = _now_local()
    users = db.list_users(tier=None, intent=None, status="active", limit=5000)

    stats = {
        "users_total": len(users),
        "skip_optout": 0,
        "skip_won": 0,
        "skip_no_last_in": 0,
        "skip_idle_not_enough": 0,
        "skip_next_decision_future": 0,
        "skip_quiet_hours": 0,
        "skip_min_interval": 0,
        "skip_daily_limit": 0,
        "skip_user_deferral": 0,
        "skip_missing_guid": 0,
        "ark_error": 0,
        "ark_no_followup": 0,
        "coze_empty": 0,
        "sent": 0,
        "send_failed": 0,
        "marked_won": 0,
        "marked_lost": 0,
        "candidates": 0,
    }

    for u in users:
        user_id = u["user_id"]
        try:
            # opt-out / won
            if int(u.get("opt_in") or 0) != 1 or u.get("opt_out_at"):
                stats["skip_optout"] += 1
                continue
            if (u.get("lifecycle_status") or "").lower() == "won":
                stats["skip_won"] += 1
                continue

            # last inbound
            last_in = _iso_to_dt(u.get("last_inbound_at"))
            if not last_in:
                stats["skip_no_last_in"] += 1
                continue

            # mark lost
            if (now - last_in).total_seconds() >= mark_lost_days * 24 * 3600:
                try:
                    intent_level = (u.get("intent_level") or "unknown").lower()
                    tier = (u.get("tier") or "C").upper()
                    profile_json = u.get("profile_json") or "{}"
                    tags_json = u.get("tags_json") or "[]"
                    ark_decision_json = u.get("last_decision_json") or "{}"
                    db.update_user_from_ark_decision(
                        user_id=user_id,
                        intent_level=intent_level,
                        tier=tier,
                        lifecycle_status="lost",
                        profile_json=profile_json,
                        tags_json=tags_json,
                        ark_decision_json=ark_decision_json,
                    )
                    stats["marked_lost"] += 1
                    logger.info("mark user lost: user=%s last_in=%s mark_lost_days=%s", user_id, last_in, mark_lost_days)
                except Exception:
                    logger.exception("mark lost failed: user=%s", user_id)
                continue

            # idle 判断
            if (now - last_in).total_seconds() < idle_minutes * 60:
                stats["skip_idle_not_enough"] += 1
                continue

            # next_decision future 判断
            nda = _iso_to_dt(u.get("next_decision_at"))
            if nda and nda > now:
                stats["skip_next_decision_future"] += 1
                continue

            # last_decision >= last_in：表示自上次决策后无新 inbound，不重复调用 Ark
            last_decision = _iso_to_dt(u.get("last_decision_at"))
            if last_decision and last_decision >= last_in:
                if (now - last_decision).total_seconds() < idle_minutes * 60:
                    target_dt = last_decision + timedelta(minutes=idle_minutes)
                    if target_dt <= now:
                        target_dt = now + timedelta(minutes=idle_minutes)
                    logger.info("skip: no new inbound since last_decision. user=%s last_decision=%s now=%s set_next=%s reason=%s",
                                user_id, last_decision, now, target_dt, "no_new_inbound_wait_idle")
                    db.set_next_decision_at(user_id, _dt_to_iso(target_dt))
                    stats["skip_idle_not_enough"] += 1
                    continue
                # else：已超过 idle_minutes，自上次决策后可再次评估

            stats["candidates"] += 1

            # quiet hours
            is_quiet = quiet_enabled and _in_quiet(now, quiet_start, quiet_end)
            if is_quiet:
                end_dt = _next_quiet_end(now, quiet_end)
                target_dt = end_dt + timedelta(minutes=quiet_defer)
                logger.info("skip quiet hours: user=%s now=%s set_next=%s reason=%s", user_id, now, target_dt, "quiet_hours")
                db.set_next_decision_at(user_id, _dt_to_iso(target_dt))
                stats["skip_quiet_hours"] += 1
                continue

            # min_interval 以 last_out + min_interval 为基准
            last_out = _iso_to_dt(u.get("last_outbound_at"))
            if last_out and (now - last_out).total_seconds() < min_interval * 60:
                target_dt = last_out + timedelta(minutes=min_interval)
                if target_dt <= now:
                    target_dt = now + timedelta(minutes=min_interval)
                logger.info("skip min_interval: user=%s last_out=%s now=%s set_next=%s reason=%s min_interval=%s",
                            user_id, last_out, now, target_dt, "min_followup_interval", min_interval)
                db.set_next_decision_at(user_id, _dt_to_iso(target_dt))
                stats["skip_min_interval"] += 1
                continue

            # daily limit
            cnt_today = db.get_followup_count_today(user_id)
            if cnt_today >= daily_limit:
                target_dt = _rand_next_day_9_10(now)
                logger.info("skip daily limit: user=%s cnt_today=%s daily_limit=%s set_next=%s reason=%s",
                            user_id, cnt_today, daily_limit, target_dt, "daily_limit")
                db.insert_followup_audit(
                    user_id=user_id,
                    reason="daily_limit",
                    followup_type="none",
                    confidence=0.0,
                    content="",
                    ark_decision_json="{}",
                    status="skipped",
                    error="超过每日上限",
                )
                db.set_next_decision_at(user_id, _dt_to_iso(target_dt))
                stats["skip_daily_limit"] += 1
                continue

            # 用户主动 defer 识别（text/asr_text/image_ocr）
            msgs = db.get_recent_messages(user_id, limit=max_msgs)
            if max_days > 0:
                cutoff = now - timedelta(days=max_days)
                msgs = [m for m in msgs if _iso_to_dt(m.get("created_at")) and _iso_to_dt(m.get("created_at")) >= cutoff]

            last_user_text = _last_user_expression_text(msgs)
            defer_mins = infer_deferral_minutes(last_user_text) if last_user_text else None
            if defer_mins:
                target_dt = now + timedelta(minutes=defer_mins)
                logger.info("user requested deferral: user=%s last_user_text=%s defer_mins=%s set_next=%s",
                            user_id, last_user_text, defer_mins, target_dt)
                db.set_next_decision_at(user_id, _dt_to_iso(target_dt))
                db.insert_followup_audit(
                    user_id=user_id,
                    reason="user_requested_deferral",
                    followup_type="none",
                    confidence=0.0,
                    content="",
                    ark_decision_json="{}",
                    status="skipped",
                    error=f"用户要求稍后联系，已推迟 {defer_mins} 分钟",
                )
                stats["skip_user_deferral"] += 1
                continue

            # 到这里通过所有前置检查，提交后台 Ark 任务（非阻塞）
            logger.info("submit ark task for user=%s now=%s", user_id, now)

            # soft lock：避免短时间内重复提交；后台 worker 会在完成时更新真实 next_decision
            try:
                db.set_next_decision_at(user_id, _dt_to_iso(now + timedelta(seconds=10)))
            except Exception:
                logger.exception("failed to set soft lock next_decision for user=%s", user_id)

            try:
                _get_ark_executor().submit(
                    _ark_worker,
                    user_id,
                    msgs,
                    is_quiet,
                    f"{quiet_start}-{quiet_end}" if quiet_enabled else "未开启",
                    daily_limit,
                    cnt_today,
                )
            except Exception as e:
                logger.exception("submit ark task failed: user=%s err=%s", user_id, e)
                stats["ark_error"] += 1
                try:
                    db.insert_followup_audit(
                        user_id=user_id,
                        reason="ark_submit_failed",
                        followup_type="none",
                        confidence=0.0,
                        content="",
                        ark_decision_json="{}",
                        status="failed",
                        error=str(e),
                    )
                except Exception:
                    logger.exception("insert followup audit failed after submit error user=%s", user_id)

        except Exception:
            stats["send_failed"] += 1

    logger.info(
        "scan stats: total=%s candidates=%s sent=%s ark_no_followup=%s ark_error=%s coze_empty=%s "
        "skip_quiet=%s skip_min_interval=%s skip_daily_limit=%s skip_deferral=%s skip_missing_guid=%s skip_idle=%s skip_next=%s marked_lost=%s",
        stats["users_total"],
        stats["candidates"],
        stats["sent"],
        stats["ark_no_followup"],
        stats["ark_error"],
        stats["coze_empty"],
        stats["skip_quiet_hours"],
        stats["skip_min_interval"],
        stats["skip_daily_limit"],
        stats["skip_user_deferral"],
        stats["skip_missing_guid"],
        stats["skip_idle_not_enough"],
        stats["skip_next_decision_future"],
        stats["marked_lost"],
    )


# ----------------- 手动评估接口（与以前保持一致） -----------------
def evaluate_user_now(user_id: str, allow_send: bool = False) -> Dict[str, Any]:
    """
    手动立即评估（可选发送）。
    行为与 scan_and_process_candidates 的决策逻辑一致，但直接触发 ark_decide（同步调用）。
    """
    quiet_enabled = _get_bool("quiet_hours_enabled", True)
    quiet_start = _env_only("quiet_hours_start") or "00:00"
    quiet_end = _env_only("quiet_hours_end") or "08:00"
    daily_limit = _get_int("daily_followup_limit", 2)
    won_threshold = _get_float("won_threshold", 0.90)

    max_msgs = _get_int("max_messages_for_decision", 80)
    max_days = _get_int("max_days_for_decision", 14)

    now = _now_local()

    u = db.get_user(user_id)
    if not u:
        return {"ok": False, "error": "user_not_found"}

    if int(u.get("opt_in") or 0) != 1 or u.get("opt_out_at"):
        return {"ok": False, "error": "opt_out_or_not_opt_in"}

    if (u.get("lifecycle_status") or "").lower() == "won":
        return {"ok": False, "error": "already_won"}

    msgs = db.get_recent_messages(user_id, limit=max_msgs)
    if max_days > 0:
        cutoff = now - timedelta(days=max_days)
        msgs = [m for m in msgs if _iso_to_dt(m.get("created_at")) and _iso_to_dt(m.get("created_at")) >= cutoff]

    is_quiet = quiet_enabled and _in_quiet(now, quiet_start, quiet_end)
    quiet_desc = f"{quiet_start}-{quiet_end}" if quiet_enabled else "未开启"

    cnt_today = db.get_followup_count_today(user_id)

    ark_obj, _ark_text, _ark_raw = ark_decide(
        user_id=user_id,
        messages=msgs,
        is_quiet_hours=is_quiet,
        quiet_hours_desc=quiet_desc,
        daily_limit=daily_limit,
        followup_count_today=cnt_today,
    )

    has_purchased = bool(ark_obj.get("has_purchased"))
    purchase_conf = float(ark_obj.get("purchase_confidence") or 0.0)
    intent_level = (ark_obj.get("intent_level") or "unknown").lower()
    tier = (ark_obj.get("tier") or "C").upper()
    profile = ark_obj.get("profile") or {}
    tags = _normalize_tags(ark_obj.get("tags"))

    lifecycle = "active"
    if has_purchased and purchase_conf >= won_threshold:
        lifecycle = "won"

    db.update_user_from_ark_decision(
        user_id=user_id,
        intent_level=intent_level if intent_level in ("high", "medium", "low", "unknown") else "unknown",
        tier=tier if tier in ("A", "B", "C") else "C",
        lifecycle_status=lifecycle,
        profile_json=json.dumps(profile, ensure_ascii=False),
        tags_json=json.dumps(tags, ensure_ascii=False),
        ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
    )

    cooldown_default = _get_int("next_decision_cooldown_minutes_default", 30)
    try:
        cm = int(ark_obj.get("cooldown_minutes") or 0)
    except Exception:
        cm = 0
    cooldown = cm if cm > 0 else cooldown_default
    if cooldown < 5:
        cooldown = 5
    db.set_next_decision_at(user_id, _dt_to_iso(now + timedelta(minutes=cooldown)))

    sent = False
    sent_error = ""

    if allow_send and bool(ark_obj.get("should_followup")) and lifecycle != "won":
        try:
            msg = coze_generate_followup_message(user_id, ark_obj, msgs).strip()
            if msg:
                guid = get_env_guid_for_marketing()
                send_reply_to_qiwe(user_id, msg, guid)

                db.touch_outbound(user_id)
                db.insert_message(
                    user_id=user_id,
                    direction="out",
                    msg_type="text",
                    text=msg,
                    source_type="marketing_followup",
                    ref_id="",
                    extra_json="",
                )
                db.bump_followup_count(user_id)

                db.insert_followup_audit(
                    user_id=user_id,
                    reason=(ark_obj.get("reason") or "manual_evaluate_now").strip(),
                    followup_type=(ark_obj.get("followup_type") or "none").lower(),
                    confidence=purchase_conf,
                    content=msg,
                    ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                    status="sent",
                    error="",
                )
                logger.info("manual evaluate audit inserted: user_id=%s status=sent", user_id)
                sent = True
            else:
                db.insert_followup_audit(
                    user_id=user_id,
                    reason=(ark_obj.get("reason") or "manual_evaluate_now").strip(),
                    followup_type=(ark_obj.get("followup_type") or "none").lower(),
                    confidence=purchase_conf,
                    content="",
                    ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                    status="skipped",
                    error="allow_send=1 但生成文案为空，未发送",
                )
                logger.info("manual evaluate audit inserted: user_id=%s status=skipped (empty msg)", user_id)
        except Exception as e:
            sent_error = str(e)
            db.insert_followup_audit(
                user_id=user_id,
                reason=(ark_obj.get("reason") or "manual_evaluate_now").strip(),
                followup_type=(ark_obj.get("followup_type") or "none").lower(),
                confidence=purchase_conf,
                content="",
                ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
                status="failed",
                error=sent_error,
            )
            logger.info("manual evaluate audit inserted: user_id=%s status=failed", user_id)
    else:
        db.insert_followup_audit(
            user_id=user_id,
            reason=("manual_evaluate_now: " + (ark_obj.get("reason") or "")).strip(),
            followup_type=(ark_obj.get("followup_type") or "none").lower(),
            confidence=purchase_conf,
            content="",
            ark_decision_json=json.dumps(ark_obj, ensure_ascii=False),
            status="skipped",
            error="allow_send=0（仅更新画像/标签/决策，不发送）",
        )
        logger.info("manual evaluate audit inserted: user_id=%s status=skipped (allow_send=0)", user_id)

    return {"ok": True, "sent": sent, "sent_error": sent_error, "ark": ark_obj}