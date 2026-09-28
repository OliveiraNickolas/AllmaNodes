"""AllmaFallback — many inputs in, the first one that actually arrived out.

A fallback chain: slot 1, else slot 2, else slot 3. Wired like Allma Bus In —
slots grow as you fill them and anything plugs into anything.

What makes it useful here is what it pairs with. A branch switched off by Allma
Muter is removed from the prompt, so its wire never arrives and the slot reads
empty — the switch falls through to the next one. Three alternative ways of
producing an image can sit side by side, and whichever one is left running is
the one that comes out, with nothing to rewire.

"Valid" means the slot has a value at all. Only `None` is skipped: an empty
string, a zero and an all-black image are values someone meant to send, and
guessing otherwise would silently drop them.

The slots are lazy, and asked for one at a time: an alternative that is never
chosen is never produced. Wire three upscalers into it and only the one that
comes out ever runs.
"""
from comfy_api.latest import io

from .bus import ANY  # wildcard socket type, shared with the data bus

LOG = "[AllmaNodes/fallback]"

MAX_SLOTS = 24
SLOTS = [f"value_{i}" for i in range(1, MAX_SLOTS + 1)]

AnyOut = io.Custom("*")


class AllmaFallback(io.ComfyNode):
    """The first slot that carries a value."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaFallback",
            display_name="Allma Fallback (first valid)",
            category="Allma/logic",
            search_aliases=["fallback", "first", "any switch", "coalesce", "either",
                            "switch", "pick", "route", "alternative"],
            description=(
                "Plug anything in; a new slot appears as you fill the last one. "
                "The output is the first slot that carries a value, in slot "
                "order. A branch switched off with Allma Muter never arrives, so "
                "the switch falls through to the next one — which is how you pick "
                "between alternatives without rewiring anything."
            ),
            inputs=[
                io.Autogrow.Input(
                    "values",
                    template=io.Autogrow.TemplateNames(
                        input=io.Custom(ANY).Input("value", optional=True, lazy=True),
                        names=SLOTS,
                        min=0,
                    ),
                ),
            ],
            outputs=[AnyOut.Output(display_name="output")],
        )

    @classmethod
    def check_lazy_status(cls, values=None) -> list[str] | None:
        """Ask for one slot at a time, stopping at the first that answers.

        Only here do the slots arrive as ``(value, key)`` pairs — ComfyUI sets
        ``create_dynamic_tuple`` for this call so a grown slot can say which flat
        input it is, ``values.value_3``. That is the name to hand back; composing
        it ourselves would be guessing at ComfyUI's spelling.
        """
        group = values or {}
        for name in SLOTS:
            if name not in group:
                continue                       # nothing plugged into this one
            item = group[name]
            pair = isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], str)
            valor, chave = item if pair else (item, f"values.{name}")
            if valor is not None:
                return None                    # already have an answer
            return [chave]                     # produce this one, then look again
        return None

    @classmethod
    def execute(cls, values=None) -> io.NodeOutput:
        group = values or {}
        # Slot order, never connection order: slot 2 stays ahead of slot 3 no
        # matter which was plugged in first.
        for name in SLOTS:
            valor = group.get(name)
            # `is not None` on purpose. A tensor has no truth value, and an empty
            # string or a zero is still something the user chose to send.
            if valor is not None:
                print(f"{LOG} passing {name}")
                return io.NodeOutput(valor)

        raise RuntimeError(
            "Allma Fallback: every slot is empty. Nothing is plugged in, or "
            "every branch that feeds it is switched off — leave one running, or "
            "wire a last slot as the fallback."
        )
