# 企微智能客服机器人系统

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Flask-2.2+-green.svg" alt="Flask 2.2+">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT">
</p>

<p align="center">
  <b>基于企业微信的全渠道 AI 智能客服解决方案</b><br>
  集成字节跳动 Coze 工作流 + 火山引擎 Ark 大模型 + 豆包 ASR 语音识别
</p>

---

## 🎯 系统概述

本系统是一套面向企业微信生态的**全栈式 AI 智能客服解决方案**，通过集成 Coze、Ark 和豆包相关 AI 服务（Coze 智能体平台、Ark 大模型引擎、豆包语音识别），为企业客户提供**7×24 小时不间断的智能客服服务**。

### 核心能力矩阵

| 能力维度 | 技术实现 | 业务价值 |
|---------|---------|---------|
| **智能对话** | Coze 工作流 + 配置的模型 | 精准理解用户意图，专业回复 |
| **语音交互** | SILK 解码 → AMR 转 WAV → 豆包 ASR | 语音消息自动转文字，无缝沟通 |
| **图像理解** | Ark 多模态视觉模型 | 图片内容识别、OCR 文字提取 |
| **沉默运营** | 智能决策引擎 + 定时任务调度 | 自动唤醒沉默用户，提升转化 |
| **拟人体验** | 动态延迟算法 + 分段发送 | 模拟真人打字，体验自然流畅 |

---

## 🏗️ 技术架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           企业微信客户端                                      │
│                    (用户发送文本/图片/语音消息)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Webhook 消息网关层                                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  消息接收    │  │  鉴权验证    │  │  类型路由    │  │   异步任务队列       │  │
│  │  回调入口   │  │  Token校验   │  │  文本/图片   │  │   ThreadPool(8)     │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│   文本处理器     │         │   图片处理器     │         │   语音处理器     │
│  realtime.py    │         │ image_inbound.py│         │ voice_inbound.py│
│                 │         │                 │         │                 │
│ • 直接送 Coze   │         │ • 下载图片      │         │ • SILK 解码     │
│ • 上下文管理    │         │ • Ark OCR识别   │         │ • AMR→WAV转码   │
│ • 延迟控制      │         │ • 结果送 Coze   │         │ • 火山 TOS 上传 │
└────────┬────────┘         └────────┬────────┘         │ • 豆包 ASR 识别 │
         │                           │                  └────────┬────────┘
         └───────────────────────────┼───────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AI 引擎层 (字节跳动生态)                             │
│  ┌─────────────────────────────────┐  ┌─────────────────────────────────┐   │
│  │      Coze 智能体平台             │  │      Ark 大模型引擎              │   │
│  │  ┌─────────────────────────┐    │  │  ┌─────────────────────────┐    │   │
│  │  │  • 多轮对话管理          │    │  │  │  • 视觉理解 (OCR)        │    │   │
│  │  │  • 知识库检索 (RAG)      │    │  │  │  • 决策推理               │    │   │
│  │  │  • 工作流编排            │    │  │  │  • 用户画像分析           │    │   │
│  │  │  • 插件扩展能力          │    │  │  │  • 沉默用户唤醒策略       │    │   │
│  │  └─────────────────────────┘    │  │  └─────────────────────────┘    │   │
│  └─────────────────────────────────┘  └─────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    豆包语音识别 (AUC BigModel ASR)                   │    │
│  │         支持 wav 格式，识别效果取决于音频与模型配置                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         数据持久化层                                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │   SQLite        │  │   火山 TOS       │  │        企微 API              │  │
│  │   结构化数据     │  │   对象存储       │  │    消息发送/媒体下载          │  │
│  │                 │  │                 │  │                             │  │
│  │ • 用户画像      │  │ • 语音文件存储   │  │ • 文本消息发送              │  │
│  │ • 对话历史      │  │ • 永久 URL      │  │ • 图片消息发送              │  │
│  │ • 运营记录      │  │ • 公共读访问     │  │ • 媒体文件下载              │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         运营管理层 (Marketing)                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │   沉默检测引擎   │  │   决策调度器     │  │      管理后台 Web UI         │  │
│  │                 │  │                 │  │                             │  │
│  │ • 空闲时间追踪   │  │ • 定时任务调度   │  │ • 用户列表/搜索             │  │
│  │ • 免打扰时段    │  │ • 并发控制      │  │ • 对话记录查看              │  │
│  │ • 跟进次数限制   │  │ • 冷却期管理    │  │ • 语音在线播放              │  │
│  │ • 成交置信度    │  │ • 优先级队列    │  │ • 手动触发运营              │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 技术栈详解

| 层级 | 技术选型 | 选型理由 |
|-----|---------|---------|
| **Web 框架** | Flask + Gunicorn | 轻量灵活，生产级 WSGI 服务器支持高并发 |
| **数据库** | SQLite | 零配置、单文件部署，适合中小型客服场景 |
| **任务队列** | Python ThreadPoolExecutor | 轻量级异步处理，无需额外消息队列依赖 |
| **对象存储** | 火山引擎 TOS | 国内访问快，与豆包 ASR 同生态，延迟低 |
| **语音处理** | SILK Decoder + FFmpeg | 完整支持微信语音格式转码链路 |
| **AI 引擎** | Coze + Ark + 豆包 ASR | 字节跳动全栈 AI，效果稳定成本可控 |

---

## ✨ 核心功能详解

### 1. 智能消息处理中枢

#### 1.1 文本消息
- **上下文管理**：维护最近 10 轮对话历史，确保对话连贯性
- **敏感词过滤**：内置敏感词检测，支持自定义词库
- **长文本分段**：超过 140 字自动分段发送，避免大段文字压迫感

#### 1.2 图片消息 (OCR)
```
用户发送图片 → 下载到本地 → Ark 视觉模型 OCR 识别 → 提取文字内容 → 送 Coze 处理 → 返回结果
```
- 支持发票、合同、截图等多种图片类型
- 可选脱敏存储（只保存摘要，不存原文）

#### 1.3 语音消息 (ASR)
```
用户发送语音 → 企微 API 下载 SILK → SILK 解码为 PCM → FFmpeg 转 WAV
    → 上传火山 TOS 获取 URL → 豆包 ASR 识别 → 文字送 Coze → 返回结果
```
- **语音链路全自动化**：无需人工干预，端到端处理
- **TOS 永久存储**：语音文件上传对象存储，后台可随时回放
- **ASR 流程**：提交音频 URL 后轮询结果；仓库未提供识别准确率基准测试

### 2. 拟人化延迟系统

为了让 AI 回复更像真人，系统实现了**多维度延迟算法**：

#### 2.1 新用户极速响应
```python
# 消息数 <= 2 的用户视为新用户
is_new_user = msg_count <= NEW_USER_MESSAGE_THRESHOLD  # 默认为 2

# 新用户首次消息 0 延迟，立即响应
QIWE_FIRST_MESSAGE_DELAY_MS = 0
```

#### 2.2 动态打字延迟
```python
# 基础延迟 + 字符数 × 单字延迟 + 随机抖动
base_ms = 150                    # 基础延迟 150ms
per_char_ms = 5                  # 每字符 5ms
jitter_ms = random(0, 100)       # 0-100ms 随机抖动
max_ms = 800                     # 最大延迟 800ms

delay = min(base_ms + length * per_char_ms + jitter_ms, max_ms)
```

#### 2.3 长文本分段延迟（可选）
```python
# 分段发送时每段间隔 10-30 秒（模拟思考时间）
QIWE_HUMAN_LONG_DELAY_ENABLED = 0  # 默认关闭，避免拖太久
QIWE_HUMAN_LONG_DELAY_MIN_SEC = 10
QIWE_HUMAN_LONG_DELAY_MAX_SEC = 30
```

### 3. 沉默用户智能运营

#### 3.1 沉默检测逻辑
```
用户最后回复时间 ──► 空闲超过 DECISION_IDLE_MINUTES(10分钟)
    │
    ▼
检查免打扰时段 ──► 不在 QUIET_HOURS (00:00-08:00)
    │
    ▼
检查跟进次数 ──► 当日未超过 DAILY_FOLLOWUP_LIMIT(2次)
    │
    ▼
检查冷却期 ──► 距离上次运营超过 MIN_FOLLOWUP_INTERVAL_MINUTES(5分钟)
    │
    ▼
触发 Ark 决策 ──► 分析对话上下文生成个性化跟进文案
```

#### 3.2 用户生命周期管理
| 状态 | 定义 | 自动处理 |
|-----|------|---------|
| `active` | 活跃用户（24小时内有互动） | 正常响应 |
| `idle` | 沉默用户（超过10分钟未回复） | 触发运营决策 |
| `won` | 成交用户（置信度 > 90%） | 停止自动运营 |
| `lost` | 流失用户（3天无回复） | 标记为流失 |

#### 3.3 决策引擎工作流
1. **数据收集**：汇总用户最近 80 条消息（或 14 天内）
2. **画像分析**：Ark 大模型分析用户需求、意向度、成交概率
3. **策略选择**：根据画像选择最佳跟进策略（优惠/案例/FAQ/预约）
4. **文案生成**：Coze 生成个性化跟进话术
5. **效果追踪**：记录用户回复，闭环优化策略

---

## 🚀 快速部署指南

### 环境要求

- **操作系统**：Ubuntu 20.04+ / Debian 11+
- **Python**：3.9 或更高版本
- **内存**：最低 2GB，推荐 4GB+
- **磁盘**：最低 10GB 可用空间
- **网络**：可访问企微 API、火山引擎、Coze 服务

### 获取源码

```bash
git clone https://github.com/Lucian-Wang/coze.git qiwe-bot
cd qiwe-bot
```

仓库没有 deploy.sh，请按下文手工配置。默认服务文件使用 /www/wwwroot/qiwe-bot 和 www 用户；安装到其他位置时需修改服务文件。

### 手动部署步骤

#### Step 1: 系统依赖

```bash
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-venv \
    ffmpeg git gcc make \
    libsqlite3-dev
```

#### Step 2: 编译 SILK 解码器（语音功能必需）

```bash
# 克隆源码
sudo git clone https://github.com/kn007/silk-v3-decoder.git /opt/silk-v3-decoder

# 编译
cd /opt/silk-v3-decoder/silk
sudo make

# 验证
sudo chmod +x /opt/silk-v3-decoder/silk/decoder
/opt/silk-v3-decoder/silk/decoder
# 应输出: Usage: decoder input.bit output.pcm [sample_rate]
```

#### Step 3: Python 环境

```bash
cd /path/to/qiwe-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### Step 4: 配置环境变量

```bash
cp .env.example .env
nano .env
```

**关键配置项说明：**

```bash
# ==================== 企微 API 配置 ====================
# 从项目对接的企微 API 服务获取
QIWE_API_TOKEN=your_token_here
QIWE_API_GUID=your_guid_here

# ==================== Coze AI 配置 ====================
# 从 Coze 平台获取
COZE_TOKEN=your_coze_token_here
COZE_PROJECT_ID=your_project_id_here

# ==================== Ark 大模型配置 ====================
# 从火山引擎控制台获取
ARK_BEARER=your_ark_key_here

# ==================== 豆包 ASR 配置 ====================
# 从火山引擎语音服务获取
DOUBAO_ASR_APP_KEY=your_app_key_here
DOUBAO_ASR_ACCESS_KEY=your_access_key_here

# ==================== 火山 TOS 配置 ====================
# 对象存储配置（从部署环境变量读取）
TOS_ACCESS_KEY=your_access_key_here
TOS_SECRET_KEY=your_secret_key_here
TOS_ENDPOINT=tos-cn-beijing.volces.com
TOS_BUCKET=your_bucket_name_here

# ==================== 安全相关配置 ====================
# Webhook 鉴权 Token（自定义强密码）
WEBHOOK_AUTH_TOKEN=your_random_token_here

# 管理后台密码
ADMIN_PASSWORD=your_admin_password_here
```

`audio_upload_client.py` 从环境变量读取 TOS 配置。启动应用前设置 `TOS_ACCESS_KEY`、`TOS_SECRET_KEY`、`TOS_BUCKET`，可选 `TOS_ENDPOINT`、`TOS_REGION`。直接运行模块时需自行加载 `.env`。部署时必须设置独有的 `ADMIN_PASSWORD`；未设置时后台拒绝登录。已公开过的旧凭据必须在服务商后台撤销。

#### Step 5: 启动服务

**开发模式（调试）：**
```bash
source venv/bin/activate
python app.py
```

**生产模式（推荐）：**
```bash
source venv/bin/activate
gunicorn -c gunicorn_conf.py app:app
```

**Systemd 服务（开机自启）：**
```bash
# 先修改 qiwe-bot.service 的 User、Group、WorkingDirectory 和 ExecStart
sudo cp qiwe-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable qiwe-bot
sudo systemctl start qiwe-bot

# 查看状态
sudo systemctl status qiwe-bot
sudo journalctl -u qiwe-bot -f
```

---

## ⚙️ 高级配置指南

### Gunicorn 进程/线程配置

当前 `gunicorn_conf.py` 使用 `workers = 1`、`threads = 1`、`worker_class = 'sync'` 和 120 秒超时。应用另有入站任务线程池及进程内调度器；不要直接按 CPU 数增加 worker，否则需先处理调度任务重复执行和跨进程用户锁。

启动前创建并授权日志目录，或设置 `GUNICORN_ACCESS_LOG=-`、`GUNICORN_ERROR_LOG=-` 输出到标准流。监听地址可通过 `GUNICORN_BIND` 修改。

### 延迟配置调优

```bash
# .env 文件

# ==================== 新用户极速响应 ====================
# 消息数 <= 2 视为新用户
NEW_USER_MESSAGE_THRESHOLD=2

# 新用户首次消息 0 延迟
QIWE_FIRST_MESSAGE_DELAY_MS=0

# ==================== 打字延迟配置 ====================
# 基础延迟（毫秒）
QIWE_HUMAN_DELAY_BASE_MS=150

# 每字符延迟（毫秒）
QIWE_HUMAN_DELAY_PER_CHAR_MS=5

# 随机抖动范围（毫秒）
QIWE_HUMAN_DELAY_JITTER_MS=100

# 最大延迟上限（毫秒）
QIWE_HUMAN_DELAY_MAX_MS=800

# 是否启用打字延迟
QIWE_HUMAN_DELAY_ENABLED=1

# ==================== 长文本分段延迟（谨慎开启） ====================
# 是否启用长延迟
QIWE_HUMAN_LONG_DELAY_ENABLED=0

# 分段间隔（秒）
QIWE_HUMAN_LONG_DELAY_MIN_SEC=10
QIWE_HUMAN_LONG_DELAY_MAX_SEC=30
```

### 沉默运营配置

```bash
# .env 文件

# ==================== 沉默检测 ====================
# 用户静默多少分钟后触发决策
DECISION_IDLE_MINUTES=10

# 免打扰时段（夜间不发送运营消息）
QUIET_HOURS_ENABLED=1
QUIET_HOURS_START=00:00
QUIET_HOURS_END=08:00

# 免打扰期间延迟多少分钟再试
QUIET_HOURS_DEFER_MINUTES=5

# ==================== 运营限制 ====================
# 当日最多跟进次数（防骚扰）
DAILY_FOLLOWUP_LIMIT=2

# 两次跟进最小间隔（分钟）
MIN_FOLLOWUP_INTERVAL_MINUTES=5

# 成交置信度阈值（超过标记为 won）
WON_THRESHOLD=0.90

# 多少天无回复标记为流失
MARKETING_MARK_LOST_AFTER_DAYS=3

# ==================== 决策模型配置 ====================
# 决策参考的最大消息数
MAX_MESSAGES_FOR_DECISION=80

# 决策参考的最大天数
MAX_DAYS_FOR_DECISION=14
```

### 语音存储配置

```bash
# .env 文件

# ==================== 语音文件存储 ====================
# 是否保存 ASR 识别的文字（推荐开启）
STORE_DERIVED_TEXT=1

# 是否只保存 OCR 摘要（开启可节省空间）
STORE_OCR_SUMMARY_ONLY=1

# 是否保存语音 URL（必须开启才能后台播放）
STORE_AUDIO_URL=1

# 当前上传函数返回对象 URL，不生成带有效期的预签名 URL；TOS_URL_EXPIRE_HOURS 不会使该 URL 自动过期
```

---

## 📊 系统监控与运维

### 日志查看

```bash
# Gunicorn 错误日志
tail -f /www/wwwlogs/python/qiwe-bot/gunicorn_error.log

# Gunicorn 访问日志
tail -f /www/wwwlogs/python/qiwe-bot/gunicorn_access.log

# Systemd 服务日志
sudo journalctl -u qiwe-bot -f

# 实时查看所有日志
sudo journalctl -u qiwe-bot -f &
tail -f /www/wwwlogs/python/qiwe-bot/*.log
```

### 数据库查看

```bash
# 进入 SQLite 命令行
sqlite3 data/app.db

# 常用查询
.tables                          # 查看所有表
SELECT * FROM users LIMIT 5;     # 查看用户列表
SELECT * FROM messages ORDER BY id DESC LIMIT 10;  # 最近消息
SELECT * FROM messages WHERE msg_type='voice_wav';  # 语音消息
```

### 性能监控

```bash
# 查看 Gunicorn 进程
ps aux | grep gunicorn

# 查看端口占用
netstat -tlnp | grep 5001

# 查看系统资源
top -p $(pgrep -d',' gunicorn)
```

---

## 🔧 故障排查

### 问题 1: 语音消息无法识别

**现象**：用户发送语音，机器人不回复或回复"语音识别失败"

**排查步骤**：
```bash
# 1. 检查 SILK 解码器
/opt/silk-v3-decoder/silk/decoder
# 应显示 Usage 信息，否则重新编译

# 2. 检查 FFmpeg
ffmpeg -version

# 3. 检查 TOS 配置
python -c "from audio_upload_client import upload_audio_bytes; print('module import OK; upload not tested')"

# 4. 检查 .env 中的 DOUBAO_ASR_APP_KEY、DOUBAO_ASR_ACCESS_KEY
# 及 DOUBAO_ASR_RESOURCE_ID；请求头与提交/轮询流程见 doubao_asr.py
```

### 问题 2: AI 不回复或回复慢

**现象**：消息发送后长时间无响应

**排查步骤**：
```bash
# 1. 检查 Coze Token 是否过期
tail -f /www/wwwlogs/python/qiwe-bot/gunicorn_error.log | grep -i "coze\|token"

# 2. 检查线程池是否满载
# 查看日志中是否有 "Task queue is full" 警告

# 3. 检查企微 API 是否正常
curl "http://manager.qiweapi.com/qiwe/cloud/wxDownload?token=YOUR_TOKEN"

# 4. 临时关闭延迟测试
# 在 .env 中设置 QIWE_HUMAN_DELAY_ENABLED=0，重启服务
```

### 问题 3: 后台无法播放语音

**现象**：聊天记录页面点击播放无反应

**排查步骤**：
```bash
# 1. 检查 STORE_AUDIO_URL 是否为 1
grep STORE_AUDIO_URL .env

# 2. 检查数据库是否有 wavUrl
sqlite3 data/app.db "SELECT extra_json FROM messages WHERE msg_type='voice_wav' LIMIT 1;"
# 应显示 {"wavUrl": "https://..."}

# 3. 检查 TOS URL 是否可访问
# 复制数据库中的 URL，在浏览器直接访问测试

# 4. 检查 TOS Bucket 权限
# 确保 Bucket 已开启公共读权限
```

### 问题 4: 沉默运营不触发

**现象**：用户沉默后没有自动跟进

**排查步骤**：
```bash
# 1. 检查调度器是否运行
sudo systemctl status qiwe-bot

# 2. 检查 MARKETING_ENABLED
grep MARKETING_ENABLED .env  # 应为 1

# 3. 检查调度器日志
tail -f /www/wwwlogs/python/qiwe-bot/gunicorn_error.log | grep -i "scheduler\|marketing"

# 4. 手动测试决策
# 登录管理后台，找到沉默用户，点击"手动评估"测试
```

---

## 📝 API 接口文档

### Webhook 接收消息

默认入口为 `POST /qiweapi/callback`，可由 `QIWEAPI_CALLBACK_PATH` 覆盖。默认通过 `?token=...` 校验 `WEBHOOK_AUTH_TOKEN`；设 `WEBHOOK_AUTH_MODE=header` 后改用 `X-Webhook-Token` 请求头（名称可配置）。未配置 token 时拒绝请求。

请求体为企微 API 的回调结构，例如：

```json
{
  "data": [{
    "cmd": 15000,
    "fromRoomId": 0,
    "senderId": "example-user",
    "guid": "your-guid",
    "msgType": 0,
    "msgData": {"content": "你好"}
  }]
}
```

响应包含 `code`、`msg`、`data`；接收回调后异步处理消息。健康检查为 `GET /health`。

### 管理后台接口

后台使用 HTTP Basic Auth，用户名和密码由 `ADMIN_USERNAME`、`ADMIN_PASSWORD` 配置。

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/admin` | GET | 首页 |
| `/admin/users` | GET | 用户列表 |
| `/admin/user/<user_id>` | GET | 用户详情 |
| `/admin/user/<user_id>/recompute` | POST | 重算用户画像 |
| `/admin/user/<user_id>/evaluate_now` | POST | 手动评估；是否发送受参数控制 |

---

## 📁 项目结构

```
qiwe-bot/
├── app.py                      # Flask 主应用入口
├── realtime.py                 # 实时消息处理（文本）
├── voice_inbound.py            # 语音消息处理（ASR 链路）
├── image_inbound.py            # 图片消息处理（OCR 链路）
├── marketing.py                # 沉默用户运营引擎
├── scheduler.py                # 定时任务调度器
├── ark_decider.py              # Ark 决策模型封装
├── db.py                       # SQLite 数据库操作
├── qiwe_client.py              # 企微 API 客户端
├── coze_client.py              # Coze AI 客户端
├── ark_client.py               # Ark 大模型客户端
├── doubao_asr.py               # 豆包 ASR 客户端
├── audio_upload_client.py      # 火山 TOS 上传客户端
├── audio_transcode.py          # 音频转码工具
├── silk_decode.py              # SILK 语音解码
├── admin.py                    # 管理后台路由
├── webhook_auth.py             # Webhook 鉴权
├── deferral.py                 # 延迟发送工具
├── gunicorn_conf.py            # Gunicorn 配置
├── qiwe-bot.service            # Systemd 服务文件
├── requirements.txt            # Python 依赖
├── .env                        # 环境变量（本地创建）
├── .env.example                # 环境变量示例
├── README.md                   # 项目文档
├── data/                       # SQLite 数据库目录
└── templates/                  # Jinja2 HTML 模板
    ├── admin_users.html        # 用户列表页
    └── admin_user_detail.html  # 用户详情页
```

---

## 🔄 更新升级

```bash
# 1. 进入项目目录
cd /www/wwwroot/qiwe-bot

# 2. 拉取最新代码
git pull

# 3. 激活虚拟环境
source venv/bin/activate

# 4. 更新依赖
pip install -r requirements.txt

# 5. 重启服务
sudo systemctl restart qiwe-bot

# 6. 验证状态
sudo systemctl status qiwe-bot
```

---

## 📄 开源协议

README 声明采用 MIT License；当前仓库未包含独立 LICENSE 文件。

---

## 🤝 技术支持

如有问题，请通过以下方式联系：

- 提交 GitHub Issue
- 联系系统管理员

---

<p align="center">
  <b>Maintained by Lucian</b>
</p>

## 维护与反馈

维护者：[Lucian](https://lucian.wang/) · 邮箱：im@lucian.wang · [问题反馈](https://github.com/Lucian-Wang/coze/issues)
