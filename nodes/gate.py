"""Gate / Null — desligar um ramo do grafo sem mexer na topologia.

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

Cuidado
-------
A saída é ``None`` num pino tipado. Só ligue em entradas que aceitem ausência —
tipicamente as declaradas ``optional``. Um node que assume valor presente vai
levantar ``AttributeError``/``TypeError`` ao receber nulo, e o erro vai apontar
para ele, não para este gate.
"""
from comfy_api.latest import io


class AllmaGate(io.ComfyNode):
    """Deixa o valor passar quando ligado; emite None quando desligado."""

    @classmethod
    def define_schema(cls):
        template = io.MatchType.Template("allma_gate")
        return io.Schema(
            node_id="AllmaGate",
            display_name="Allma Gate (null when off)",
            category="Allma/logic",
            search_aliases=["null", "none", "gate", "disable", "toggle", "skip"],
            description=(
                "Passes its input through when 'enabled' is ON, and emits null "
                "when OFF — so a downstream node with an optional input simply "
                "sees nothing there. Works with any type. While OFF the input is "
                "never evaluated, so the whole branch above it is skipped."
            ),
            inputs=[
                io.Boolean.Input(
                    "enabled", default=True,
                    tooltip="ON: pass the value through. OFF: emit null and skip "
                    "everything upstream of this node.",
                ),
                io.MatchType.Input("value", template=template, lazy=True),
            ],
            outputs=[io.MatchType.Output(template=template, display_name="value")],
        )

    @classmethod
    def check_lazy_status(cls, enabled, value=None):
        # Só pede a avaliação do ramo quando ele vai mesmo ser usado. Devolver
        # nada aqui com o gate desligado é o que impede o upstream de rodar;
        # `value` chega None em execute() por nunca ter sido avaliado.
        if enabled and value is None:
            return ["value"]

    @classmethod
    def execute(cls, enabled, value=None) -> io.NodeOutput:
        return io.NodeOutput(value if enabled else None)
