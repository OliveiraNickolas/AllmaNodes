"""ComfyUI-Allma — Fase 1 MVP

Talks to the Allma backend (OpenAI-compatible) with two nodes:
  - AllmaConnectivity: host/port/model/sampling
  - AllmaGenerate:     images + audio + system/user prompt → text
Plus preset CRUD via HTTP + JS extension for the preset buttons.
"""
from .nodes.connectivity import AllmaConnectivity
from .nodes.generate import AllmaGenerate
from .nodes.preset import register_preset_endpoints

NODE_CLASS_MAPPINGS = {
    "AllmaConnectivity": AllmaConnectivity,
    "AllmaGenerate": AllmaGenerate,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AllmaConnectivity": "Allma Connectivity",
    "AllmaGenerate": "Allma Generate",
}

WEB_DIRECTORY = "./web"

try:
    register_preset_endpoints()
except Exception as e:
    print(f"[ComfyUI-Allma] preset endpoints not registered: {e}")

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
