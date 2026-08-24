"""AllmaConnectivity node — holds host/port + model + sampling in one place."""
from comfy_api.latest import io

from ..api.allma_client import list_models
from ..api.state import get_state, set_state

AllmaConnectivityType = io.Custom("ALLMA_CONNECTIVITY")

# How long the model reasons, sent as chat_template_kwargs.reasoning_effort.
#
# Three levels because three is all the model distinguishes. The Qwen 3.5/3.6/3.8
# template accepts a wider vocabulary but folds it: 'minimal' lands on low,
# 'high' and 'max' land on xhigh, and anything it does not recognise — 'ultra'
# included — silently becomes medium. Offering those aliases would be offering
# the same three settings under seven names.
#
# 'none' is deliberately absent: it means "no thinking", which is what the
# `thinking` toggle above already does.
EFFORT_CHOICES = ["low", "medium", "xhigh"]
EFFORT_DEFAULT = "medium"

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
                io.Boolean.Input(
                    "thinking", default=False,
                    tooltip="OFF: the model skips the <think> block and answers "
                    "directly. ON: it reasons first, and the reasoning comes back "
                    "on Allma Generate's 'thinking' output instead of leaking into "
                    "'output_prompt'. Note the reasoning spends the SAME max_tokens "
                    "budget as the answer.",
                ),
                io.Combo.Input(
                    "effort", options=EFFORT_CHOICES, default=EFFORT_DEFAULT,
                    tooltip="How long the model reasons when 'thinking' is ON. Not a token "
                    "budget and it does not lower answer quality — it changes how "
                    "much the model narrates its way to the answer. A .allm profile "
                    "declaring @reasoning-effort overrides this.",
                ),
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
                max_tokens, seed, thinking=False, effort=EFFORT_DEFAULT,
                show_sampling=False) -> io.NodeOutput:
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
            "thinking": bool(thinking),
            # Only a real pick travels. The empty string is the signal to leave
            # chat_template_kwargs.reasoning_effort out of the request entirely,
            # so the .allm profile (or the model's template) keeps ownership.
            "reasoning_effort": effort,
        }
        if isinstance(model, str) and model and not model.startswith("("):
            set_state(last_model=model)
        return io.NodeOutput(conn)
