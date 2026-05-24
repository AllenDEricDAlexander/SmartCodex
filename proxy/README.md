# proxy

此目录实现了本机 Codex Desktop 可用的 OpenAI 反向代理 PoC。

目标不是代理“Codex Desktop 本体”，而是：
- 让本机应用（如 Codex Desktop）把 OpenAI 请求发到你这台机器的统一入口
- 由代理负责注入 `OPENAI_API_KEY`
- 本机返回 JWT 鉴权，避免在客户端泄露 OpenAI Key

## 目录结构

- `app/main.py`：FastAPI 应用入口，负责鉴权与转发
- `requirements.txt`：PoC 运行依赖
- `plan.md`：当前实现方案与范围说明
- `start-proxy.sh`：本地启动脚本
- `.env.example`：环境变量示例

## 快速开始

### 1) 安装依赖

```bash
cd /Users/mario/SelfProject/SmartCodex
python3 -m venv .venv
source .venv/bin/activate
pip install -r proxy/requirements.txt
```

### 2) 准备环境变量

```bash
export OPENAI_API_KEY="你的 OpenAI API Key"
export INTERNAL_JWT_SECRET="你的 JWT 签名密钥（建议至少 32 字符）"
```

可选配置：

- `UPSTREAM_MODEL_DEFAULT`（默认：`gpt-5.3-codex-spark`）
- `OPENAI_BASE_URL`（默认：`https://api.openai.com/v1`）
- `OPENAI_API_KEY`（必填；可直接 `export` 或写入 `.env`）
- `INTERNAL_TEST_TOKEN`（可选，测试环境用：将其设置为明文 token，匹配 `Authorization: Bearer <token>`）

`OPENAI_BASE_URL` 的作用：只决定“这台代理要转发到哪个 OpenAI 上游地址”。
通常你不需要改，保留默认即可；只有这三种场景才改：
- 你在本地做联调/mock（比如当前代码验证中的本地测试服务）
- 使用 OpenAI 兼容网关（如 Azure OpenAI、企业网关）
- 需要把某些测试环境隔离到特定的 API 域名

- 本机 Codex Desktop 一般只需要改本机请求端点（见下文 5b），上游地址通常不用改。
- `OPENAI_PROJECT`（透传到上游请求）
- `OPENAI_ORGANIZATION`（透传到上游请求）
- `INTERNAL_JWT_ALGORITHM`（默认：`HS256`）
- `INTERNAL_JWT_AUDIENCE` / `INTERNAL_JWT_ISSUER`（可选）
- `UPSTREAM_TIMEOUT_SECONDS`（默认：`120`）
- `PROXY_HOST`（启动脚本读取，默认：`127.0.0.1`）
- `PROXY_PORT`（启动脚本读取，默认：`18980`）
- `PROXY_PYTHON_BIN`（启动脚本读取，默认：`python3`）
- `UVICORN_EXTRA_ARGS`（启动脚本读取，追加启动参数）

### 2b) 你把 `OPENAI_BASE_URL` 和 Codex Desktop 地址分开理解

- `proxy` 的 `OPENAI_BASE_URL`：代理内部把请求转发到的上游地址（默认为 OpenAI）。
- Codex Desktop 的 `base URL`：你要把它改成 `http://127.0.0.1:18980`（代理本地监听地址），这样所有 OpenAI 请求先打到代理。
- 两者不是同一个地址，别在 Codex Desktop 里填 `OPENAI_BASE_URL`（服务端变量）当作本机代理地址。

### 3) 直接启动

```bash
cd /Users/mario/SelfProject/SmartCodex
source .venv/bin/activate
uvicorn proxy.app.main:app --host 127.0.0.1 --port 18980
```

### 4) 使用启动脚本（推荐）

```bash
cd /Users/mario/SelfProject/SmartCodex
cp proxy/.env.example proxy/.env
# 按需编辑 proxy/.env
chmod +x proxy/start-proxy.sh
./proxy/start-proxy.sh
```

### 5) 健康检查

```bash
curl http://127.0.0.1:18980/health
```

### 5a) 你要不要“查 Codex Desktop 端口”？

先说结论：**Codex Desktop 本身通常不是本地监听端口服务**，它更像客户端，真正需要配置的是“目标 API 地址”。  
所以你不用去找它的监听端口，改的是它的 `base URL`。

- 如果你仍想确认当前请求到底打到哪里，可以只看运行时连接记录：

```bash
lsof -nP -iTCP -sTCP:ESTABLISHED | rg -i "codex|electron|openai|chatgpt|18980|8080"
```

- 如果你是想改本机代理地址，就只改成 `127.0.0.1:18980`，不需要改 `OPENAI_BASE_URL`。

### 5b) Codex Desktop（本机）直连说明

你只需要把你的请求端点指向代理地址：
- `http://127.0.0.1:18980`

并且保证你从客户端带上：
- `Authorization: Bearer <内部JWT>`（PoC 必要鉴权）
- 请求体照常发到 `/v1/responses` 或 `/v1/chat/completions`

例如：
```bash
curl -X POST http://127.0.0.1:18980/v1/responses \
  -H "Authorization: Bearer <内部JWT>" \
  -H "Content-Type: application/json" \
  -d '{"input":"Hello","stream":false}'
```

### 6) 打开测试页面（推荐）

```bash
open http://127.0.0.1:18980/test/
```

页面支持：

- 检查 `/health`
- 调 `/v1/responses` 和 `/v1/chat/completions`
- 输入内部 JWT 做鉴权测试
- 支持 `stream` 开关，能看到 SSE 分片输出

### 7) 调用 `responses` 接口

```bash
curl -X POST http://127.0.0.1:18980/v1/responses \
  -H "Authorization: Bearer <internal_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.3-codex-spark","input":"Hello","stream":false}'
```

未提供 `model` 时，服务会自动使用 `UPSTREAM_MODEL_DEFAULT`。

### 8) 支持的最小转发接口

- `POST /v1/responses`
- `POST /v1/chat/completions`
- `GET /v1/models`
- `GET /health`

## JWT 约定

- 客户端通过 `Authorization: Bearer <token>` 访问 Proxy
- token 使用 `HS256` 签发，需包含 `exp`
- `sub` 字段会用于生成 `OpenAI-Safety-Identifier`

开发联调可临时配：

- `INTERNAL_TEST_TOKEN=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`
- 请求直接带 `Authorization: Bearer aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`
- 配置后会跳过 JWT 校验，便于本地快速验证

## 当前范围（MVP）

- 支持 `POST /v1/responses`、`POST /v1/chat/completions`、`GET /health`
- 不包含生产级速率限制、缓存、预算、批量、websocket 等高级能力
- 后续可在 `proxy/app/main.py` 扩展成本控与策略能力
