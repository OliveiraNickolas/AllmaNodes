"""AllmaLiveText — a text display that fills in while the model is still typing.

A node output cannot stream: execute() returns once, and the STRING lands
whole. So this node shows the final value like any preview would, and also
subscribes to the live relay that AllmaGenerate pushes over the websocket —
finding its own source by following its input link back through the graph.

Wire it to AllmaGenerate's `thinking` output to watch the reasoning as it is
produced; wire it to `output_prompt` to watch the answer.
"""
from comfy_api.latest import io

LOG = "[ComfyUI-Allma/live_text]"


class AllmaLiveText(io.ComfyNode):
    """Show a STRING, live while it streams and complete when it lands."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaLiveText",
            display_name="Allma Live Text",
            category="Allma/llm",
            description=(
                "Displays a STRING and, when it comes from AllmaGenerate, fills "
                "in live while the model produces it."
            ),
            inputs=[
                io.String.Input(
                    "text", force_input=True,
                    tooltip="Wire AllmaGenerate's 'thinking' here to watch the "
                    "reasoning as it happens, or 'output_prompt' for the answer.",
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
        value = text if isinstance(text, str) else str(text or "")
        return io.NodeOutput(value, ui={"text": [value]})
