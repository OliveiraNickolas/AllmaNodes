"""ComfyUI-Allma

Talks to the Allma backend (OpenAI-compatible):
  - AllmaConnectivity: host/port/model/sampling
  - AllmaGenerate:     images + audio + system/user prompt → text
  - AllmaLoadImage:    image + its embedded prompt metadata on one link
  - AllmaComboSelect:  a dropdown that mirrors whatever it is plugged into
  - AllmaPresetPrompt: preset name → the preset's system prompt
  - AllmaGate:         pass a value through, or emit null when switched off
Plus preset CRUD over HTTP and a JS extension for the inline controls.

Registered through comfy_entrypoint because AllmaGenerate needs the V3 schema
for its Autogrow image/audio slots. ComfyUI takes NODE_CLASS_MAPPINGS *or*
comfy_entrypoint per module (nodes.py picks the first and returns), so every
node here has to be a V3 io.ComfyNode.
"""
from comfy_api.latest import ComfyExtension, io

from .api.interrupt import register_interrupt_endpoints
from .api.lora_intercept import install as _install_lora_intercept
from .api.state import register_state_endpoints
from .nodes.connectivity import AllmaConnectivity
from .nodes.gate import AllmaGate
from .nodes.generate import AllmaGenerate
from .nodes.live_text import AllmaLiveText
from .nodes.load_image import AllmaLoadImage
from .nodes.preset import register_preset_endpoints
from .nodes.selectors import AllmaComboSelect, AllmaPresetPrompt
from .nodes.vram import AllmaClearVRAM

try:
    _install_lora_intercept()
except Exception as _e:
    print(f"[ComfyUI-Allma] lora intercept not installed: {_e}")

for _register, _label in (
    (register_preset_endpoints, "preset endpoints"),
    (register_state_endpoints, "state endpoint"),
    (register_interrupt_endpoints, "interrupt endpoints"),
):
    try:
        _register()
    except Exception as _e:
        print(f"[ComfyUI-Allma] {_label} not registered: {_e}")

WEB_DIRECTORY = "./web"


class AllmaExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            AllmaConnectivity,
            AllmaGenerate,
            AllmaGate,
            AllmaLoadImage,
            AllmaLiveText,
            AllmaComboSelect,
            AllmaPresetPrompt,
            AllmaClearVRAM,
        ]


async def comfy_entrypoint() -> AllmaExtension:
    return AllmaExtension()


__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
