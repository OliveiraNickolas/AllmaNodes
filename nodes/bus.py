"""A data bus that carries its own slot names.

Comfyroll's bus works, but every wire is an anonymous `Any` and the labels
cannot be changed, so a bus with eight things on it becomes a memory test. This
one keeps the order you plugged things in and lets each slot be named, with the
receiving node mirroring those names back.

Split of responsibility, which is what makes it simple:

  Python  carries VALUES, in slot order, and nothing else.
  The UI   carries NAMES, mirrored from the sending node to the receiving one.

Names never need to reach the backend — the receiving node's outputs are renamed
in the browser, and a rename is not something a generation depends on. Keeping
them out of the payload means a bus still works when driven from an API prompt,
where no frontend exists to have named anything.

Outputs are capped rather than dynamic: Autogrow is `ComfyTypeI` — inputs only —
and ComfyUI has no dynamic-output mechanism, so the receiver declares MAX_SLOTS
of them and the UI hides the ones the bus does not fill.
"""
from comfy_api.latest import io

MAX_SLOTS = 24

BusType = io.Custom("ALLMA_BUS")
SLOTS = [f"slot_{i}" for i in range(1, MAX_SLOTS + 1)]
NAMES = [f"name_{i}" for i in range(1, MAX_SLOTS + 1)]


class AnyType(str):
    """A type that satisfies any comparison ComfyUI makes against it."""

    def __ne__(self, _other):
        return False


ANY = AnyType("*")


class AllmaBusIn(io.ComfyNode):
    """Collect anything into one wire."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaBusIn",
            display_name="Allma Bus In",
            category="Allma/bus",
            search_aliases=["bus", "pipe", "collect", "gather", "wire"],
            description=(
                "Plug anything in; a new slot appears as you fill the last one. "
                "Everything travels on a single wire to Allma Bus Out, which "
                "gives it back in the same order under the same names. Rename a "
                "slot in the Parameters panel — name_1, name_2 … — and the new name "
                "appears on the receiving node straight away."
            ),
            inputs=[
                io.Autogrow.Input(
                    "slots",
                    template=io.Autogrow.TemplateNames(
                        input=io.Custom(ANY).Input("slot", optional=True),
                        names=SLOTS,
                        min=0,
                    ),
                ),
                # Declared here rather than added from JS so they are real
                # widgets: the Parameters panel lists a node's widgets, and a
                # slot label has to be one to be editable there. The browser
                # hides the ones whose slot is empty, so the node only shows a
                # name box for a wire that exists.
                *[io.String.Input(n, default="", optional=True,
                                  tooltip="Label for this slot. Blank takes the "
                                          "name of whatever is plugged in.")
                  for n in NAMES],
            ],
            outputs=[BusType.Output(display_name="bus")],
        )

    @classmethod
    def execute(cls, slots=None, **_names) -> io.NodeOutput:
        # Names are UI only — accepted so ComfyUI can pass them, then ignored.
        # Read in SLOT order rather than connection order, so slot 3 stays the
        # third output no matter which slots were filled or in what sequence.
        group = slots or {}
        return io.NodeOutput([group.get(name) for name in SLOTS])


class AllmaBusOut(io.ComfyNode):
    """Give the bus back, one output per slot."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaBusOut",
            display_name="Allma Bus Out",
            category="Allma/bus",
            search_aliases=["bus", "pipe", "unpack", "split", "wire"],
            description=(
                "Unpacks an Allma Bus. Outputs appear in the order they were "
                "plugged into Allma Bus In and carry the same names. Slots the "
                "bus does not fill are hidden."
            ),
            inputs=[BusType.Input("bus")],
            outputs=[
                io.Custom(ANY).Output(display_name=name) for name in SLOTS
            ],
        )

    @classmethod
    def execute(cls, bus=None) -> io.NodeOutput:
        values = list(bus or [])
        # Pad rather than truncate: every declared output must be returned, and
        # an unfilled slot is None — which any optional consumer already skips.
        values += [None] * (MAX_SLOTS - len(values))
        return io.NodeOutput(*values[:MAX_SLOTS])
