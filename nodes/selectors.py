"""Two selector nodes.

AllmaComboSelect is a universal combo: it has no fixed list of its own. The JS
side reads the option list off whatever widget you plug it into — following
routers such as a switch until it reaches a real dropdown — and rebuilds itself
as that dropdown. The value travels on a wildcard output, which ComfyUI's
validator accepts into any input (comfy_execution/validation.py short-circuits
to True when either side is '*').

AllmaPresetPrompt turns a preset NAME into the preset's SYSTEM PROMPT TEXT.
That is the piece you actually switch on: AllmaGenerate's own `preset` widget
is client-side only (the server deletes it), so routing a name into it would
change nothing. Route this node's STRING into `system_prompt` instead.
"""
from comfy_api.latest import io

from .preset import list_preset_names, load_preset

LOG = "[ComfyUI-Allma/selectors]"

# The legacy wildcard. Declared as a custom io_type so the V3 schema emits the
# bare "*" the validator special-cases.
AnyOut = io.Custom("*")


def _preset_choices() -> list[str]:
    names = list_preset_names()
    return ["(none)"] + names if names else ["(none)"]


class AllmaComboSelect(io.ComfyNode):
    """A standalone dropdown that borrows its options from its target."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaComboSelect",
            display_name="Combo Select (universal)",
            category="Allma/utils",
            description=(
                "Universal combo selector. Plug it into any dropdown — directly "
                "or through a switch — and it mirrors that dropdown's options."
            ),
            inputs=[
                io.String.Input(
                    "value", default="",
                    tooltip="Connect the output to any dropdown widget on another "
                    "node. This widget then becomes a copy of that dropdown, listing "
                    "the same options, while staying a separate node you can switch "
                    "or route freely.",
                ),
            ],
            outputs=[AnyOut.Output(display_name="value")],
        )

    @classmethod
    def fingerprint_inputs(cls, value, **_kwargs):
        return value

    @classmethod
    def execute(cls, value) -> io.NodeOutput:
        return io.NodeOutput(value)


class AllmaPresetPrompt(io.ComfyNode):
    """Preset name -> the preset's system_prompt text."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaPresetPrompt",
            display_name="Allma Preset Selector",
            category="Allma/llm",
            description="Loads a saved Allma preset and outputs its system prompt.",
            inputs=[
                io.Combo.Input(
                    "preset",
                    options=_preset_choices(),
                    tooltip="Reads presets/<name>.json and outputs its system_prompt. "
                    "Wire it into AllmaGenerate's system_prompt input — AllmaGenerate's "
                    "own preset widget is cosmetic and cannot be driven from the graph.",
                ),
            ],
            # Deliberately a single output. An extra `preset_name` string reads
            # like it belongs in AllmaGenerate's `preset` input, which is
            # client-side only and would silently do nothing.
            outputs=[io.String.Output(display_name="system_prompt")],
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        # Always re-read: editing a preset on disk must take effect without
        # having to dirty the graph.
        return float("nan")

    @classmethod
    def execute(cls, preset) -> io.NodeOutput:
        name = (preset or "").strip()
        if not name or name == "(none)":
            print(f"{LOG} no preset selected — emitting an empty system prompt")
            return io.NodeOutput("")

        data = load_preset(name)
        if data is None:
            raise RuntimeError(
                f"Preset '{name}' not found. It was renamed or deleted after "
                f"ComfyUI started — refresh the browser to repopulate the list. "
                f"Available: {', '.join(list_preset_names()) or '(none)'}"
            )

        text = (data.get("system_prompt") or "").strip()
        print(f"{LOG} loaded preset '{name}' ({len(text)} chars)")
        return io.NodeOutput(text)
