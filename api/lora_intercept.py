"""Monkey-patch ComfyUI's stock LoRA loaders so we can recover the name,
file path and strength of every LoRA that has been applied to a MODEL.

ComfyUI's `load_lora_for_models` stores the raw safetensors metadata dict on
`ModelPatcher.attachments["lora_metadata"]`, but it never records which FILE
the LoRA came from. Trigger words usually live in sidecar JSONs next to the
.safetensors — so without the path we cannot reach them.

This module wraps `LoraLoader.load_lora` and `LoraLoaderModelOnly.load_lora_model_only`
so that, after the original call, we append `{name, path, strength}` to a new
attachment `attachments["_allma_loras"]` on the returned ModelPatcher. Runs
once at import; idempotent.
"""
LOG = "[ComfyUI-Allma/lora_intercept]"

_PATCHED = False


def install() -> bool:
    """Wrap ComfyUI's LoRA loaders. Safe to call more than once."""
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import folder_paths
        import nodes as _nodes
    except Exception as e:
        print(f"{LOG} could not import ComfyUI internals: {e}")
        return False

    def _tag(new_model, lora_name):
        if new_model is None:
            return
        try:
            path = folder_paths.get_full_path_or_raise("loras", lora_name)
        except Exception:
            path = lora_name
        try:
            existing = new_model.attachments.get("_allma_loras", [])
        except Exception:
            existing = []
        entry = {"name": lora_name, "path": path}
        new_model.set_attachments("_allma_loras", list(existing) + [entry])

    LoraLoader = getattr(_nodes, "LoraLoader", None)
    if LoraLoader is not None and not getattr(LoraLoader, "_allma_patched", False):
        _orig = LoraLoader.load_lora

        def _patched_load_lora(self, model, clip, lora_name, strength_model, strength_clip):
            result = _orig(self, model, clip, lora_name, strength_model, strength_clip)
            try:
                new_model = result[0] if isinstance(result, tuple) else None
                if strength_model != 0.0:
                    _tag(new_model, lora_name)
            except Exception as e:
                print(f"{LOG} tag failed on LoraLoader: {e}")
            return result

        LoraLoader.load_lora = _patched_load_lora
        LoraLoader._allma_patched = True
        print(f"{LOG} patched LoraLoader.load_lora")

    LoraLoaderModelOnly = getattr(_nodes, "LoraLoaderModelOnly", None)
    if (
        LoraLoaderModelOnly is not None
        and not getattr(LoraLoaderModelOnly, "_allma_patched_model_only", False)
    ):
        _orig_mo = LoraLoaderModelOnly.load_lora_model_only

        def _patched_model_only(self, model, lora_name, strength_model):
            result = _orig_mo(self, model, lora_name, strength_model)
            try:
                new_model = result[0] if isinstance(result, tuple) else result
                if strength_model != 0.0:
                    _tag(new_model, lora_name)
            except Exception as e:
                print(f"{LOG} tag failed on LoraLoaderModelOnly: {e}")
            return result

        LoraLoaderModelOnly.load_lora_model_only = _patched_model_only
        LoraLoaderModelOnly._allma_patched_model_only = True
        print(f"{LOG} patched LoraLoaderModelOnly.load_lora_model_only")

    _PATCHED = True
    return True
