# -*- coding: utf-8 -*-
"""
admin.py（/admin/users 列表增加 tags 展示 + 默认显示所有用户 + 分页）
- 从 users.tags_json 解析 tags；若为空则从 last_decision_json.tags 兜底
- 为避免一次性把大量用户 JSON 解析拖慢页面，列表页使用分页（默认每页 50）
"""
import base64
import os
import json
import threading
import logging
from typing import Any, Dict, Optional, List

from flask import request, jsonify, render_template, redirect, url_for

import db
from marketing import evaluate_user_now

logger = logging.getLogger("admin")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def _basic_auth_required() -> Optional[Any]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return jsonify({"error": "Unauthorized"}), 401, {"WWW-Authenticate": 'Basic realm="admin"'}
    try:
        b64 = auth.split(" ", 1)[1].strip()
        raw = base64.b64decode(b64).decode("utf-8")
        username, password = raw.split(":", 1)
    except Exception:
        return jsonify({"error": "Unauthorized"}), 401, {"WWW-Authenticate": 'Basic realm="admin"'}

    if not ADMIN_PASSWORD or username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        return jsonify({"error": "Unauthorized"}), 401, {"WWW-Authenticate": 'Basic realm="admin"'}
    return None


def _safe_json(s: Optional[str], default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _pretty_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return "{}"


def _extract_user_tags_for_list(u: Dict[str, Any], max_tags: int = 3) -> List[str]:
    """
    /admin/users 列表页用：取用户 tags
    - 优先 users.tags_json
    - 兜底 last_decision_json.tags
    """
    tags = _safe_json(u.get("tags_json"), [])
    if not tags:
        decision = _safe_json(u.get("last_decision_json"), {})
        tags = decision.get("tags") or []
    if not isinstance(tags, list):
        return []
    tags2 = []
    for t in tags:
        s = str(t).strip()
        if s:
            tags2.append(s)
    return tags2[:max_tags]


def register_admin_routes(app):
    @app.route("/admin", methods=["GET"])
    def admin_index():
        r = _basic_auth_required()
        if r:
            return r
        stats = db.get_admin_stats()
        stats["updated_at"] = db.now_iso() if hasattr(db, "now_iso") else ""
        return render_template("admin_index.html", stats=stats)

    # 注意：已移除 /admin/settings 页面与功能（防止后台修改配置/环境变量）
    # 如需配置请通过服务器 .env / 环境变量调整，再重启服务。

    @app.route("/admin/users", methods=["GET"])
    def admin_users():
        r = _basic_auth_required()
        if r:
            return r

        # 过滤条件
        tier = (request.args.get("tier") or "").strip() or None
        intent = (request.args.get("intent") or "").strip() or None
        # 改动：默认不强制为 "active"，若不传 status 则为 None（显示所有）
        status = (request.args.get("status") or "").strip() or None

        # 分页参数
        try:
            page = int(request.args.get("page") or "1")
        except Exception:
            page = 1
        if page < 1:
            page = 1

        try:
            per_page = int(request.args.get("per_page") or "50")
        except Exception:
            per_page = 50
        # 限制每页最大值，防止一次性加载过多
        if per_page < 1:
            per_page = 1
        if per_page > 500:
            per_page = 500

        # 后端分页查询（返回 users 列表与总数）
        users, total = db.list_users_paginated(tier=tier, intent=intent, status=status, page=page, per_page=per_page)

        # 注入 tags_short 用于列表展示
        for u in users:
            u["tags_short"] = _extract_user_tags_for_list(u, max_tags=3)

        total_pages = (total + per_page - 1) // per_page if per_page else 1

        filters = {"tier": tier or "", "intent": intent or "", "status": status or ""}
        notice = (request.args.get("notice") or "").strip()

        return render_template(
            "admin_users.html",
            users=users,
            filters=filters,
            notice=notice,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
        )

    @app.route("/admin/user/<user_id>", methods=["GET"])
    def admin_user_detail(user_id: str):
        r = _basic_auth_required()
        if r:
            return r

        user = db.get_user(user_id)
        if not user:
            return render_template(
                "admin_user_detail.html",
                user=None,
                tag_list=[],
                profile_text="{}",
                tags_text="[]",
                decision_text="{}",
                messages=[],
                followups=[],
                notice="用户不存在",
            )

        profile = _safe_json(user.get("profile_json"), {})
        decision = _safe_json(user.get("last_decision_json"), {})

        tags = _safe_json(user.get("tags_json"), [])
        if not tags:
            tags = decision.get("tags") or []
        if not isinstance(tags, list):
            tags = []

        profile_text = _pretty_json(profile)
        tags_text = _pretty_json(tags)
        decision_text = _pretty_json(decision)

        messages = db.get_recent_messages(user_id, limit=200)
        followups = db.get_recent_followups(user_id, limit=100)

        notice = (request.args.get("notice") or "").strip()

        return render_template(
            "admin_user_detail.html",
            user=user,
            tag_list=tags,
            profile_text=profile_text,
            tags_text=tags_text,
            decision_text=decision_text,
            messages=messages,
            followups=followups,
            notice=notice,
        )

    @app.route("/admin/user/<user_id>/recompute", methods=["POST"])
    def admin_user_recompute(user_id: str):
        r = _basic_auth_required()
        if r:
            return r
        db.set_next_decision_at(user_id, None)
        return redirect(url_for("admin_user_detail", user_id=user_id, notice="已清空 next_decision_at"))

    @app.route("/admin/user/<user_id>/evaluate_now", methods=["POST"])
    def admin_user_evaluate_now(user_id: str):
        r = _basic_auth_required()
        if r:
            return r

        allow_send = (request.args.get("send") or "").strip() in ("1", "true", "True", "yes", "YES")

        def _bg():
            try:
                logger.info("evaluate_now bg start: user_id=%s allow_send=%s", user_id, allow_send)
                res = evaluate_user_now(user_id, allow_send=allow_send)
                logger.info(
                    "evaluate_now bg done: user_id=%s ok=%s sent=%s err=%s",
                    user_id,
                    res.get("ok"),
                    res.get("sent"),
                    res.get("sent_error"),
                )
            except Exception as e:
                logger.exception("evaluate_now bg failed: user_id=%s err=%s", user_id, e)

        threading.Thread(target=_bg, daemon=True).start()
        return redirect(url_for("admin_user_detail", user_id=user_id, notice="已提交后台评估任务，稍后刷新查看标签/画像/决策"))