"""Cancellation token for in-flight Allma generations.

The node runs on ComfyUI's worker thread while the HTTP endpoint runs on the
aiohttp event loop, so the stop signal has to cross threads. Two mechanisms,
because either alone leaves a gap:

  - a threading.Event, checked between SSE chunks (clean stop, keeps partial
    text);
  - closing the live HTTPResponse, which unblocks a read that is stuck waiting
    on a long prefill where no chunk has arrived yet.

Closing the socket is also what tells llama.cpp to abort: it notices the client
disconnect and frees the slot, instead of grinding on an answer nobody will
read (which is what made timed-out requests pile up before).
"""
import threading

LOG = "[AllmaNodes/interrupt]"

_LOCK = threading.Lock()
_CANCEL = threading.Event()
_LIVE_RESPONSE = None
_RUN_ID = 0


def begin_run() -> int:
    """Arm a fresh token. Returns the run id so a stale stop can't kill it."""
    global _CANCEL, _LIVE_RESPONSE, _RUN_ID
    with _LOCK:
        _RUN_ID += 1
        _CANCEL = threading.Event()
        _LIVE_RESPONSE = None
        return _RUN_ID


def register_response(resp) -> None:
    """Publish the live response so request_interrupt() can close it."""
    global _LIVE_RESPONSE
    with _LOCK:
        _LIVE_RESPONSE = resp


def clear_response() -> None:
    global _LIVE_RESPONSE
    with _LOCK:
        _LIVE_RESPONSE = None


def is_cancelled() -> bool:
    return _CANCEL.is_set()


def request_interrupt() -> dict:
    """Signal the running generation to stop. Safe to call when nothing runs."""
    with _LOCK:
        _CANCEL.set()
        resp = _LIVE_RESPONSE
        run_id = _RUN_ID
    live = False
    if resp is not None:
        live = True
        try:
            # Unblocks a stuck read and drops the connection, which llama.cpp
            # reads as "client gone" and stops generating.
            resp.close()
        except Exception as e:
            print(f"{LOG} could not close live response: {e}")
    print(f"{LOG} interrupt requested (run={run_id}, live={live})")
    return {"ok": True, "live": live, "run_id": run_id}


_ENDPOINT_REGISTERED = False


def register_interrupt_endpoints() -> None:
    """POST /allma/interrupt — stop the current generation.
    GET  /allma/interrupt/status — whether a generation is in flight."""
    global _ENDPOINT_REGISTERED
    if _ENDPOINT_REGISTERED:
        return
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception as e:
        print(f"{LOG} could not register endpoints: {e}")
        return

    routes = PromptServer.instance.routes

    @routes.post("/allma/interrupt")
    async def _interrupt(_request):
        return web.json_response(request_interrupt())

    @routes.get("/allma/interrupt/status")
    async def _status(_request):
        with _LOCK:
            running = _LIVE_RESPONSE is not None
            run_id = _RUN_ID
        return web.json_response(
            {"running": running, "run_id": run_id, "cancelled": _CANCEL.is_set()}
        )

    _ENDPOINT_REGISTERED = True
    print(f"{LOG} registered HTTP endpoints /allma/interrupt")
