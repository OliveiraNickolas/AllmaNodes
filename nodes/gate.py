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
MAX_BRANCHES = 10
SLOTS = [f"value_{i}" for i in range(1, MAX_BRANCHES + 1)]
TOGGLES = [f"on_{i}" for i in range(1, MAX_BRANCHES + 1)]


class AllmaMuter(io.ComfyNode):
    """Passa o valor quando ligado; muta o ramo atras e emite None quando desligado."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaMuter",
            display_name="Allma Muter (false = mute)",
            category="Allma/logic",
            search_aliases=["mute", "muter", "gate", "null", "none", "bypass",
                            "disable", "toggle", "skip", "branch"],
            description=(
                "Point it at the branches you want to switch off: wire any "
                "output into a slot and a toggle appears for it. Nothing passes "
                "through — the real wire still runs straight from the source to "
                "whatever consumes it. Switching a branch off mutes the node it "
                "points at, and everything feeding only that node, exactly as "
                "Ctrl+M does. The master toggle sets every branch at once."
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
                        input=io.Custom(ANY).Input("value", optional=True),
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
                *[io.Boolean.Input(t, default=True, optional=True,
                                   tooltip="This branch on its own. A wire here "
                                           "must come from a literal boolean — "
                                           "it is read before the graph runs.")
                  for t in TOGGLES],
            ],
            # No outputs, deliberately. Muting removes the upstream from the
            # prompt entirely, so there is nothing left to pass through — the
            # real wire runs straight from the source to whatever consumes it,
            # and this node only POINTS AT the branch it governs. That also
            # means it never needs to execute: with nothing downstream it is
            # pruned before the graph runs, which is exactly right for a control
            # surface. The work happens in the browser, before submit.
            outputs=[],
        )

    @classmethod
    def check_lazy_status(cls, enabled, values=None, **rest):
        # Never ask for anything. An input here exists to identify a branch, not
        # to carry a value, and requesting one would run the very branch the user
        # is switching off.
        return None

    @classmethod
    def execute(cls, enabled, values=None, **rest) -> io.NodeOutput:
        return io.NodeOutput()


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
        schema.display_name = "Allma Bypasser (false = bypass)"
        schema.category = "Allma/logic"
        schema.search_aliases = ["bypass", "skip", "passthrough", "branch", "toggle"]
        schema.description = (
            "Point it at the stages you want to step over: wire any output into "
            "a slot and a toggle appears for it. Switching a branch off bypasses "
            "the node it points at, exactly as Ctrl+B does, so its input passes "
            "straight through to whatever came after. The master toggle sets "
            "every branch at once. Use Allma Muter instead when the branch should "
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
