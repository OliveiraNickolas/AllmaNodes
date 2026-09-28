"""AllmaLiveText — a text display that fills in while its source is still working.

A node output cannot stream: execute() returns once, and the value lands whole.
So this node shows the final value like any preview would, and while the source
is still running it shows whatever that source reports along the way:

- from AllmaGenerate, the text itself, token by token, over the live relay;
- from ANY other node, ComfyUI's own progress (``340/3000``, rate, time left) and
  any progress text the node publishes — the frontend listens for the node the
  input is wired to, whatever it is.

Wire it to AllmaGenerate's `thinking` output to watch the reasoning as it is
produced; wire it to `output_prompt` to watch the answer; wire it to anything
else to watch it progress and read the result.

Any type is accepted, the way Preview Any does: a STRING is shown as-is,
structures as indented JSON, tensors as a one-line summary rather than a dump.
"""
import json

from comfy_api.latest import io

from .bus import ANY  # wildcard socket type, shared with the data bus

LOG = "[AllmaNodes/live_text]"

# A value nested deep in a structure gets at most this much text of its own, so
# one embedded tensor or blob cannot turn the display into megabytes of digits.
MAX_ITEM_CHARS = 2000


def _summary(value):
    """One line for things that have no useful text form (tensors, arrays)."""
    shape = getattr(value, "shape", None)
    dtype = getattr(value, "dtype", None)
    if shape is not None and dtype is not None:
        return f"{type(value).__name__} shape={tuple(shape)} dtype={dtype}"
    text = str(value)
    return text if len(text) <= MAX_ITEM_CHARS else text[:MAX_ITEM_CHARS] + "…"


def as_text(value) -> str:
    """Whatever arrives, as something a person can read."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, (bool, int, float)):
        return str(value)
    if getattr(value, "shape", None) is not None:
        return _summary(value)
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=_summary)
    except (TypeError, ValueError):
        return _summary(value)


class AllmaLiveText(io.ComfyNode):
    """Show any value, with live progress while its source runs."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaLiveText",
            display_name="Allma Live Text",
            category="Allma/llm",
            description=(
                "Displays any value as text. While the source node runs it shows "
                "live progress; when the source is AllmaGenerate it shows the text "
                "itself as the model produces it."
            ),
            inputs=[
                io.Custom(ANY).Input(
                    "text",
                    tooltip="Wire any output here. AllmaGenerate's 'thinking' or "
                    "'output_prompt' stream live; other nodes show their progress "
                    "while running and their value when done.",
                ),
            ],
            outputs=[io.String.Output(display_name="text")],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        return float("nan")

    @classmethod
    def execute(cls, text) -> io.NodeOutput:
        value = as_text(text)
        return io.NodeOutput(value, ui={"text": [value]})
