"""Make `lazy` work on dynamic (Autogrow) inputs.

ComfyUI reads an input's flags in two places, and only one of them knows about
dynamic inputs:

- ``execution.get_input_data`` expands the schema against the node's ACTUAL
  inputs first (``_io.get_finalized_class_inputs``), so a grown slot named
  ``values.value_3`` is found with all its flags.
- ``TopologicalSort.get_input_info`` — the one the SCHEDULER uses to decide
  what has to run — calls ``class_def.INPUT_TYPES()`` raw. In a V3 node that
  returns the template, not the grown slots: there is no ``values.value_3`` in
  it, so the lookup returns nothing, ``lazy`` reads as false, and every wired
  branch is scheduled as a hard dependency.

The result is that ``check_lazy_status`` is still called and still answers, but
by then everything it might have skipped has already run. Measured on three
4-second branches: 12.3s with one branch switched off, where laziness would
have given 4s.

So this wraps that one method to expand the schema the same way the executor
does, for V3 nodes only. Static inputs are unaffected — they were already found
by name — and non-V3 nodes never enter the branch.

Installed at import; idempotent; a failure here is never fatal, it only means
the lazy slots stay eager, which is how ComfyUI behaves without us.
"""
LOG = "[AllmaNodes/lazy_dynamic]"

_PATCHED = False


def install() -> bool:
    """Teach the scheduler to see flags on grown slots. Safe to call twice."""
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import nodes as _nodes
        from comfy_api.internal import _ComfyNodeInternal
        from comfy_api.latest import _io
        from comfy_execution.graph import TopologicalSort, get_input_info
    except Exception as e:
        print(f"{LOG} ComfyUI internals not available: {e}")
        return False

    if getattr(TopologicalSort, "_allma_lazy_dynamic", False):
        _PATCHED = True
        return True

    def _expanded(self, unique_id, class_def):
        """The node's schema grown against its own inputs, cached per run.

        The scheduler asks once per wired input, and expanding is not free, so
        the answer is kept on the sort object — which lives exactly as long as
        one execution, so nothing can go stale across runs.
        """
        cache = getattr(self, "_allma_inputs_cache", None)
        if cache is None:
            cache = self._allma_inputs_cache = {}
        if unique_id in cache:
            return cache[unique_id]
        valid = class_def.INPUT_TYPES()
        try:
            live = self.dynprompt.get_node(unique_id)["inputs"]
            valid, _hidden, _v3 = _io.get_finalized_class_inputs(valid, live)
        except Exception as e:
            print(f"{LOG} could not expand {unique_id}: {e}")
        cache[unique_id] = valid
        return valid

    def get_input_info_patched(self, unique_id, input_name):
        class_type = self.dynprompt.get_node(unique_id)["class_type"]
        class_def = _nodes.NODE_CLASS_MAPPINGS[class_type]
        valid_inputs = None
        try:
            if issubclass(class_def, _ComfyNodeInternal):
                valid_inputs = _expanded(self, unique_id, class_def)
        except Exception as e:
            print(f"{LOG} falling back to the stock lookup for {class_type}: {e}")
        return get_input_info(class_def, input_name, valid_inputs)

    TopologicalSort.get_input_info = get_input_info_patched
    TopologicalSort._allma_lazy_dynamic = True
    _PATCHED = True
    return True
