# 客服小美 (xiaomei-chat)

客户网页 AI 客服——客户在网页发消息，AI（AstrBot 大脑 + 知识库）自动回复。

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

数据层：SQLite `data.db`（WAL），`db.py` 三表（visitors/sessions/messages）+ 索引 + 落库，管理员 cookie 鉴权（HttpOnly/Secure + 登录限速）。
后台登录密码：服务器 `/www/wwwroot/chat-xiaomei/.admin_password`（环境变量 `ADMIN_PASSWORD`）。

## 部署

```bash
# WSL2
cd /www/wwwroot/chat-xiaomei
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn httpx
ASTRBOT_API_KEY=<key> ASTRBOT_URL=http://localhost:6185 .venv/bin/uvicorn main:app --host 0.0.0.0 --port 6200
```

Supervisor 托管（宝塔）：
```
[program:chat-xiaomei]
command=/www/wwwroot/chat-xiaomei/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 6200
directory=/www/wwwroot/chat-xiaomei
environment=ASTRBOT_API_KEY="<key>",ASTRBOT_URL="http://localhost:6185"
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
- ApiKey 只在服务端环境变量，客户页面接触不到
- AstrBot 历史 CVE 集中在 WebUI/插件安装链，面板务必改强密码 + 开双因素
