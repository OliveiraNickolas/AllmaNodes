"""Allma Muter — desligar um ramo inteiro do grafo com um boolean.

Por que isto existe
-------------------
Bypassar ou mutar um node muda o GRAFO, e o grafo congela no momento em que a
fila recebe o prompt. Um boolean muda um VALOR. Os dois não são intercambiáveis,
e é por isso que nenhuma combinação de nodes nativos liga e desliga um slot de
imagem em tempo de execução:

- ``ExecutionBlocker`` não serve: ``execution.py`` varre TODOS os inputs sem
  distinguir opcional de obrigatório, então um bloqueio em qualquer entrada mata
  o node inteiro em vez de pular só aquela entrada.
- ``ComfySwitchNode`` exige os dois ramos ligados, e não existe node nativo que
  produza "nada" para alimentar o ramo desligado.
- ``ComfySoftSwitchNode`` tolera um ramo faltando, mas aí devolve sempre o outro
  — ele nunca emite nulo.

A saída é entregar ``None``. Nodes que aceitam entrada opcional já sabem lidar:
``MiniMaxH3ReferenceToVideo``, por exemplo, faz ``if img is None: continue`` e
simplesmente ignora o slot. Assim o boolean deixa de precisar mexer na topologia
— o slot continua ligado, só chega vazio.

O input é lazy DE PROPÓSITO: com o gate desligado, nada acima dele executa. É o
que faz o ``ImageResizeKJv2`` de um slot não usado nem rodar, em vez de rodar e
ter o resultado descartado.

Nao e um bypass
---------------
Este node OMITE uma entrada opcional; ele nao pula uma etapa. Para "processa ou
nao processa, mas o fluxo segue", o node nativo ``ComfySwitchNode`` (If/Else
Switch) e o certo: ele tambem e lazy, entao o ramo nao escolhido nao executa, e
como exige os dois lados ligados o destino sempre recebe um valor real.

    images ---+--------------------------> on_false
              +--> SeedVR2VideoUpscaler --> on_true
                                             switch --> resto do grafo

Sem nada ligado
---------------
A entrada e opcional de proposito. Um gate com o `value` solto emite nulo, que e
exatamente o que se quer num slot de referencia ainda nao usado — nao e preciso
mutar nem apagar o node para o grafo validar. Antes essa entrada era obrigatoria
e um slot vazio invalidava a saida inteira do workflow.

Cuidado
-------
A saída é ``None`` num pino tipado. Só ligue em entradas que aceitem ausência —
tipicamente as declaradas ``optional``. Um node que assume valor presente vai
levantar ``AttributeError``/``TypeError`` ao receber nulo, e o erro vai apontar
para ele, não para este gate.
"""
from comfy_api.latest import io

from .bus import ANY  # wildcard socket type, shared with the data bus

# Distinguishes "nothing is wired here" from "wired, not evaluated yet" — the
# lazy machinery passes None for the second, so None alone cannot tell them
# apart. Same trick the built-in Switch uses.
_MISSING = object()

# Total branches one muter governs. Slot 1 keeps the original `value` name and
# output position so every muter already placed in a workflow keeps its wiring;
# slots 2..N are the ones that grow.
MAX_BRANCHES = 25
SLOTS = [f"value_{i}" for i in range(1, MAX_BRANCHES + 1)]
TOGGLES = [f"on_{i}" for i in range(1, MAX_BRANCHES + 1)]

# "none" plus one entry per branch; the browser hides the ones with no wire.
SOLO_OPTIONS = ["none"] + [str(i) for i in range(1, MAX_BRANCHES + 1)]


def _unpack(item, slot):
    """A check_lazy_status slot as (value, flat key), whichever shape it arrives in.

    The pair only exists while ComfyUI asks what is still needed; anywhere else
    the slot is the value itself. Composing the key is the fallback, and it has
    to match how the schema is flattened: the Autogrow input's id, a dot, the
    slot name.
    """
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], str):
        return item
    return item, f"values.{slot}"


class AllmaMuter(io.ComfyNode):
    """Passa o valor quando ligado; muta o ramo atras e emite None quando desligado."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaMuter",
            display_name="Allma Muter",
            category="Allma/logic",
            search_aliases=["mute", "muter", "gate", "null", "none", "bypass",
                            "disable", "toggle", "skip", "branch"],
            description=(
                "Point it at the branches you want to switch off: wire any "
                "output into a slot and a toggle appears for it. Nothing passes "
                "through — the real wire still runs straight from the source to "
                "whatever consumes it. Switching a branch off mutes the node it "
                "points at, and everything feeding only that node, exactly as "
                "Ctrl+M does. The master toggle sets every branch at once. "
                "Wired through Allma Bus Out, a branch switches off whatever "
                "fills that slot on Allma Bus In, never the bus itself."
            ),
            inputs=[
                io.Boolean.Input(
                    "enabled", default=True, socketless=True,
                    tooltip="Master switch. Flipping it sets every branch toggle "
                    "at once; each branch can still be set on its own afterwards.",
                ),
                # Slots appear as the previous one fills, so a muter governing
                # one branch stays one row tall.
                io.Autogrow.Input(
                    "values",
                    template=io.Autogrow.TemplateNames(
                        # lazy: with the fallback output wired, only the branches
                        # the answer actually needs are ever asked for.
                        input=io.Custom(ANY).Input("value", optional=True, lazy=True),
                        names=SLOTS, min=0,
                    ),
                ),
                # One toggle per branch. Declared here rather than added from JS
                # so they serialize predictably and reach the Parameters panel.
                # Connectable, but read in the BROWSER rather than at run time.
                #
                # Muting edits the graph, which is fixed the moment you queue,
                # while a value on a link only exists once the graph is already
                # running — far too late. So when one of these is wired, the
                # frontend follows the link and reads the source's own widget,
                # before submit. That works for a literal (a Boolean primitive,
                # or a subgraph input promoted from one) and not for a value some
                # node computes, which cannot be known until it runs.
                # The solo picker. A shortcut for "only this one": choosing a
                # number switches that branch on and every other off. Touching
                # any toggle by hand puts it back to none, because the promise
                # it makes — exactly one branch live — stopped being true.
                #
                # Declared here rather than added from JS for the same reason as
                # the toggles: a widget in the schema serializes predictably and
                # reaches the Parameters panel. The browser narrows the list to
                # the branches that actually have a wire.
                io.Combo.Input("solo", options=SOLO_OPTIONS, default="none", optional=True, socketless=True,
                               tooltip="Switch on one branch and only that one. "
                                       "Flipping any toggle by hand clears it."),
                *[io.Boolean.Input(t, default=True, optional=True,
                                   tooltip="This branch on its own. A wire here "
                                           "must come from a literal boolean — "
                                           "it is read before the graph runs.")
                  for t in TOGGLES],
            ],
            # One optional output: the first branch still switched on.
            #
            # Leave it unwired and nothing changes — the node has no consumer, so
            # it is pruned before the graph runs and stays what it always was: a
            # control surface whose work happens in the browser, with the real
            # wires running straight from each source to whatever uses them.
            #
            # Wire it and the same switches become a fallback chain: branch 1 if
            # it is on, else branch 2, else branch 3. The switch that silences a
            # branch now also decides which one comes out, so three ways of making
            # the same image can sit side by side with nothing to rewire. Allma
            # Fallback does the same on its own, for when the choice has nothing
            # to do with muting.
            # Then one BOOLEAN per switch: whether that branch is on. Output 0
            # stays the fallback so every muter already wired keeps its links;
            # the switches follow as outputs 1..25 (the browser shows only the
            # ones whose branch has a wire).
            outputs=[
                io.Custom(ANY).Output(display_name="fallback"),
                *[io.Boolean.Output(t, display_name=str(i),
                                    tooltip="Whether this branch's switch is on.")
                  for i, t in enumerate(TOGGLES, start=1)],
            ],
            # Who reads output 0: with only the booleans wired, no branch may be
            # produced just to answer them.
            hidden=[io.Hidden.unique_id, io.Hidden.dynprompt],
        )

    @classmethod
    def _branches_on(cls, rest):
        """Slot names whose own switch is on, in slot order."""
        return [slot for slot, toggle in zip(SLOTS, TOGGLES) if rest.get(toggle, True) is not False]

    @classmethod
    def _fallback_wired(cls):
        """Is the fallback output (0) consumed by some node in this run?

        When unsure (no prompt info), answer yes: that is the old behaviour.
        """
        try:
            dp, me = cls.hidden.dynprompt, str(cls.hidden.unique_id)
            if dp is None or not me:
                return True
            for nid in dp.all_node_ids():
                for v in (dp.get_node(nid) or {}).get("inputs", {}).values():
                    if isinstance(v, list) and len(v) == 2 and str(v[0]) == me and v[1] == 0:
                        return True
            return False
        except Exception:
            return True

    @classmethod
    def _switch_states(cls, rest):
        """One boolean per switch, in order (the outputs after the fallback)."""
        return [rest.get(t, True) is not False for t in TOGGLES]

    @classmethod
    def check_lazy_status(cls, enabled, values=None, **rest):
        """Ask for one branch at a time, and only while the answer is missing.

        This runs at all only when the fallback output is wired — with nothing
        consuming it the node is pruned and never reaches here, which is why a
        muter used purely as a control surface still costs nothing.

        Asked in slot order, one per round: the first branch that comes back
        with a value ends it, so branch 2 is never produced when branch 1
        answered. A branch switched off is skipped outright — running the very
        thing the user silenced is the one thing this must not do.

        Only HERE the values arrive as ``(value, key)`` pairs: ComfyUI sets
        ``create_dynamic_tuple`` for this call so a dynamic slot can say which
        flat input it came from — ``values.value_3``, the name the executor
        knows it by. Taking the key from the pair instead of composing it keeps
        that spelling ComfyUI's business, not ours. ``execute`` gets the bare
        values, as usual.
        """
        if not cls._fallback_wired():
            return None                       # only the switch outputs are read
        group = values or {}
        for slot in cls._branches_on(rest):
            if slot not in group:
                continue                      # nothing plugged into this one
            valor, chave = _unpack(group[slot], slot)
            if valor is not None:
                return None                   # already have an answer
            return [chave]                    # produce this one, then look again
        return None

    @classmethod
    def execute(cls, enabled, values=None, **rest) -> io.NodeOutput:
        switches = cls._switch_states(rest)
        group = values or {}
        for slot in cls._branches_on(rest):
            valor = group.get(slot)
            # `is not None` on purpose: an empty string, a zero and an all-black
            # image are values, and a tensor has no truth value to test.
            if valor is not None:
                return io.NodeOutput(valor, *switches)
        # Unwired output: ComfyUI ignores the value, so None is simply "nothing
        # to pass on". Wired and empty, the consumer will say so far more
        # clearly than this node could.
        return io.NodeOutput(None, *switches)


class AllmaBypasser(AllmaMuter):
    """Same node, same wiring, but the branches are BYPASSED rather than muted.

    Muting removes a node from the graph: whatever it fed sees an unconnected
    input. Bypassing keeps it in place and passes its input through to its
    output, so the branch is skipped while the chain around it stays whole —
    which is what you want for a stage you are stepping over, not a branch you
    are switching off.

    Everything else is inherited. Only the mode the toggles apply differs, and
    that lives in the frontend, so this is identity alone.
    """

    @classmethod
    def define_schema(cls):
        schema = super().define_schema()
        schema.node_id = "AllmaBypasser"
        schema.display_name = "Allma Bypasser"
        schema.category = "Allma/logic"
        schema.search_aliases = ["bypass", "skip", "passthrough", "branch", "toggle"]
        schema.description = (
            "Point it at the stages you want to step over: wire any output into "
            "a slot and a toggle appears for it. Switching a branch off bypasses "
            "the node it points at, exactly as Ctrl+B does, so its input passes "
            "straight through to whatever came after. The master toggle sets "
            "every branch at once. Wired through Allma Bus Out, a branch bypasses "
            "whatever fills that slot on Allma Bus In, never the bus itself. "
            "Use Allma Muter instead when the branch should "
            "disappear rather than be stepped over."
        )
        return schema


class AllmaGate(AllmaMuter):
    """Kept so workflows saved under the old id still load.

    The node was renamed once its behaviour settled on muting rather than merely
    emitting null. A node_id is what a saved workflow records, so dropping the
    old one would turn every existing instance into a red "missing node" box —
    including in anyone else's workflow, since the pack is published.

    Same implementation, inherited whole; only the identity differs. The frontend
    rewrites the type on load, so a workflow re-saved after the rename stops
    depending on this shim, and it exists for API-format prompts and for anything
    never re-saved.
    """

    @classmethod
    def define_schema(cls):
        schema = super().define_schema()
        schema.node_id = "AllmaGate"
        schema.display_name = "Allma Gate (renamed → Allma Muter)"
        schema.is_deprecated = True
        return schema
