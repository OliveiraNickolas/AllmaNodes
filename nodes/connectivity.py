"""AllmaConnectivity node — holds host/port + model + sampling in one place."""
from comfy_api.latest import io

from ..api.allma_client import list_models
from ..api.state import get_state, set_state

AllmaConnectivityType = io.Custom("ALLMA_CONNECTIVITY")

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


class AllmaConnectivity(io.ComfyNode):
    """Where to reach the Allma backend and how to sample from it.

    Output is a plain dict that AllmaGenerate consumes."""

    @classmethod
    def define_schema(cls):
        models = _fetch_models() or ["(allma offline — check host/port)"]
        # New nodes default to whatever model was last actually used, so you
        # don't have to re-pick it every time you drop the node into a new
        # workflow. The JS extension covers the same-session case via
        # GET /allma/state.
        last = get_state().get("last_model")
        default_model = last if last in models else None
        return io.Schema(
            node_id="AllmaConnectivity",
            display_name="Allma Connectivity",
            category="Allma/llm",
            inputs=[
                io.String.Input("host", default="127.0.0.1"),
                io.Int.Input("port", default=9000, min=1, max=65535),
                io.Int.Input(
                    "timeout", default=120, min=5, max=3600,
                    tooltip="Max seconds per request. Bump for slow first-time loads.",
                ),
                io.Combo.Input("model", options=models, default=default_model),
                io.Float.Input("temperature", default=1.0, min=0.0, max=2.0, step=0.05),
                io.Float.Input("top_p", default=0.95, min=0.0, max=1.0, step=0.01),
                io.Int.Input("top_k", default=20, min=0, max=500,
                             tooltip="0 disables top_k."),
                io.Int.Input("max_tokens", default=2048, min=16, max=131072),
                io.Int.Input(
                    "seed", default=-1, min=-1, max=2**31 - 1,
                    tooltip="-1 lets the backend choose (or is ignored).",
                ),
                io.Boolean.Input(
                    "show_sampling", default=False,
                    tooltip="When OFF, hides the 5 sampling widgets (temperature, "
                    "top_p, top_k, max_tokens, seed) so you don't nudge them by "
                    "mistake while dragging the node. Their values are preserved "
                    "either way.",
                ),
            ],
            outputs=[AllmaConnectivityType.Output(display_name="connectivity")],
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        return float("nan")

    @classmethod
    def execute(cls, host, port, timeout, model, temperature, top_p, top_k,
                max_tokens, seed, show_sampling=False) -> io.NodeOutput:
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
        return io.NodeOutput(conn)
