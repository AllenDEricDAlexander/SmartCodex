# Proxy 目录 PoC 实施计划（最小可运行版）

## 目标
- 在 `proxy/` 目录实现一个最小可用的 OpenAI 代理服务
- 使用内部 JWT 对调用方鉴权
- 提供 `POST /v1/responses` 的转发能力，并支持 `stream: true` 的 SSE 透传
- 默认在代理层注入 OpenAI API Key，不将密钥暴露给客户端

## 范围（MVP）
- 配置加载（环境变量）
- 鉴权：`Authorization: Bearer <JWT>`，验证签名/过期并解析 `sub`
- 代理转发：
  - 接收 `POST /v1/responses`
  - 若未提供 `model` 则注入默认模型
  - 将请求转发到 `https://api.openai.com/v1/responses`
  - 透传响应头与状态码
  - `stream=true` 时返回流式响应
- 监控/日志：
  - 最小日志：请求时间、上下游状态码、内部用户标识、模型名
- 运维：健康检查 `GET /health`

## 不在本阶段包含
- 全量限流策略
- 缓存、单飞、降本策略
- 批量任务和 WebSocket
- 复杂配额与审计系统

## 交付清单
- `proxy/README.md`
- `proxy/plan.md`
- `proxy/requirements.txt`
- `proxy/app/main.py`
