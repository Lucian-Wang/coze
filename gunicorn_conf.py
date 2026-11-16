# -*- coding: utf-8 -*-
# Gunicorn 配置文件

import os
import multiprocessing

# 项目目录
chdir = os.path.dirname(os.path.abspath(__file__))

# 进程配置
workers = 1
threads = 1
worker_class = 'sync'

# 绑定的 IP 与端口
bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:5001')

# 启动用户（根据实际用户修改）
# user = 'www'

# 设置进程文件目录
pidfile = os.environ.get('GUNICORN_PIDFILE', 'gunicorn.pid')

# 设置访问日志和错误信息日志路径
accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '/www/wwwlogs/python/qiwe-bot/gunicorn_access.log')
errorlog = os.environ.get('GUNICORN_ERROR_LOG', '/www/wwwlogs/python/qiwe-bot/gunicorn_error.log')

# 日志级别
debug = os.environ.get('GUNICORN_DEBUG', '0') in ('1', 'true', 'True')
loglevel = 'debug' if debug else 'info'

# 工作模式配置
timeout = 120
keepalive = 5

# 优雅重启配置
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 50

# 预加载应用（节省内存）
preload_app = True

# 守护进程模式（如果使用 systemd 则设为 False）
daemon = False
