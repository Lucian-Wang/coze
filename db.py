# -*- coding: utf-8 -*-
"""SQLite 存储层：用户/消息/画像/标签/决策审计/系统配置 settings。

变更摘要：
- init_db() 不再在数据库初始化时创建 settings 表或插入默认 settings。
  （按需创建 settings 表：当第一次调用 set_setting 时会自动创建该表。）
- now_iso() 返回带 Asia/Shanghai 时区的 ISO（若可用 zoneinfo），避免 naive/aware 比较错误。
- _ensure_user() 优先从环境变量 OPT_IN_DEFAULT 读取默认 opt_in 值（避免依赖 settings 表）。
"""
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = os.environ.get("SQLITE_DB_PATH", "data/app.db")


def now_iso() -> str:
    """
    统一返回带时区信息的 ISO 字符串（优先 Asia/Shanghai）。
    如果系统支持 zoneinfo（py>=3.9），返回带 +08:00 的 iso；否则返回 naive iso 字符串（向后兼容）。
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def _today_ymd() -> str:
    """
    统一“今天”的日期口径为 Asia/Shanghai（与 scheduler.py 和 marketing.py 的时区一致）。
    避免服务器是 UTC 时导致 daily_limit / followup_count_today 跨天错位。
    """
    try:
        from zoneinfo import ZoneInfo  # py>=3.9

        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    except Exception:
        # fallback：使用服务器本地时区
        return datetime.now().strftime("%Y-%m-%d")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


_DEFAULT_SETTINGS: Dict[str, str] = {
    "marketing_enabled": "1",
    "opt_in_default": "1",
    "decision_idle_minutes": "10",
    "decision_scan_interval_seconds": "60",
    "quiet_hours_enabled": "1",
    "quiet_hours_start": "00:00",
    "quiet_hours_end": "08:00",
    "quiet_hours_defer_minutes": "5",
    "daily_followup_limit": "5",
    "min_followup_interval_minutes": "30",
    "won_threshold": "0.90",
    "max_messages_for_decision": "80",
    "max_days_for_decision": "14",
    "next_decision_cooldown_minutes_default": "30",
}


def _ensure_column(cur: sqlite3.Cursor, table: str, col: str, col_type: str) -> None:
    """
    SQLite 没有 ALTER TABLE ADD COLUMN IF NOT EXISTS，这里用 pragma table_info 做兼容迁移。
    """
    cols = [r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


def init_db() -> None:
    """
    初始化数据库 schema。
    注意：不再在初始化时创建 settings 表或插入默认 settings（避免将 settings 表作为运行时配置源）。
    如需 settings，调用 set_setting 会按需创建该表。
    """
    c = _conn()
    cur = c.cursor()

    # users 表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          user_id TEXT PRIMARY KEY,
          first_seen_at TEXT NOT NULL,
          last_inbound_at TEXT,
          last_outbound_at TEXT,

          opt_in INTEGER NOT NULL DEFAULT 1,
          opt_out_at TEXT,

          lifecycle_status TEXT NOT NULL DEFAULT 'active', -- active|won|lost
          intent_level TEXT NOT NULL DEFAULT 'unknown',     -- high|medium|low|unknown
          tier TEXT NOT NULL DEFAULT 'C',                   -- A|B|C

          profile_json TEXT,
          tags_json TEXT,
          profile_updated_at TEXT,

          last_decision_at TEXT,
          last_decision_json TEXT,

          next_decision_at TEXT,

          followup_count_date TEXT,
          followup_count_today INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # messages 表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          direction TEXT NOT NULL, -- in|out
          msg_type TEXT NOT NULL,  -- text|voice_meta|asr_text|image_meta|image_ocr|...
          text TEXT,
          created_at TEXT NOT NULL
        )
        """
    )

    # 兼容迁移：给 messages 增加结构化字段（不破坏旧数据）
    _ensure_column(cur, "messages", "source_type", "TEXT")
    _ensure_column(cur, "messages", "ref_id", "TEXT")
    _ensure_column(cur, "messages", "extra_json", "TEXT")

    # 索引（加速后台查询与决策扫描）
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_id_id ON messages(user_id, id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_id_created_at ON messages(user_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_id_msg_type ON messages(user_id, msg_type)")

    # followups 表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS followups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          reason TEXT NOT NULL,
          followup_type TEXT,
          confidence REAL,
          content TEXT,
          ark_decision_json TEXT,
          status TEXT NOT NULL, -- sent|skipped|failed
          error TEXT,
          created_at TEXT NOT NULL
        )
        """
    )

    # 不再创建 settings 表或写入默认 settings（已移除）。
    c.commit()
    c.close()


def _ensure_settings_table(conn: sqlite3.Connection) -> None:
    """
    如果需要写 settings，则按需创建 settings 表（不会在 init_db 自动执行）。
    """
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def get_setting(key: str) -> Optional[str]:
    """
    读取 settings 表（如果表不存在则返回 None）。
    """
    try:
        c = _conn()
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        c.close()
        return row["value"] if row else None
    except sqlite3.OperationalError:
        # settings 表不存在
        return None


def set_setting(key: str, value: str) -> None:
    """
    按需创建 settings 表，然后写入/更新键值。
    （之所以在这里按需创建，是为了避免在 init_db 时强制生成 settings 表。）
    """
    c = _conn()
    try:
        c.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now_iso()),
        )
        c.commit()
    except sqlite3.OperationalError:
        # settings 表不存在，按需创建再重试
        _ensure_settings_table(c)
        c.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now_iso()),
        )
        c.commit()
    finally:
        c.close()


def get_all_settings() -> Dict[str, str]:
    """
    返回 settings 全表（若表不存在返回空 dict）。
    """
    try:
        c = _conn()
        rows = c.execute("SELECT key,value FROM settings").fetchall()
        c.close()
        return {r["key"]: r["value"] for r in rows}
    except sqlite3.OperationalError:
        return {}


def _ensure_user(user_id: str) -> None:
    """
    确保 users 表存在该 user 行。opt_in 默认值现在优先从环境变量 OPT_IN_DEFAULT 读取（避免依赖 settings 表作为运行时 source）。
    """
    c = _conn()
    row = c.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        # 优先读取 ENV，否则使用内置默认（与 _DEFAULT_SETTINGS 相同）
        try:
            opt_in_default = int(os.environ.get("OPT_IN_DEFAULT", os.environ.get("MARKETING_OPT_IN_DEFAULT", _DEFAULT_SETTINGS["opt_in_default"])))
        except Exception:
            opt_in_default = int(_DEFAULT_SETTINGS["opt_in_default"])
        c.execute(
            "INSERT INTO users(user_id, first_seen_at, opt_in, lifecycle_status) VALUES(?,?,?,?)",
            (user_id, now_iso(), opt_in_default, "active"),
        )
    c.commit()
    c.close()


def touch_inbound(user_id: str) -> None:
    _ensure_user(user_id)
    c = _conn()
    c.execute("UPDATE users SET last_inbound_at=? WHERE user_id=?", (now_iso(), user_id))
    c.commit()
    c.close()


def touch_outbound(user_id: str) -> None:
    _ensure_user(user_id)
    c = _conn()
    c.execute("UPDATE users SET last_outbound_at=? WHERE user_id=?", (now_iso(), user_id))
    c.commit()
    c.close()


def insert_message(
    user_id: str,
    direction: str,
    msg_type: str,
    text: str,
    source_type: str = "",
    ref_id: str = "",
    extra_json: str = "",
) -> None:
    """
    统一消息落库：
    - msg_type: text|voice_meta|asr_text|image_meta|image_ocr|...
    - source_type: qiwe_text|qiwe_voice|qiwe_image|doubao_asr|ark_ocr|coze_reply|system...
    - ref_id: 关联键（如 fileId / guid+msgServerId / hash 等）
    - extra_json: 结构化信息（cloudUrl/voiceTime/wavUrl 等）
    """
    _ensure_user(user_id)
    c = _conn()
    c.execute(
        """
        INSERT INTO messages(user_id,direction,msg_type,text,source_type,ref_id,extra_json,created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (user_id, direction, msg_type, text, source_type, ref_id, extra_json, now_iso()),
    )
    c.commit()
    c.close()


def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_users_count(tier: Optional[str], intent: Optional[str], status: Optional[str]) -> int:
    """
    返回满足筛选条件的用户总数（用于分页）。
    """
    sql = "SELECT COUNT(1) AS n FROM users WHERE 1=1"
    args: List[Any] = []
    if tier:
        sql += " AND tier=?"
        args.append(tier)
    if intent:
        sql += " AND intent_level=?"
        args.append(intent)
    if status:
        sql += " AND lifecycle_status=?"
        args.append(status)

    c = _conn()
    n = c.execute(sql, tuple(args)).fetchone()["n"]
    c.close()
    return int(n or 0)


def list_users_paginated(tier: Optional[str], intent: Optional[str], status: Optional[str], page: int = 1, per_page: int = 50) -> Tuple[List[Dict[str, Any]], int]:
    """
    分页获取 users，同时返回总数 (users, total)
    page: 从 1 开始
    per_page: 每页大小
    """
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 50

    offset = (page - 1) * per_page

    sql = "SELECT * FROM users WHERE 1=1"
    args: List[Any] = []
    if tier:
        sql += " AND tier=?"
        args.append(tier)
    if intent:
        sql += " AND intent_level=?"
        args.append(intent)
    if status:
        sql += " AND lifecycle_status=?"
        args.append(status)

    sql += " ORDER BY COALESCE(last_inbound_at, first_seen_at) DESC LIMIT ? OFFSET ?"
    args.extend([per_page, offset])

    c = _conn()
    rows = c.execute(sql, tuple(args)).fetchall()
    c.close()
    users = [dict(r) for r in rows]
    total = get_users_count(tier, intent, status)
    return users, total


def list_users(tier: Optional[str], intent: Optional[str], status: Optional[str], limit: int = 2000) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM users WHERE 1=1"
    args: List[Any] = []
    if tier:
        sql += " AND tier=?"
        args.append(tier)
    if intent:
        sql += " AND intent_level=?"
        args.append(intent)
    if status:
        sql += " AND lifecycle_status=?"
        args.append(status)
    sql += " ORDER BY COALESCE(last_inbound_at, first_seen_at) DESC LIMIT ?"
    args.append(limit)

    c = _conn()
    rows = c.execute(sql, tuple(args)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_recent_messages(user_id: str, limit: int = 80) -> List[Dict[str, Any]]:
    c = _conn()
    rows = c.execute("SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    c.close()
    return [dict(r) for r in rows][::-1]


def get_recent_followups(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    c = _conn()
    rows = c.execute("SELECT * FROM followups WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_user_message_count(user_id: str) -> int:
    """获取用户的消息总数，用于判断是否是新用户"""
    c = _conn()
    row = c.execute("SELECT COUNT(*) as cnt FROM messages WHERE user_id=?", (user_id,)).fetchone()
    c.close()
    return row["cnt"] if row else 0


def set_user_opt_out(user_id: str) -> None:
    _ensure_user(user_id)
    c = _conn()
    c.execute("UPDATE users SET opt_in=0, opt_out_at=? WHERE user_id=?", (now_iso(), user_id))
    c.commit()
    c.close()


def force_user_decision_now(user_id: str) -> None:
    _ensure_user(user_id)
    c = _conn()
    c.execute("UPDATE users SET next_decision_at=NULL WHERE user_id=?", (user_id,))
    c.commit()
    c.close()


def update_user_from_ark_decision(
    user_id: str,
    intent_level: str,
    tier: str,
    lifecycle_status: str,
    profile_json: str,
    tags_json: str,
    ark_decision_json: str,
) -> None:
    _ensure_user(user_id)
    c = _conn()
    c.execute(
        """
        UPDATE users
        SET intent_level=?,
            tier=?,
            lifecycle_status=?,
            profile_json=?,
            tags_json=?,
            profile_updated_at=?,
            last_decision_at=?,
            last_decision_json=?
        WHERE user_id=?
        """,
        (
            intent_level,
            tier,
            lifecycle_status,
            profile_json,
            tags_json,
            now_iso(),
            now_iso(),
            ark_decision_json,
            user_id,
        ),
    )
    c.commit()
    c.close()


def set_next_decision_at(user_id: str, iso_time: Optional[str]) -> None:
    _ensure_user(user_id)
    c = _conn()
    c.execute("UPDATE users SET next_decision_at=? WHERE user_id=?", (iso_time, user_id))
    c.commit()
    c.close()


def bump_followup_count(user_id: str) -> None:
    _ensure_user(user_id)
    today = _today_ymd()
    c = _conn()
    row = c.execute("SELECT followup_count_date, followup_count_today FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        if row["followup_count_date"] != today:
            c.execute("UPDATE users SET followup_count_date=?, followup_count_today=1 WHERE user_id=?", (today, user_id))
        else:
            c.execute("UPDATE users SET followup_count_today=followup_count_today+1 WHERE user_id=?", (user_id,))
    c.commit()
    c.close()


def get_followup_count_today(user_id: str) -> int:
    _ensure_user(user_id)
    today = _today_ymd()
    c = _conn()
    row = c.execute("SELECT followup_count_date, followup_count_today FROM users WHERE user_id=?", (user_id,)).fetchone()
    c.close()
    if not row:
        return 0
    if row["followup_count_date"] != today:
        return 0
    return int(row["followup_count_today"] or 0)


def insert_followup_audit(
    user_id: str,
    reason: str,
    followup_type: str,
    confidence: float,
    content: str,
    ark_decision_json: str,
    status: str,
    error: str = "",
) -> None:
    _ensure_user(user_id)
    c = _conn()
    c.execute(
        """
        INSERT INTO followups(user_id,reason,followup_type,confidence,content,ark_decision_json,status,error,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (user_id, reason, followup_type, confidence, content, ark_decision_json, status, error, now_iso()),
    )
    c.commit()
    c.close()


def get_dashboard_stats() -> Dict[str, Any]:
    """旧名字：仪表盘统计。"""
    c = _conn()
    stats: Dict[str, Any] = {}
    stats["users_total"] = c.execute("SELECT COUNT(1) AS n FROM users").fetchone()["n"]
    stats["users_won"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE lifecycle_status='won'").fetchone()["n"]
    stats["tier_A"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE tier='A'").fetchone()["n"]
    stats["tier_B"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE tier='B'").fetchone()["n"]
    stats["tier_C"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE tier='C'").fetchone()["n"]
    stats["intent_high"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE intent_level='high'").fetchone()["n"]
    stats["intent_medium"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE intent_level='medium'").fetchone()["n"]
    stats["intent_low"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE intent_level='low'").fetchone()["n"]
    c.close()
    return stats


def get_admin_stats() -> Dict[str, Any]:
    """
    新名字：给 admin.py 用的统计接口（兼容你当前 admin.py 调用）。
    直接复用 get_dashboard_stats 的结果，并补充几个常用字段。
    """
    stats = get_dashboard_stats()

    # 补一些常见统计，便于后台展示（即使模板不用也无害)
    c = _conn()
    try:
        stats["users_active"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE lifecycle_status='active'").fetchone()["n"]
        stats["users_lost"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE lifecycle_status='lost'").fetchone()["n"]
        stats["intent_unknown"] = c.execute("SELECT COUNT(1) AS n FROM users WHERE intent_level='unknown'").fetchone()["n"]
    finally:
        c.close()

    return stats