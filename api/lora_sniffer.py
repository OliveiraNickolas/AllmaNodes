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
import re
from pathlib import Path

LOG = "[ComfyUI-Allma/lora_sniffer]"


NAME_HINT_PATTERNS: list[tuple[str, str]] = [
    (
        r"\bvbvr\b",
        "Prefers VBVR structure — the final prompt MUST use blocks "
        "STARTING STATE / ACTION SEQUENCE (Step 1, Step 2, ...) / CAMERA / "
        "CONSISTENCY instead of a flowing paragraph. Present tense throughout.",
    ),
    (
        r"\breasoning\b",
        "Structured-reasoning LoRA — prefer explicit numbered action beats "
        "over a single flowing paragraph.",
    ),
    (
        r"talking[_ -]?head|talkvid|celebvhq",
        'Talking-head LoRA — use the exact format: [full visual description of '
        'the subject and setting], speaking, saying: "the exact words". '
        "Single character only.",
    ),
    (
        r"\bi2v\b|image[_ -]?to[_ -]?video",
        "Image-to-video LoRA — focus the prompt on MOTION and what happens "
        "next; keep the restatement of the reference image brief.",
    ),
]


def _detect_name_hints(name: str) -> list[str]:
    """Look at the LoRA name for structural markers when the sidecar JSONs
    are empty. Zero-friction alternative to manually editing metadata."""
    if not name:
        return []
    found: list[str] = []
    for pattern, hint in NAME_HINT_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            found.append(hint)
    return found


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

        display_name = name or Path(path).stem
        file_name = entry.get("name") or Path(path).name

        auto_hints = _detect_name_hints(display_name) + _detect_name_hints(file_name)
        auto_hints = list(dict.fromkeys(auto_hints))

        out.append({
            "name": display_name,
            "file": file_name,
            "trigger_words": trigger,
            "notes": notes,
            "tags": lm.get("tags") or rg.get("tags") or [],
            "base_model": lm.get("base_model") or "",
            "usage_tips": lm.get("usage_tips") or "",
            "auto_hints": auto_hints,
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
    """Render one LoRA into a multi-line entry that surfaces every signal
    the LLM might act on: trigger words, structural hints (from name),
    notes and usage_tips."""
    name = lora.get("name") or lora.get("file") or "unknown"
    trig = (lora.get("trigger_words") or "").strip()
    header = f"  * {name}"
    if trig:
        header += f' — MUST include verbatim: "{trig}"'
    lines = [header]

    hints = lora.get("auto_hints") or []
    if hints:
        for h in hints:
            lines.append(f"      - structural hint (inferred from name): {h}")

    notes = (lora.get("notes") or "").strip()
    if notes:
        lines.append(f"      - notes: {notes}")

    tips = _tips_to_text(lora.get("usage_tips"))
    if tips:
        lines.append(f"      - usage tips: {tips}")

    return "\n".join(lines)


def format_loras_for_prompt(loras: list[dict]) -> str:
    """Compact but directive block that goes into the system prompt.

    We stop segregating LoRAs into "triggered" vs "style influence only" —
    that soft framing caused the LLM to ignore LoRAs whose metadata JSONs
    were empty even when the file name itself signalled structure (e.g.
    "reasoning ... VBVR"). Now every LoRA lands in one imperative block,
    with any trigger words / name-inferred hints / notes / tips exposed
    side-by-side so nothing gets skipped.
    """
    if not loras:
        return ""
    lines = [
        "ACTIVE LoRAs in this workflow — every entry below is authoritative. "
        "Trigger words MUST appear verbatim in the final prompt. Structural "
        "hints, notes and usage_tips outrank the preset's default structure. "
        "Read the entry's name carefully — many LoRAs encode required "
        "structure or format in the name itself (e.g. 'VBVR', 'reasoning', "
        "'talking-head', 'i2v')."
    ]
    for lora in loras:
        lines.append(_format_lora_entry(lora))
    return "\n".join(lines)
