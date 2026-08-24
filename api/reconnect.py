"""GET /allma/models — re-ask the backend which models it has, right now.

The dropdown on AllmaConnectivity is built from define_schema, which runs once
when ComfyUI imports the pack. If the backend was down, still loading, or stuck
in an error state at that moment, the list is empty or stale and stays that way
until ComfyUI is restarted — a heavy price for a backend that came up thirty
seconds late.

This endpoint bypasses the process-wide cache so the answer reflects the backend
as it is at the moment of asking, and reports the backend's own health alongside
the list. A model list that comes back empty with an error attached is a very
different situation from a backend that genuinely serves nothing, and the button
in the UI can say which.
"""
import json
import urllib.request

from .allma_client import list_models

LOG = "[AllmaNodes/reconnect]"
_REGISTERED = False


def _health(host: str, port: int, timeout: float = 4.0) -> dict:
    """Whatever the backend says about what it has loaded and what failed.

    Allma exposes /v1/ps; other OpenAI-compatible backends do not, and their 404
    is not a fault — it just means there is nothing extra to report.
    """
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/v1/ps",
            headers={"Authorization": "Bearer dummy"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return {}
    errors = data.get("errors") or {}
    loaded = [s.get("model") or s.get("id") for s in (data.get("servers") or [])]
    out: dict = {}
    if loaded:
        out["loaded"] = [m for m in loaded if m]
    if errors:
        # Surfaced verbatim: a stuck load ("tensor_parallel_failed") makes every
        # later request fail with a bare HTTP 500, which tells the user nothing.
        first = next(iter(errors.values()), {})
        out["error"] = first.get("explanation") or first.get("error_type") or "unknown"
        out["error_model"] = next(iter(errors), "")
    return out


def register_reconnect_endpoint() -> None:
    """Idempotent — safe to call on every import."""
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception as e:
        print(f"{LOG} could not register endpoint: {e}")
        return

    @PromptServer.instance.routes.get("/allma/models")
    async def _models(request):
        host = request.query.get("host") or "127.0.0.1"
        try:
            port = int(request.query.get("port") or 9000)
        except ValueError:
            port = 9000
        models = list_models(host, port, timeout=6.0)
        body = {"models": models, "host": host, "port": port}
        body.update(_health(host, port))
        print(f"{LOG} {host}:{port} -> {len(models)} model(s)"
              + (f", backend error: {body['error']}" if body.get("error") else ""))
        return web.json_response(body)

    _REGISTERED = True
    print(f"{LOG} registered HTTP endpoint /allma/models")
