#!/usr/bin/env python3
"""Diagnose Xiaomi MiMo Token Plan provider wiring.

Example:
  venv/bin/python -m scripts.diagnose_mimo_provider --model mimo-v2.5-pro --prompt "hi"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any
from urllib.parse import urlparse

from src.auth.service import AuthManager
from src.core.llm_protocols import _anthropic_messages_endpoint, _normalize_anthropic_base_url
from src.core.llm_router import LLMRouter
from src.core.model_config import ModelConfigManager


def _proxy_state(hostname: str) -> dict[str, Any]:
    raw_no_proxy = (os.getenv("NO_PROXY") or os.getenv("no_proxy") or "").strip()
    entries = [item.strip().lower() for item in raw_no_proxy.split(",") if item.strip()]
    host = hostname.lower()
    covered = False
    for entry in entries:
        if entry == host:
            covered = True
            break
        if entry.startswith(".") and host.endswith(entry):
            covered = True
            break
    return {
        "http_proxy_present": bool((os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or "").strip()),
        "https_proxy_present": bool((os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "").strip()),
        "no_proxy_present": bool(raw_no_proxy),
        "no_proxy_covers_host": covered,
    }


def _sanitize_error_payload(exc: Exception) -> str:
    body = ""
    response = getattr(exc, "response", None)
    if response is not None:
        text = getattr(response, "text", None)
        if isinstance(text, str):
            body = text
    body = body.strip()
    if not body:
        body = str(exc)
    return body[:500]


async def _run(model_ref: str, prompt: str) -> dict[str, Any]:
    auth = AuthManager()
    cfg = ModelConfigManager()
    router = LLMRouter(auth_manager=auth, model_config=cfg)
    route = router._lookup_model_route(model_ref)  # noqa: SLF001
    if route is None:
        raise ValueError(f"Model ref not found in routing registry: {model_ref}")

    client, raw_model, api_type = router._resolve_client(  # noqa: SLF001
        "chat",
        auth_value=route.api_key,
        model_override=model_ref,
    )
    base_url = _normalize_anthropic_base_url(route.base_url)
    final_url = _anthropic_messages_endpoint(base_url) if api_type == "anthropic" else base_url

    header_source = getattr(client, "default_headers", None)
    if not isinstance(header_source, dict):
        inner = getattr(client, "_client", None)
        header_source = getattr(inner, "headers", None)
    headers = {}
    if hasattr(header_source, "items"):
        headers = {str(k).lower(): str(v) for k, v in header_source.items()}

    status_code = None
    sanitized_error = ""
    try:
        await router.complete(
            [{"role": "user", "content": prompt}],
            purpose="chat",
            session_key=f"diag:{model_ref}",
            origin="scripts.diagnose_mimo_provider",
        )
        status_code = 200
    except Exception as exc:  # noqa: BLE001
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
        sanitized_error = _sanitize_error_payload(exc)

    hostname = urlparse(route.base_url).hostname or ""
    return {
        "provider": route.provider_id,
        "protocol": api_type,
        "sdk_type": "anthropic_sdk" if api_type == "anthropic" else "openai_sdk",
        "base_url": base_url,
        "final_url": final_url,
        "raw_model": raw_model,
        "auth_scheme": "bearer" if "authorization" in headers else ("x_api_key" if "x-api-key" in headers else "unknown"),
        "authorization_present": "authorization" in headers,
        "x_api_key_present": "x-api-key" in headers,
        "stream": False,
        "status_code": status_code,
        "sanitized_error_body": sanitized_error,
        "proxy": _proxy_state(hostname),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Raw model id or provider/model ref")
    parser.add_argument("--prompt", default="hi")
    args = parser.parse_args()

    model_ref = args.model
    if "/" not in model_ref:
        model_ref = f"xiaomimimo/{model_ref}"
    result = asyncio.run(_run(model_ref=model_ref, prompt=args.prompt))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
