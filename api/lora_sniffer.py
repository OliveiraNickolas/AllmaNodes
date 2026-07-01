"""Extract LoRA info from a ComfyUI MODEL passed into a node.

ComfyUI stores loaded LoRAs on the ModelPatcher. Format varies by version, so
this module is defensive: it tries a few known locations and returns [] when
nothing looks LoRA-ish, rather than crashing the node.

For each LoRA we found, we also try to load its Lora Manager metadata JSON
(`<name>.metadata.json` in the same directory as the .safetensors), which
gives us trigger_words / notes / usage_tips / activation text.
"""
import json
from pathlib import Path

LOG = "[ComfyUI-Allma/loras]"


def _find_lora_paths(model_obj) -> list[str]:
    """Best-effort: return absolute file paths of LoRAs applied to `model_obj`."""
    paths: list[str] = []
    if model_obj is None:
        return paths

    seen: set[str] = set()

    def _record(candidate):
        if not candidate:
            return
        s = str(candidate)
        if ".safetensors" not in s and ".sft" not in s:
            return
        if s in seen:
            return
        seen.add(s)
        paths.append(s)

    for attr in ("patches", "patches_uuid", "model_options"):
        val = getattr(model_obj, attr, None)
        if val is None:
            continue
        if isinstance(val, dict):
            for v in val.values():
                _record(v)
        else:
            _record(val)

    for attr in ("model", "model_patches", "extra"):
        sub = getattr(model_obj, attr, None)
        if sub is None:
            continue
        for a in ("loras", "lora_paths", "applied_loras"):
            v = getattr(sub, a, None)
            if v:
                for x in v if isinstance(v, (list, tuple)) else [v]:
                    _record(x)

    hist = getattr(model_obj, "_allma_lora_history", None)
    if hist:
        for x in hist:
            _record(x)

    return paths


def _read_metadata(safetensors_path: str) -> dict:
    """Read the sidecar Lora Manager `<name>.metadata.json` if it exists."""
    p = Path(safetensors_path)
    meta_path = p.with_suffix("").with_name(p.stem + ".metadata.json")
    if not meta_path.exists():
        meta_path = p.parent / f"{p.stem}.metadata.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{LOG} failed to read {meta_path}: {e}")
        return {}


def _digest(name: str, meta: dict) -> dict:
    """Reduce metadata to the fields the LLM actually needs."""
    trigger = meta.get("trigger_words") or meta.get("activation text") or ""
    if isinstance(trigger, list):
        trigger = ", ".join(t for t in trigger if t)
    notes = (meta.get("notes") or "").strip()
    tips = meta.get("usage_tips") or ""
    if isinstance(tips, str) and tips.strip().startswith("{"):
        try:
            tips = json.loads(tips)
        except Exception:
            pass
    tags = meta.get("tags") or []
    base_model = meta.get("base_model") or ""
    return {
        "name": name,
        "trigger_words": trigger,
        "notes": notes,
        "usage_tips": tips,
        "tags": tags,
        "base_model": base_model,
    }


def sniff_loras(model_obj) -> list[dict]:
    """Return a list of {name, trigger_words, notes, usage_tips, tags, base_model}
    for each LoRA applied to `model_obj`, best-effort."""
    if model_obj is None:
        return []
    paths = _find_lora_paths(model_obj)
    if not paths:
        return []
    out: list[dict] = []
    for path in paths:
        p = Path(path)
        info = _digest(p.stem, _read_metadata(str(p)))
        out.append(info)
    return out


def format_loras_for_prompt(loras: list[dict]) -> str:
    """Turn the sniffed LoRA list into a compact block for the system prompt."""
    if not loras:
        return ""
    lines = ["Active LoRAs in this workflow:"]
    for lora in loras:
        name = lora.get("name") or "unknown"
        parts = [f"- {name}"]
        if lora.get("trigger_words"):
            parts.append(f"trigger: {lora['trigger_words']}")
        if lora.get("notes"):
            parts.append(f"notes: {lora['notes']}")
        lines.append("  ".join(parts))
    return "\n".join(lines)
