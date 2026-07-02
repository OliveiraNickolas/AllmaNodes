"""Read LoRA info from a MODEL that came through ComfyUI's LoRA loaders.

We rely on the intercept module (`lora_intercept.install()`) — which tags each
applied LoRA on `ModelPatcher.attachments["_allma_loras"]` with its filename
and path — and then combine three sidecar formats to recover the trigger word
and any prompt-format guidance the author left behind:

  1. `<name>.safetensors.rgthree-info.json` — has `trainedWords` (Civitai data
     fetched by rgthree; the most reliable source of trigger words).
  2. `<name>.metadata.json`                 — LoRA Manager sidecar. Has
     `trigger_words`, `notes`, `usage_tips` and the full Civitai model card
     body as HTML in `modelDescription`.
  3. `attachments["lora_metadata"]`         — raw metadata dict from inside
     the safetensors file. Rare, but occasionally holds `ss_tag_frequency`
     or `ss_metadata`.

We intentionally do NOT try to summarise, interpret or hardcode rules per
LoRA family here. The philosophy is: hand the LLM the raw facts we have
(name, trigger words, notes, tips, cleaned description) and let the
preset instruct it to actually READ those facts. That way the sniffer
stays useful for LoRAs nobody has heard of yet.

Gracefully returns [] when nothing is found instead of crashing the node.
"""
import json
import re
from pathlib import Path

LOG = "[ComfyUI-Allma/lora_sniffer]"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
}


def _clean_description(raw: str, max_chars: int = 3500) -> str:
    """Strip HTML, decode common entities, collapse whitespace, cap length."""
    if not raw or not isinstance(raw, str):
        return ""
    text = _HTML_TAG_RE.sub(" ", raw)
    for k, v in _HTML_ENTITIES.items():
        text = text.replace(k, v)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " […truncated]"
    return text


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
    desc_raw = data.get("modelDescription") or data.get("model_description") or ""
    if not desc_raw:
        civ = data.get("civitai")
        if isinstance(civ, dict):
            m = civ.get("model")
            if isinstance(m, dict):
                desc_raw = m.get("description") or ""
            if not desc_raw:
                desc_raw = civ.get("description") or ""
    return {
        "trigger_words": (trigger or "").strip(),
        "notes": (data.get("notes") or "").strip(),
        "usage_tips": tips,
        "tags": data.get("tags") or [],
        "manager_name": (data.get("model_name") or "").strip(),
        "base_model": data.get("base_model") or "",
        "model_description": _clean_description(desc_raw),
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

        display_name = name or Path(path).stem
        file_name = entry.get("name") or Path(path).name

        out.append({
            "name": display_name,
            "file": file_name,
            "trigger_words": trigger,
            "notes": notes,
            "tags": lm.get("tags") or rg.get("tags") or [],
            "base_model": lm.get("base_model") or "",
            "usage_tips": lm.get("usage_tips") or "",
            "model_description": lm.get("model_description") or "",
            "internal_meta": _safetensors_internal(ss_meta),
        })
    return out


def _tips_to_text(tips) -> str:
    if isinstance(tips, dict) and tips:
        return "; ".join(f"{k}: {v}" for k, v in tips.items() if v)
    if isinstance(tips, str):
        return tips.strip()
    return ""


def _format_lora_entry(lora: dict) -> str:
    """Render one LoRA as a labelled bundle of facts. No imperatives here —
    the preset is where we tell the LLM how to react to this block."""
    name = lora.get("name") or lora.get("file") or "unknown"
    file_name = lora.get("file") or ""
    lines = [f"  * name: {name}"]
    if file_name and file_name != name:
        lines.append(f"      file: {file_name}")

    trig = (lora.get("trigger_words") or "").strip()
    if trig:
        lines.append(f"      trigger_words: {trig}")

    notes = (lora.get("notes") or "").strip()
    if notes:
        lines.append(f"      notes: {notes}")

    tips = _tips_to_text(lora.get("usage_tips"))
    if tips:
        lines.append(f"      usage_tips: {tips}")

    desc = (lora.get("model_description") or "").strip()
    if desc:
        lines.append(f"      description: {desc}")

    return "\n".join(lines)


def format_triggers_only(loras: list[dict]) -> str:
    """Minimal block that only surfaces trigger words.

    Used when the user asks us NOT to feed the full metadata to the LLM
    (to save tokens / effort) — but trigger words are still cheap and
    literally required for the LoRA to activate, so we always inject
    them when they exist.

    Returns "" if none of the sniffed LoRAs have trigger words."""
    if not loras:
        return ""
    lines: list[str] = []
    for lora in loras:
        trig = (lora.get("trigger_words") or "").strip()
        if not trig:
            continue
        name = lora.get("name") or lora.get("file") or "unknown"
        lines.append(f"  * {name} — trigger_words: {trig}")
    if not lines:
        return ""
    header = (
        "Active LoRA trigger words. These are literal tokens the LoRAs "
        "were trained on — include them verbatim in the final prompt."
    )
    return header + "\n" + "\n".join(lines)


def format_loras_for_prompt(loras: list[dict]) -> str:
    """Emit a plain, factual LoRA block for the system prompt.

    The header is a short instruction telling the enhancer to actually read
    the fields — trigger words, notes, tips and description — and factor
    them into the final prompt. We don't hardcode format rules per LoRA
    family here; if the LoRA needs a specific prompt structure, the author
    almost always spells it out in the description or usage_tips. Trust
    the LLM to extract that."""
    if not loras:
        return ""
    header = (
        "The following LoRAs are active in this workflow. For each one, "
        "read the fields (trigger_words, notes, usage_tips, description) "
        "and take them into account when writing the final prompt. If a "
        "LoRA's description or usage_tips specifies a required prompt "
        "format, structure, or example, follow it. Any trigger_words "
        "shown must appear verbatim in the output — they are literal tokens "
        "the LoRA was trained on, not concepts to paraphrase."
    )
    lines = [header]
    for lora in loras:
        lines.append(_format_lora_entry(lora))
    return "\n".join(lines)
