"""A stop button you can put anywhere, including outside the subgraph.

Allma Generate carries its own stop button, which is fine until the node lives
inside a subgraph: reaching it means opening the subgraph mid-run, and by then
the point of a stop button — clicking it quickly — is gone.

The interrupt endpoint is global rather than per-node, so a button placed at the
root of the workflow cancels the generation exactly as the one on the node does.
Nothing about this node participates in execution; it exists to hold the button
where you can see it.
"""
from comfy_api.latest import io


class AllmaStop(io.ComfyNode):
    """Cancels whatever generation is in flight."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaStop",
            display_name="Allma Stop",
            category="Allma/llm",
            search_aliases=["stop", "cancel", "interrupt", "abort"],
            description=(
                "A stop button for the current Allma generation, placeable "
                "anywhere — the interrupt is global, so it does not need to sit "
                "next to the Generate node it cancels. Useful when Generate is "
                "buried in a subgraph."
            ),
            inputs=[],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls) -> io.NodeOutput:
        # Nothing to do: the button talks to /allma/interrupt from the browser.
        # The node still declares itself an output node so ComfyUI keeps it in
        # the graph rather than pruning a node that feeds nothing.
        return io.NodeOutput()
