"""ComfyUI-Allma — Fase 1 MVP

Talks to the Allma backend (OpenAI-compatible) with two nodes:
  - AllmaConnectivity: host/port/model/sampling
  - AllmaGenerate:     images + audio + system/user prompt → text
Plus preset CRUD via HTTP + JS extension for the preset buttons.
"""
from .api.lora_intercept import install as _install_lora_intercept
from .api.state import register_state_endpoints
from .nodes.connectivity import AllmaConnectivity
from .nodes.generate import AllmaGenerate
from .nodes.load_image import AllmaLoadImage
from .nodes.preset import register_preset_endpoints

try:
    _install_lora_intercept()
except Exception as _e:
    print(f"[ComfyUI-Allma] lora intercept not installed: {_e}")

NODE_CLASS_MAPPINGS = {
    "AllmaConnectivity": AllmaConnectivity,
    "AllmaGenerate": AllmaGenerate,
    "AllmaLoadImage": AllmaLoadImage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AllmaConnectivity": "Allma Connectivity",
    "AllmaGenerate": "Allma Generate",
    "AllmaLoadImage": "Allma Load Image",
}

WEB_DIRECTORY = "./web"

try:
    register_preset_endpoints()
except Exception as e:
    print(f"[ComfyUI-Allma] preset endpoints not registered: {e}")

try:
    register_state_endpoints()
except Exception as e:
    print(f"[ComfyUI-Allma] state endpoint not registered: {e}")

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
