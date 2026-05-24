from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional
from pathlib import Path

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles


logger = logging.getLogger("proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@dataclass(frozen=True)
class ProxySettings:
    openai_api_key: str
    internal_jwt_secret: str
    internal_test_token: Optional[str] = None
    openai_base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-5.3-codex-spark"
    jwt_algorithm: str = "HS256"
    jwt_audience: Optional[str] = None
    jwt_issuer: Optional[str] = None
    openai_project: Optional[str] = None
    openai_organization: Optional[str] = None
    upstream_timeout_seconds: float = 120.0


def _require_env(name: str, required: bool = False, default: Optional[str] = None) -> str:
    value = (default or "").strip()
    if value:
        return value
    from_env = (getattr(__import__("os"), "environ").get(name) or "").strip()
    if required and not from_env:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return from_env


def _load_settings() -> ProxySettings:
    return ProxySettings(
        openai_api_key=_require_env("OPENAI_API_KEY", required=True),
        internal_jwt_secret=_require_env("INTERNAL_JWT_SECRET", required=True),
        internal_test_token=_require_env("INTERNAL_TEST_TOKEN") or None,
        openai_base_url=_require_env("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        default_model=_require_env("UPSTREAM_MODEL_DEFAULT") or "gpt-5.3-codex-spark",
        jwt_algorithm=_require_env("INTERNAL_JWT_ALGORITHM") or "HS256",
        jwt_audience=_require_env("INTERNAL_JWT_AUDIENCE") or None,
        jwt_issuer=_require_env("INTERNAL_JWT_ISSUER") or None,
        openai_project=_require_env("OPENAI_PROJECT") or None,
        openai_organization=_require_env("OPENAI_ORGANIZATION") or None,
        upstream_timeout_seconds=float(_require_env("UPSTREAM_TIMEOUT_SECONDS") or "120"),
    )


settings = _load_settings()
http_client: httpx.AsyncClient


def _build_headers(claims: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    if settings.openai_project:
        headers["OpenAI-Project"] = settings.openai_project
    if settings.openai_organization:
        headers["OpenAI-Organization"] = settings.openai_organization

    user_id = claims.get("sub")
    if isinstance(user_id, str) and user_id:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        headers["OpenAI-Safety-Identifier"] = digest

    return headers


def _sanitize_response_headers(raw_headers: httpx.Headers) -> dict[str, str]:
    forbidden = {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "upgrade",
        "te",
        "trailer",
    }
    clean = {}
    for name, value in raw_headers.items():
        lower = name.lower()
        if lower in forbidden:
            continue
        if lower.startswith("x-openai-"):
            continue
        clean[name] = value
    return clean


def _to_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be JSON object")
    return dict(payload)


def _prepare_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "model" not in payload or not payload["model"]:
        payload["model"] = settings.default_model
    return payload


def _decode_token(auth_value: Optional[str]) -> dict[str, Any]:
    if not auth_value or not auth_value.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = auth_value.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    decode_kwargs = {
        "algorithms": [settings.jwt_algorithm],
        "options": {"require": ["exp"]},
    }
    if settings.jwt_issuer:
        decode_kwargs["issuer"] = settings.jwt_issuer
    if settings.jwt_audience:
        decode_kwargs["audience"] = settings.jwt_audience

    try:
        if settings.internal_test_token and token == settings.internal_test_token:
            return {"sub": "test-client"}
        return jwt.decode(token, settings.internal_jwt_secret, **decode_kwargs)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(exc)}") from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(timeout=settings.upstream_timeout_seconds, connect=10.0)
    global http_client
    http_client = httpx.AsyncClient(timeout=timeout)
    try:
        yield
    finally:
        await http_client.aclose()


app = FastAPI(title="SmartCodex OpenAI Proxy", lifespan=lifespan)
web_dir = Path(__file__).resolve().parents[1] / "web"
app.mount("/test", StaticFiles(directory=str(web_dir), html=True), name="test-client")


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": "minimal-poc",
        "default_model": settings.default_model,
    }


@app.post("/v1/responses")
async def proxy_responses(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Response:
    start = time.perf_counter()
    claims = _decode_token(authorization)
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        payload = _to_dict(json.loads(raw_body.decode("utf-8")))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    payload = _prepare_payload(payload)
    stream = bool(payload.get("stream", False))
    upstream_headers = _build_headers(claims)

    target_url = f"{settings.openai_base_url.rstrip('/')}/responses"

    request_obj = http_client.build_request(
        method="POST",
        url=target_url,
        json=payload,
        headers=upstream_headers,
    )

    try:
        upstream_resp = await http_client.send(request_obj, stream=stream)
    except httpx.HTTPError as exc:
        logger.exception("upstream_request_failed")
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {str(exc)}") from exc

    headers = _sanitize_response_headers(upstream_resp.headers)
    content_type = upstream_resp.headers.get("content-type", "application/json")

    if stream:
        if content_type.startswith("text/event-stream"):
            headers["Cache-Control"] = "no-cache"
            headers["X-Accel-Buffering"] = "no"

        async def body_generator():
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    yield chunk
            finally:
                await upstream_resp.aclose()

        status_code = upstream_resp.status_code
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "stream_forwarded",
            extra={
                "status_code": status_code,
                "user": claims.get("sub"),
                "model": payload.get("model"),
                "elapsed_ms": elapsed_ms,
            },
        )
        return StreamingResponse(
            body_generator(),
            status_code=status_code,
            headers=headers,
            media_type=content_type,
        )

    body = await upstream_resp.aread()
    status_code = upstream_resp.status_code
    await upstream_resp.aclose()

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "request_forwarded",
        extra={
            "status_code": status_code,
            "user": claims.get("sub"),
            "model": payload.get("model"),
            "elapsed_ms": elapsed_ms,
        },
    )

    return Response(content=body, status_code=status_code, headers=headers, media_type=content_type)


@app.get("/v1/models")
async def proxy_models(
    authorization: Optional[str] = Header(default=None),
) -> Response:
    claims = _decode_token(authorization)
    target_url = f"{settings.openai_base_url.rstrip('/')}/models"
    upstream_headers = _build_headers(claims)

    request_obj = http_client.build_request(
        method="GET",
        url=target_url,
        headers=upstream_headers,
    )
    try:
        upstream_resp = await http_client.send(request_obj)
    except httpx.HTTPError as exc:
        logger.exception("upstream_request_failed")
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {str(exc)}") from exc

    headers = _sanitize_response_headers(upstream_resp.headers)
    body = await upstream_resp.aread()
    await upstream_resp.aclose()
    return Response(content=body, status_code=upstream_resp.status_code, headers=headers, media_type=upstream_resp.headers.get("content-type", "application/json"))


@app.post("/v1/chat/completions")
async def compatibility_chat_completions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Response:
    # Keep a minimal compatibility path for legacy clients while still converging on Responses API.
    return await proxy_responses(request, authorization=authorization)
