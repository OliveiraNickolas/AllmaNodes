"""AllmaConnectivity node — holds host/port + model + sampling in one place."""
from ..api.allma_client import list_models
from ..api.state import get_state, set_state

_MODELS_CACHE: list[str] = []
_MODELS_CACHE_HOST = ""
_MODELS_CACHE_PORT = 0


def _fetch_models(host: str = "127.0.0.1", port: int = 9000) -> list[str]:
    global _MODELS_CACHE, _MODELS_CACHE_HOST, _MODELS_CACHE_PORT
    if _MODELS_CACHE and _MODELS_CACHE_HOST == host and _MODELS_CACHE_PORT == port:
        return _MODELS_CACHE
    models = list_models(host, port, timeout=3.0)
    if models:
        _MODELS_CACHE = models
        _MODELS_CACHE_HOST = host
        _MODELS_CACHE_PORT = port
    return models or _MODELS_CACHE


class AllmaConnectivity:
    """Where to reach the Allma backend and how to sample from it.

    Output is a plain dict that AllmaGenerate consumes."""

    @classmethod
    def INPUT_TYPES(cls):
        models = _fetch_models() or ["(allma offline — check host/port)"]
        # New nodes default to whatever model was last actually used, so you
        # don't have to re-pick it every time you drop the node into a new
        # workflow. The JS extension covers the same-session case via
        # GET /allma/state.
        last = get_state().get("last_model")
        model_opts = {"default": last} if last in models else {}
        return {
            "required": {
                "host": ("STRING", {"default": "127.0.0.1"}),
                "port": ("INT", {"default": 9000, "min": 1, "max": 65535}),
                "timeout": ("INT", {"default": 120, "min": 5, "max": 3600,
                                     "tooltip": "Max seconds per request. Bump for slow first-time loads."}),
                "model": (models, model_opts),
                "temperature": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05}),
                "top_p": ("FLOAT", {"default": 0.95, "min": 0.0, "max": 1.0, "step": 0.01}),
                "top_k": ("INT", {"default": 20, "min": 0, "max": 500,
                                   "tooltip": "0 disables top_k."}),
                "max_tokens": ("INT", {"default": 2048, "min": 16, "max": 131072}),
                "seed": ("INT", {"default": -1, "min": -1, "max": 2**31 - 1,
                                  "tooltip": "-1 lets the backend choose (or is ignored)."}),
                "show_sampling": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "When OFF, hides the 5 sampling widgets (temperature, top_p, "
                    "top_k, max_tokens, seed) so you don't nudge them by mistake while "
                    "dragging the node. Their values are preserved either way.",
                }),
            }
        }

    RETURN_TYPES = ("ALLMA_CONNECTIVITY",)
    RETURN_NAMES = ("connectivity",)
    FUNCTION = "build"
    CATEGORY = "Allma"

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")

    def build(self, host, port, timeout, model, temperature, top_p, top_k, max_tokens, seed,
              show_sampling=False):
        conn = {
            "host": host.strip() or "127.0.0.1",
            "port": int(port),
            "timeout": int(timeout),
            "model": model,
            "temperature": float(temperature),
            "top_p": float(top_p),
            "top_k": int(top_k),
            "max_tokens": int(max_tokens),
            "seed": int(seed),
        }
        if isinstance(model, str) and model and not model.startswith("("):
            set_state(last_model=model)
        return (conn,)
