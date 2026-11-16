# -*- coding: utf-8 -*-

import os
import time
import uuid
import logging
from datetime import datetime
from typing import Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor

import db
from marketing import scan_and_process_candidates

logger = logging.getLogger("scheduler")

_SCHEDULER: Optional[BackgroundScheduler] = None
_LOCK_FD = None

_LOCK_PATH = os.environ.get("SCHEDULER_LOCK_PATH", "/tmp/qiwe-bot.scheduler.lock")


def _now_shanghai_str() -> str:
    """
    统一 scheduler 日志时间口径为 Asia/Shanghai。
    注意：APScheduler 本身 timezone 也设为 Asia/Shanghai，保持一致更利于排障。
    """
    try:
        from zoneinfo import ZoneInfo  # py>=3.9

        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        # fallback：用服务器本地时间
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "y", "on")


def _try_acquire_lock(path: str):
    """尝试获取文件锁，用于防止多实例同时运行调度器。
    
    注意：Windows 不支持 fcntl，会静默返回 None（依赖其他机制保证单实例）。
    """
    try:
        import fcntl  # Linux only

        fd = open(path, "w")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except ImportError:
        # Windows 不支持 fcntl，记录日志后继续（不阻塞）
        logger.debug("fcntl not available (Windows), skip file lock")
        return None
    except Exception as e:
        logger.warning("scheduler lock not acquired: path=%s err=%s", path, e)
        return None


def _get_interval_seconds() -> Tuple[int, str]:
    """
    获取调度间隔（秒）并返回 (interval, source)：
    - 优先使用环境变量 SCHEDULER_INTERVAL_SECONDS（推荐）
    - 若 env 未设置，则回退到默认 300 秒（5 分钟）
    - 出于兼容与可见性考虑：如果 DB 中仍存在 decision_scan_interval_seconds，会打印一条警告
    说明：此前实现优先读 DB；团队已决定弃用 DB 配置并统一用 env，因此此处调整为以 env 为准。
    """
    # 1) ENV (preferred)
    try:
        v = os.environ.get("SCHEDULER_INTERVAL_SECONDS")
        if v not in (None, ""):
            i = int(float(v))
            if i > 0:
                return i, "env:SCHEDULER_INTERVAL_SECONDS"
    except Exception:
        pass

    # 2) 如果 DB 仍有旧值，记录一条警告供运维/排障查看（但不使用它）
    try:
        v = None
        try:
            v = db.get_setting("decision_scan_interval_seconds")
        except Exception:
            v = None
        if v not in (None, ""):
            logger.warning(
                "Deprecated DB setting decision_scan_interval_seconds found=%s but will be ignored. "
                "Configure SCHEDULER_INTERVAL_SECONDS env var instead.",
                str(v),
            )
    except Exception:
        # 忽略任何读取/日志异常，继续使用默认
        pass

    # 3) 默认（5 分钟）
    return 300, "default"


def start_scheduler():
    """
    启动 APScheduler（强制单实例）:
    - interval 优先从环境变量 SCHEDULER_INTERVAL_SECONDS 读取；若未设置，使用默认 300s（5 分钟）。
    - 可通过 env SCHEDULER_DEBUG=1 打开更详细日志
    """
    global _SCHEDULER, _LOCK_FD

    if _SCHEDULER is not None:
        logger.debug("调度器已在本进程启动，跳过重复启动：pid=%s", os.getpid())
        return _SCHEDULER

    debug = _env_bool("SCHEDULER_DEBUG", False)

    # 注意：确保 db 已初始化（你的 app.py 里 init_app_once 会先 init_db）
    interval, interval_src = _get_interval_seconds()

    _LOCK_FD = _try_acquire_lock(_LOCK_PATH)
    if not _LOCK_FD:
        logger.warning("调度器未启动：已存在运行中的 scheduler（lock=%s pid=%s）", _LOCK_PATH, os.getpid())
        return None

    executors = {"default": ThreadPoolExecutor(max_workers=4)}
    job_defaults = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 30,
    }

    scheduler = BackgroundScheduler(executors=executors, job_defaults=job_defaults, timezone="Asia/Shanghai")

    def job_wrapper():
        run_id = str(uuid.uuid4())[:8]
        t0 = time.time()
        logger.info(
            "[run %s] scan_and_process_candidates start at %s interval=%ss(src=%s)",
            run_id,
            _now_shanghai_str(),
            interval,
            interval_src,
        )
        try:
            scan_and_process_candidates()
            cost = int((time.time() - t0) * 1000)
            logger.info("[run %s] scan_and_process_candidates done cost=%sms at %s", run_id, cost, _now_shanghai_str())
        except Exception as e:
            cost = int((time.time() - t0) * 1000)
            logger.exception("[run %s] scan_and_process_candidates FAILED cost=%sms err=%s", run_id, cost, e)

    scheduler.add_job(
        job_wrapper,
        trigger="interval",
        seconds=interval,
        id="scan_and_process_candidates",
        replace_existing=True,
    )

    scheduler.start()
    _SCHEDULER = scheduler

    logger.info(
        "调度器启动：interval=%ss(source=%s) debug=%s pid=%s lock=%s",
        interval,
        interval_src,
        debug,
        os.getpid(),
        _LOCK_PATH,
    )

    if debug:
        for job in scheduler.get_jobs():
            logger.info("job registered: id=%s trigger=%s next_run=%s", job.id, job.trigger, job.next_run_time)

    return scheduler