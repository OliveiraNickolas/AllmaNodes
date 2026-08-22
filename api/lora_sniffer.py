"""Read LoRA info from a MODEL that came through ComfyUI's LoRA loaders.

We rely on the intercept module (`lora_intercept.install()`) — which tags each
applied LoRA on `ModelPatcher.attachments["_allma_loras"]` with its filename,
path and strength — and then combine the available sources to recover the
trigger word and any prompt-format guidance the author left behind:

  1. `lora_hints/<stem>.md`                 — user-written override inside the
     plugin dir; when present it replaces everything below (see
     `_load_curated_hints`).
  2. `<name>.safetensors.rgthree-info.json` — has `trainedWords` (Civitai data
     fetched by rgthree; the most reliable source of trigger words).
  3. `<name>.metadata.json`                 — LoRA Manager sidecar. Has
     `trigger_words`, `notes`, `usage_tips` and the full Civitai model card
     body as HTML in `modelDescription`.

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

LOG = "[AllmaNodes/lora_sniffer]"

# Where we look for user-written hint overrides. The directory lives inside
# the plugin (not next to the LoRA files) so the user's models/loras stays
# untouched. First lookup that matches wins; see _load_curated_hints below.
_HINTS_DIR = Path(__file__).resolve().parent.parent / "lora_hints"
try:
    _HINTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


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


_SIGNAL_PREFIXES = frozenset([
    "prompt", "format", "structur", "template", "describ", "starting",
    "state", "action", "camera", "consistency", "block", "example",
    "follow", "verbatim", "includ", "must", "should", "use", "writ",
    "avoid", "literal", "precise", "trigger", "activat", "step",
    "shot", "scene", "sequence", "beat",
])
_WORD_RE = re.compile(r"[a-z]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"])")


def _signal_score(sentence: str) -> int:
    return sum(
        1
        for w in _WORD_RE.findall(sentence.lower())
        if any(w.startswith(p) for p in _SIGNAL_PREFIXES)
    )


def _extract_format_hints(text: str, max_sentences: int = 4) -> list[str]:
    """Pull sentences from a cleaned description that read like format
    guidance — sentences packed with words like 'prompt', 'format',
    'describe', 'starting state', 'action', 'camera', 'follow', 'must'.

    Civitai descriptions often bury the actual prompting instructions
    in thousands of chars of flavour text (credits, ko-fi links,
    training details, version history). A smart LLM still misses the
    signal when it's diluted like that. We surface the top-scoring
    sentences at the top of the LoRA's entry so the enhancer sees the
    format rules first, then can consult the full description below
    for context."""
    if not text:
        return []
    sentences = _SENTENCE_SPLIT_RE.split(text)
    scored: list[tuple[int, str]] = []
    for raw in sentences:
        s = raw.strip().rstrip(",;:")
        if not (40 <= len(s) <= 300):
            continue
        score = _signal_score(s)
        if score >= 3:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    out: list[str] = []
    seen: set[str] = set()
    for _, s in scored:
        key = s.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_sentences:
            break
    return out


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
        "description": _clean_description(data.get("description") or ""),
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


def _load_curated_hints(safetensors_path: str) -> str:
    """Look for a user-written override file in <AllmaNodes>/lora_hints/.

    We try three lookups in order and return the first that exists:
      1. `<stem>.md`                            (LTX2.3_reasoning_Sulphur.md)
      2. `<parent_dir>_<stem>.md`               (LTX-2.3_LTX2.3_reasoning...md)
      3. `<stem>.txt`                           (fallback for plain-text folks)

    Content is returned stripped, no parsing. Write markdown, plain text,
    a JSON blob, whatever — it's injected verbatim into the LoRA block as
    `human_curated_hints`, which takes precedence over the auto-extracted
    format hints and the raw description."""
    p = Path(safetensors_path)
    stem = p.stem
    parent = p.parent.name
    candidates = [
        _HINTS_DIR / f"{stem}.md",
        _HINTS_DIR / f"{parent}_{stem}.md" if parent else None,
        _HINTS_DIR / f"{stem}.txt",
    ]
    for cand in candidates:
        if cand is None or not cand.exists():
            continue
        try:
            return cand.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"{LOG} failed to read curated hints {cand}: {e}")
            return ""
    return ""


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
            "strength": entry.get("strength"),
            "trigger_words": trigger,
            "notes": notes,
            "tags": lm.get("tags") or rg.get("tags") or [],
            "base_model": lm.get("base_model") or "",
            "usage_tips": lm.get("usage_tips") or "",
            "model_description": lm.get("model_description") or "",
            "human_curated_hints": _load_curated_hints(path),
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
    strength = lora.get("strength")
    header = f"  * name: {name}"
    if isinstance(strength, (int, float)):
        header += f" (strength {strength:g})"
    lines = [header]
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

    curated = (lora.get("human_curated_hints") or "").strip()
    if curated:
        # User curated the guidance by hand — trust it completely and skip
        # the auto-extraction + full description. This keeps the block
        # short and unambiguous when the human has already spoken.
        lines.append("      human_curated_hints (prescriptive; overrides auto-extraction):")
        for cline in curated.splitlines():
            lines.append(f"        {cline}")
        return "\n".join(lines)

    desc = (lora.get("model_description") or "").strip()
    if desc:
        hints = _extract_format_hints(desc)
        if hints:
            lines.append("      format_hints_extracted_from_description:")
            for h in hints:
                lines.append(f"        > {h}")
        lines.append(f"      description: {desc}")

    return "\n".join(lines)


# A trigger is vocabulary, not metadata. Without this, models dutifully obey
# "include verbatim" by announcing the token — "her anatomy is defined by the
# inniepussy trigger word" — which is meaningless to an image or video model
# and pollutes the prompt with tooling jargon.
_TRIGGER_PROSE_RULE = (
    "A trigger is vocabulary, not metadata: it must read as ordinary "
    "description. NEVER write the words 'trigger', 'trigger word', 'token', "
    "'keyword', 'activated by', 'defined by' or 'using the X trigger' in the "
    "output, and never explain that a term came from a LoRA — the downstream "
    "model has no idea what a LoRA is and treats such phrasing as literal "
    "scene content. Write 'her inner labia protrude slightly from a soft "
    "cleft', not 'she displays inniepussy'. If a trigger cannot be placed "
    "naturally without describing something the brief never asked for, leave "
    "it out entirely — a forced trigger costs more than a missing one."
)


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
        "were trained on — spell them exactly as shown in the final prompt, "
        "woven into the description at the point where the thing they name "
        "is on screen. " + _TRIGGER_PROSE_RULE
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
        "read the fields available (trigger_words, notes, usage_tips, "
        "human_curated_hints, format_hints_extracted_from_description, "
        "description) and take them into account when writing the final "
        "prompt. Precedence for how the final prompt must be shaped: "
        "(1) human_curated_hints — hand-written by the workflow author "
        "for this specific LoRA; treat as ground truth. "
        "(2) format_hints_extracted_from_description — auto-extracted "
        "prescriptive sentences from the LoRA's card. "
        "(3) description — raw author write-up, use for context. "
        "All three outrank any default format assumed by the base "
        "preset. Any trigger_words shown must appear verbatim in the "
        "output — they are literal tokens the LoRA was trained on, "
        "not concepts to paraphrase — UNLESS human_curated_hints "
        "explicitly says to omit, avoid or replace them; the curated "
        "hints are the workflow author's final word and win over every "
        "other field, trigger_words included. A LoRA's strength shows "
        "how hard it is applied: at low strength (<0.5) its guidance "
        "is a soft preference; at 1.0+ follow it strictly. " + _TRIGGER_PROSE_RULE
    )
    lines = [header]
    for lora in loras:
        lines.append(_format_lora_entry(lora))
    return "\n".join(lines)
