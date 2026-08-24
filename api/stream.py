"""Live relay of the model's reasoning to the node in the browser.

The SSE loop already sees every reasoning token as it arrives — it just used
to accumulate them silently until the answer was complete. This forwards them
over the websocket ComfyUI already holds open, so a run that has gone into a
thinking loop is visible while it happens instead of ten minutes later.

Chunks are batched: one websocket message per token would flood the socket and
stall the canvas. Text is flushed on a size or time threshold, whichever comes
first, which keeps it feeling live without the traffic.
"""
import time

LOG = "[AllmaNodes/stream]"
EVENT = "allma.stream"

FLUSH_CHARS = 160     # a sentence or so
FLUSH_SECONDS = 0.25  # still feels immediate, ~4 messages/s ceiling


def _send(payload: dict) -> None:
    try:
        from server import PromptServer
        PromptServer.instance.send_sync(EVENT, payload)
    except Exception as e:
        # Never let a UI relay break a generation.
        print(f"{LOG} could not send: {e}")


class Relay:
    """Batches reasoning/content text and pushes it to one node's widget."""

    def __init__(self, node_id: str):
        self.node_id = str(node_id or "")
        self._buf: list[str] = []
        self._n = 0
        self._last = time.time()
        self._kind: str | None = None
        self.total = 0

    def start(self) -> None:
        _send({"node": self.node_id, "event": "start"})

    def add(self, text: str, kind: str = "reasoning") -> None:
        if not text or not self.node_id:
            return
        # One buffer serves both channels, so a switch from reasoning to content
        # has to flush first. Without this the pending text goes out labelled
        # with whichever kind happened to arrive last, and reasoning lands in the
        # answer box (or the reverse) — the two interleave freely on backends
        # that emit them in the same stream.
        if self._kind is not None and kind != self._kind:
            self.flush(self._kind)
        self._kind = kind
        self._buf.append(text)
        self._n += len(text)
        self.total += len(text)
        now = time.time()
        if self._n >= FLUSH_CHARS or (now - self._last) >= FLUSH_SECONDS:
            self.flush(kind)

    def flush(self, kind: str = "reasoning") -> None:
        if not self._buf:
            return
        _send({
            "node": self.node_id, "event": "chunk", "kind": kind,
            "text": "".join(self._buf), "total": self.total,
        })
        self._buf.clear()
        self._n = 0
        self._last = time.time()

    def done(self, note: str = "") -> None:
        self.flush(self._kind or "reasoning")
        _send({
            "node": self.node_id, "event": "done",
            "total": self.total, "note": note,
        })
