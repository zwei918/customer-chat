# 客服小美 (xiaomei-chat)

> Cursor 总工作台项目：`Project_03_客服小美`。2026-08-26 从 GitHub `zwei918/customer-chat` clone 到本机开发。

客户网页 AI 客服——客户在网页发消息，AI（AstrBot 大脑 + 知识库）自动回复。

| | |
|---|---|
| 代码仓库 | https://github.com/zwei918/customer-chat |
| 来源账号 | `chenxi5378-oss/customer-chat` 仍在，日常推送到 zwei918 |
| 线上聊天 | https://chat.okva.cc |
| 运营后台 | https://admin01.okva.cc |

## 项目目标

维护综合性在线客服平台：访客 H5 通道 + 坐席工作台 + 运营中台 + FastAPI / AstrBot 机器人通道（SSE）。「客服小美」只是默认机器人昵称。

## 当前状态

- 状态：进行中（V1 本机可运行：访客 H5 + 坐席工作台 + 运营中台）
- 最近更新：2026-08-29
- 下一步：本机已收紧聊天页顶底留白、后台可切中/英、看板与铃铛下拉已修。改完必须推 `zwei918/customer-chat` 再部署，线上才能看到。上线前服务器仍要配齐 `ADMIN_PASSWORD` + `ADMIN_SECRET`（禁止默认 `changeme-secret`）。
- 视觉：聊天页苹果原则；后台按 Figma CoreUI 骨架。顶栏七个图标和运营看板都接 H5 真实会话，不搬稿里的交通图和社交假数据。

## 目录说明

- `index.html` / `desk.html` / `admin.html`：访客通道、坐席工作台、运营中台。
- `DESIGN.md`：视觉规范（苹果原则 + Intercom 对话清晰度 + Cal 后台密度）。
- `main.py` / `admin_api.py` / `db.py` / `routing.py` / `live.py`：FastAPI 服务、路由分配与 SQLite。
- `docs/`：需求、方案、资料。
- `work/`：过程文件、草稿、实验。
- `outputs/`：最终交付物。
- `logs/`：项目日志和复盘。

## 常用命令

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn httpx python-multipart
ADMIN_PASSWORD=<后台密码> ADMIN_SECRET=<随机长串> \
ASTRBOT_API_KEY=<key> ASTRBOT_URL=http://localhost:6185 \
  .venv/bin/uvicorn main:app --host 127.0.0.1 --port 6200
```

聊天页：`http://127.0.0.1:6200/`  
坐席工作台：`http://127.0.0.1:6200/desk`  
运营中台：`http://127.0.0.1:6200/admin`

## 架构

```
客户浏览器 → https://chat.okva.cc → wsl2 tunnel → FastAPI 服务(:6200)
    ├── GET /        聊天页（客服小美 UI，Host=chat → index.html）
    ├── POST /api/chat  代理 → AstrBot OpenAPI /api/v1/chat (ApiKey) → SSE 流式回显
    └── POST /api/upload 代理 → AstrBot /api/v1/file（附件）

运营后台 → https://admin01.okva.cc → wsl2 tunnel → 同服务 :6200
    ├── GET /        后台页（Host=admin01 → admin.html）
    └── /admin/api/* 会话/访客/看板查询（登录 cookie 鉴权）
        └── SQLite data.db（visitors/sessions/messages 三表落库）
```

- AstrBot 容器：WSL2 宝塔 `/www/wwwroot/astrbot/`（:6185）
- 本服务：WSL2 宝塔 `/www/wwwroot/chat-xiaomei/`（:6200）
- ApiKey 只存服务端环境变量，客户页面永远不接触 AstrBot 管理端

## 能力

- 文本对话（SSE 流式）
- 图片上传（qwen3.7-flash 视觉识别，OpenRouter $0.03/M 最便宜视觉模型）
- 文件上传（小美能感知附件；读取文件内容需装文档解析插件或让客户粘贴文字）
- 语音输入（按住说话，浏览器 Web Speech API 转文字；Chrome/Edge 支持，不支持时按钮自动隐藏）
- 会话保持 + 聊天记录本地保存（localStorage `xiaomei_history` 存 100 条，刷新恢复；换设备/清缓存会丢显示记录，AI 上下文仍在服务端）

## 管理后台（admin01.okva.cc）

运营侧聊天业务后台，与聊天页同服务（按 Host 分流），登录后查看：

- **会话记录** — 全部对话（客户/小美标注），按关键词/访客/时间筛选、分页
- **访客列表** — 匿名访客（浏览器 `crypto.randomUUID()` → `X-Visitor-Id` header），谁来过、聊几轮、消息数
- **运营看板** — 累计/今日访客、会话、消息、附件数 + 高频关键词
- **转人工** — 预留（sessions.status='active'|'handoff' + 后台按钮 disabled"即将上线"），本期未启用

数据层：SQLite `data.db`（WAL），`db.py` 三表（visitors/sessions/messages）+ 索引 + 落库。
管理员 cookie 可吊销（登录换新 token，logout 立刻失效）；未配置 `ADMIN_PASSWORD` 或 `ADMIN_SECRET` 仍为默认值时，后台接口拒绝服务。
后台密码与签名密钥只走环境变量或服务器未跟踪文件 `/www/wwwroot/chat-xiaomei/.admin_password`、`.admin_secret`，不要写入 git。
公开接口按访客+IP 限速：聊天 30 次/分、上传 20 次/分。登录只计失败次数。

## 部署

```bash
# WSL2
cd /www/wwwroot/chat-xiaomei
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn httpx python-multipart
ADMIN_PASSWORD=<后台密码> ADMIN_SECRET=<随机长串> \
ASTRBOT_API_KEY=<key> ASTRBOT_URL=http://localhost:6185 \
  .venv/bin/uvicorn main:app --host 0.0.0.0 --port 6200
```

Supervisor 托管（宝塔）。**上线前必须写上这两个变量**，缺一个后台进不去；`ADMIN_SECRET` 不能是 `changeme-secret`。部署本版会让已登录后台的 cookie 失效一次，重新登录即可。
```
[program:chat-xiaomei]
command=/www/wwwroot/chat-xiaomei/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 6200
directory=/www/wwwroot/chat-xiaomei
environment=ADMIN_PASSWORD="<后台密码>",ADMIN_SECRET="<随机长串>",ASTRBOT_API_KEY="<key>",ASTRBOT_URL="http://localhost:6185"
user=www
autostart=true
autorestart=true
```

## Tunnel

- DNS CNAME: `chat.okva.cc` → wsl2 tunnel (bb6f5c86-7d31-4fc3-bcf6-666ea652f1cd)
- Ingress: `chat.okva.cc` → `http://localhost:6200`

## 模型

- Provider: DeepSeek（openai_chat_completion，`api_base=https://api.deepseek.com`，model=`deepseek-v4-flash`）
- 配置位置：`/www/wwwroot/astrbot/data/cmd_config.json` 的 `provider` 数组（key 从 Hermes .env 的 DEEPSEEK_API_KEY）
- 人格：数据库 `data_v4.db` 的 `personas` 表，persona_id=`xiaomei`（客服小美人设），`provider_settings.default_personality=xiaomei`

## 知识库

WebUI → 知识库，上传文档后客户问答自动检索（faiss+BM25）。AstrBot 知识库在 `/www/wwwroot/astrbot/data/knowledge_base/`。

## 运维

- AstrBot 容器：`docker restart astrbot`（数据在 `/www/wwwroot/astrbot/data/`，备份 = 整个 data 目录）
- 客服服务：Supervisor `chat-xiaomei`（宝塔面板可看），日志 `/www/wwwroot/chat-xiaomei/supervisor.log`
- 改配置流程：备份 cmd_config.json → 改 → 重启容器

## 安全

- 客服页面公网只暴露 6200 代理，AstrBot 管理端 6185 不经公网（astrbot.okva.cc 需登录）
- ApiKey、后台密码、`ADMIN_SECRET` 只在服务端环境变量，客户页面接触不到
- 未配置 `ADMIN_PASSWORD` 或仍用默认 `ADMIN_SECRET=changeme-secret` 时，后台登录与 `/admin/api` 直接拒绝
- AstrBot 历史 CVE 集中在 WebUI/插件安装链，面板务必改强密码 + 开双因素
