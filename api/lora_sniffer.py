"""Read LoRA info from a MODEL that came through ComfyUI's LoRA loaders.

We rely on the intercept module (`lora_intercept.install()`) — which tags each
applied LoRA on `ModelPatcher.attachments["_allma_loras"]` with its filename
and path — and then combine three sidecar formats to recover the trigger word:

  1. `<name>.safetensors.rgthree-info.json` — has `trainedWords` (Civitai data
     fetched by rgthree; the most reliable source).
  2. `<name>.metadata.json`                 — LoRA Manager sidecar. Sometimes
     has `trigger_words`, often null.
  3. `attachments["lora_metadata"]`         — raw metadata dict from inside
     the safetensors file. Rare, but occasionally holds `ss_tag_frequency`
     or `ss_metadata`.

Gracefully returns [] when nothing is found instead of crashing the node.
"""
import json
from pathlib import Path

LOG = "[ComfyUI-Allma/lora_sniffer]"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _rgthree_info(safetensors_path: str) -> dict:
    p = Path(safetensors_path + ".rgthree-info.json")
    if not p.exists():
        return {}
    data = _read_json(p)
    if not data:
        return {}
    trigger = ""
    twords = data.get("trainedWords") or []
    if isinstance(twords, list):
        picks: list[str] = []
        for w in twords:
            if isinstance(w, dict) and w.get("word"):
                picks.append(str(w["word"]))
            elif isinstance(w, str) and w.strip():
                picks.append(w.strip())
        trigger = ", ".join(picks)
    if not trigger:
        trigger = (data.get("triggerWords") or data.get("words") or "") or ""
    return {
        "trigger_words": trigger,
        "civitai_name": data.get("name") or "",
        "description": (data.get("description") or "").strip(),
        "tags": data.get("tags") or [],
    }


def _lora_manager_meta(safetensors_path: str) -> dict:
    p = Path(safetensors_path)
    meta_path = p.with_suffix(".metadata.json")
    if not meta_path.exists():
        meta_path = p.parent / f"{p.stem}.metadata.json"
    if not meta_path.exists():
        return {}
    data = _read_json(meta_path)
    if not data:
        return {}
    trigger = data.get("trigger_words") or data.get("activation text") or ""
    if isinstance(trigger, list):
        trigger = ", ".join(t for t in trigger if t)
    tips = data.get("usage_tips") or ""
    if isinstance(tips, str) and tips.strip().startswith("{"):
        try:
            tips = json.loads(tips)
        except Exception:
            pass
    return {
        "trigger_words": (trigger or "").strip(),
        "notes": (data.get("notes") or "").strip(),
        "usage_tips": tips,
        "tags": data.get("tags") or [],
        "manager_name": (data.get("model_name") or "").strip(),
        "base_model": data.get("base_model") or "",
    }


def _safetensors_internal(meta: dict) -> dict:
    """The safetensors' own metadata dict — occasionally holds tag frequencies."""
    if not isinstance(meta, dict):
        return {}
    interesting = {}
    for k in ("ss_output_name", "ss_tag_frequency", "modelspec.title", "modelspec.description"):
        v = meta.get(k)
        if v:
            interesting[k] = v
    return interesting


def _list_from_attachments(model_obj) -> list[dict]:
    if model_obj is None:
        return []
    att = getattr(model_obj, "attachments", None)
    if not att:
        return []
    entries = att.get("_allma_loras") or []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("path")]


def sniff_loras(model_obj) -> list[dict]:
    """Return {name, trigger_words, notes, tags, base_model, extras} per applied LoRA."""
    entries = _list_from_attachments(model_obj)
    if not entries:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    ss_meta = {}
    try:
        att = getattr(model_obj, "attachments", None)
        if att:
            ss_meta = att.get("lora_metadata") or {}
    except Exception:
        pass
    for entry in entries:
        path = entry["path"]
        if path in seen:
            continue
        seen.add(path)
        rg = _rgthree_info(path)
        lm = _lora_manager_meta(path)
        trigger = rg.get("trigger_words") or lm.get("trigger_words") or ""
        name = (
            rg.get("civitai_name")
            or lm.get("manager_name")
            or Path(path).stem
        )
        notes = lm.get("notes") or rg.get("description") or ""
        out.append({
            "name": name,
            "file": entry.get("name") or Path(path).name,
            "trigger_words": trigger,
            "notes": notes,
            "tags": lm.get("tags") or rg.get("tags") or [],
            "base_model": lm.get("base_model") or "",
            "usage_tips": lm.get("usage_tips") or "",
            "internal_meta": _safetensors_internal(ss_meta),
        })
    return out


def format_loras_for_prompt(loras: list[dict]) -> str:
    """Compact block that goes into the system prompt."""
    if not loras:
        return ""
    lines = ["Active LoRAs in this workflow:"]
    for lora in loras:
        parts = [f"- {lora.get('name') or lora.get('file') or 'unknown'}"]
        if lora.get("trigger_words"):
            parts.append(f'trigger words: "{lora["trigger_words"]}"')
        if lora.get("notes"):
            parts.append(f"notes: {lora['notes']}")
        tips = lora.get("usage_tips")
        if isinstance(tips, dict) and tips:
            tip_txt = "; ".join(f"{k}: {v}" for k, v in tips.items() if v)
            if tip_txt:
                parts.append(f"tips: {tip_txt}")
        elif isinstance(tips, str) and tips.strip():
            parts.append(f"tips: {tips.strip()}")
        lines.append("  " + "  ·  ".join(parts))
    return "\n".join(lines)
