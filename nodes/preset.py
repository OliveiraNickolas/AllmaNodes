"""Preset storage and HTTP endpoints for the JS extension.

A preset is a JSON file in `presets/` with:
    {
      "system_prompt": "...",
      "notes": "any user notes (optional)"
    }
"""
import json
import re
from pathlib import Path

LOG = "[AllmaNodes/presets]"

PRESET_DIR = Path(__file__).resolve().parent.parent / "presets"
PRESET_DIR.mkdir(exist_ok=True)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_\-. ]+")


def _safe(name: str) -> str:
    return _SAFE_NAME.sub("", (name or "").strip())[:80] or "unnamed"


def list_preset_names() -> list[str]:
    try:
        return sorted(p.stem for p in PRESET_DIR.glob("*.json"))
    except Exception as e:
        print(f"{LOG} list failed: {e}")
        return []


def load_preset(name: str) -> dict | None:
    name = _safe(name)
    if not name:
        return None
    f = PRESET_DIR / f"{name}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{LOG} load failed for {name}: {e}")
        return None


def save_preset(name: str, system_prompt: str = "", notes: str = "") -> bool:
    name = _safe(name)
    if not name:
        return False
    data = {"system_prompt": system_prompt or "", "notes": notes or ""}
    try:
        (PRESET_DIR / f"{name}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        print(f"{LOG} save failed for {name}: {e}")
        return False


def delete_preset(name: str) -> bool:
    name = _safe(name)
    if not name:
        return False
    f = PRESET_DIR / f"{name}.json"
    if not f.exists():
        return False
    try:
        f.unlink()
        return True
    except Exception as e:
        print(f"{LOG} delete failed for {name}: {e}")
        return False


_ENDPOINTS_REGISTERED = False


def register_preset_endpoints() -> None:
    """Register HTTP endpoints on the ComfyUI server so the JS extension can call
    them. Idempotent — safe to call more than once."""
    global _ENDPOINTS_REGISTERED
    if _ENDPOINTS_REGISTERED:
        return

    try:
        from aiohttp import web
        from server import PromptServer
    except Exception as e:
        print(f"{LOG} could not register endpoints: {e}")
        return

    routes = PromptServer.instance.routes

    @routes.get("/allma/presets")
    async def _list(_request):
        return web.json_response({"presets": list_preset_names()})

    @routes.get("/allma/presets/{name}")
    async def _get(request):
        name = request.match_info["name"]
        preset = load_preset(name)
        if preset is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(preset)

    @routes.post("/allma/presets")
    async def _save(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        name = body.get("name", "")
        ok = save_preset(name, body.get("system_prompt", ""), body.get("notes", ""))
        if not ok:
            return web.json_response({"error": "save failed"}, status=400)
        return web.json_response({"ok": True, "name": _safe(name)})

    @routes.delete("/allma/presets/{name}")
    async def _delete(request):
        name = request.match_info["name"]
        ok = delete_preset(name)
        if not ok:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"ok": True})

    _ENDPOINTS_REGISTERED = True
    print(f"{LOG} registered HTTP endpoints under /allma/presets")
