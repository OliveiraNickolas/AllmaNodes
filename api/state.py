"""Tiny persistent key-value state for the plugin (last-used model, etc).

Stored as `state.json` in the plugin root, gitignored. Read/write is
best-effort — a corrupt or missing file just means defaults.
"""
import json
from pathlib import Path

LOG = "[AllmaNodes/state]"

_STATE_FILE = Path(__file__).resolve().parent.parent / "state.json"


def get_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_state(**kwargs) -> None:
    state = get_state()
    state.update({k: v for k, v in kwargs.items() if v is not None})
    try:
        _STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        print(f"{LOG} could not persist state: {e}")


_ENDPOINT_REGISTERED = False


def register_state_endpoints() -> None:
    """GET /allma/state — lets the JS extension read the last-used model so
    freshly added AllmaConnectivity nodes can default to it without a page
    refresh. Idempotent."""
    global _ENDPOINT_REGISTERED
    if _ENDPOINT_REGISTERED:
        return
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception as e:
        print(f"{LOG} could not register endpoint: {e}")
        return

    @PromptServer.instance.routes.get("/allma/state")
    async def _get_state(_request):
        return web.json_response(get_state())

    _ENDPOINT_REGISTERED = True
    print(f"{LOG} registered HTTP endpoint /allma/state")
