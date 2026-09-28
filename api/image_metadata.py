"""Read prompt-relevant metadata from PNG/JPEG images.

Supports:
  - ComfyUI PNGs: `prompt` and `workflow` tEXt/zTXt chunks (graph JSON).
    We walk the graph to pull the positive/negative prompts, checkpoint,
    LoRAs, and sampler settings.
  - A1111 PNGs: `parameters` tEXt chunk (plain-text format).
  - JPEG EXIF: best-effort camera info.

The final `format_metadata_for_llm()` returns a compact, human-friendly block
that we drop into the system prompt so the LLM can reason over it.
"""
import json
import re
import struct
import zlib
from pathlib import Path

LOG = "[AllmaNodes/meta]"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def _iter_png_chunks(data: bytes):
    """Yield (chunk_type_str, chunk_data_bytes) for each PNG chunk."""
    if not data.startswith(PNG_MAGIC):
        return
    off = len(PNG_MAGIC)
    n = len(data)
    while off + 8 <= n:
        length = struct.unpack(">I", data[off : off + 4])[0]
        ctype = data[off + 4 : off + 8].decode("ascii", errors="replace")
        start = off + 8
        end = start + length
        if end + 4 > n:
            return
        yield ctype, data[start:end]
        off = end + 4
        if ctype == "IEND":
            return


def read_png_text_chunks(path: str) -> dict:
    """Return {keyword: text} from all tEXt / zTXt / iTXt chunks."""
    out: dict = {}
    try:
        blob = Path(path).read_bytes()
    except Exception as e:
        print(f"{LOG} read failed: {e}")
        return out
    for ctype, payload in _iter_png_chunks(blob):
        try:
            if ctype == "tEXt":
                key, _, text = payload.partition(b"\x00")
                out[key.decode("latin-1", errors="replace")] = text.decode(
                    "utf-8", errors="replace"
                )
            elif ctype == "zTXt":
                key, _, rest = payload.partition(b"\x00")
                if not rest:
                    continue
                comp_method, comp_data = rest[0], rest[1:]
                if comp_method == 0:
                    text = zlib.decompress(comp_data).decode("utf-8", errors="replace")
                    out[key.decode("latin-1", errors="replace")] = text
            elif ctype == "iTXt":
                pieces = payload.split(b"\x00", 4)
                if len(pieces) != 5:
                    continue
                key, comp_flag, _comp_method, _lang, rest = pieces
                if comp_flag == b"\x00":
                    text = rest.decode("utf-8", errors="replace")
                else:
                    text = zlib.decompress(rest).decode("utf-8", errors="replace")
                out[key.decode("latin-1", errors="replace")] = text
        except Exception as e:
            print(f"{LOG} chunk {ctype} decode failed: {e}")
    return out


def parse_comfyui_workflow(graph_json: str) -> dict:
    """Walk a ComfyUI prompt/workflow JSON and extract the essentials.

    Returns keys: source, model, positive_prompt, negative_prompt, loras,
    sampler_summary, raw_class_types (for debugging).
    """
    try:
        data = json.loads(graph_json)
    except Exception:
        return {}

    nodes: dict = {}
    if isinstance(data, dict) and "nodes" in data and isinstance(data["nodes"], list):
        for n in data["nodes"]:
            if not isinstance(n, dict):
                continue
            key = str(n.get("id", ""))
            widgets = n.get("widgets_values") or []
            nodes[key] = {
                "class_type": n.get("type", ""),
                "inputs": {"__widgets__": widgets},
            }
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and "class_type" in v:
                nodes[str(k)] = v

    if not nodes:
        return {}

    model_names: list[str] = []
    positive: list[str] = []
    negative: list[str] = []
    loras: list[dict] = []
    sampler_bits: list[str] = []
    class_types: list[str] = []
    seeds: list[int] = []
    steps_list: list[int] = []

    allma_user_prompt = None
    allma_enhancer = None

    def _widgets(entry):
        return entry.get("inputs", {}).get("__widgets__") or []

    # Class names vary wildly in punctuation between packs — "Power Lora Loader
    # (rgthree)" vs "LoraLoaderModelOnly". Match on letters only.
    def _norm(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (name or "").lower())

    # Prompt text is usually NOT a literal on the encoder: it arrives through a
    # link, e.g. CLIPTextEncode.text = ["143", 0] -> Any Switch -> Primitive.
    # Walk the graph until an actual string turns up.
    _TEXT_FIELDS = ("value", "text", "string", "prompt", "str")

    def _resolve_text(value, _seen=None, _depth=0):
        if isinstance(value, str):
            return value
        if _depth > 12 or not isinstance(value, (list, tuple)) or not value:
            return ""
        node_id = str(value[0])
        _seen = _seen or set()
        if node_id in _seen:
            return ""
        _seen.add(node_id)
        target = nodes.get(node_id)
        if not isinstance(target, dict):
            return ""
        t_in = target.get("inputs", {}) or {}
        # a literal on a known text field wins
        for f in _TEXT_FIELDS:
            v = t_in.get(f)
            if isinstance(v, str) and v.strip():
                return v
        # UI-format nodes keep their value in widgets_values
        for w in _widgets(target):
            if isinstance(w, str) and w.strip():
                return w
        # otherwise keep following links (switches, reroutes, concat nodes…)
        for v in t_in.values():
            if isinstance(v, (list, tuple)) and v:
                got = _resolve_text(v, _seen, _depth + 1)
                if got.strip():
                    return got
        return ""

    def _resolve_bool(value, _seen=None, _depth=0):
        if isinstance(value, bool):
            return value
        if _depth > 6 or not isinstance(value, (list, tuple)) or not value:
            return None
        node_id = str(value[0])
        _seen = _seen or set()
        if node_id in _seen:
            return None
        _seen.add(node_id)
        target = nodes.get(node_id)
        if not isinstance(target, dict):
            return None
        t_in = target.get("inputs", {}) or {}
        v = t_in.get("value")
        if isinstance(v, bool):
            return v
        for w in _widgets(target):
            if isinstance(w, bool):
                return w
        return None

    for key, entry in nodes.items():
        ct = entry.get("class_type", "") or ""
        class_types.append(ct)
        inp = entry.get("inputs", {}) or {}
        low = _norm(ct)

        if ("checkpointloader" in low or "unetloader" in low
                or "diffusionmodelloader" in low or "diffusionloader" in low):
            name = inp.get("ckpt_name") or inp.get("unet_name") or inp.get("model_name")
            if not name:
                w = _widgets(entry)
                if w and isinstance(w[0], str):
                    name = w[0]
            if name:
                model_names.append(str(name))

        elif "allmagenerate" in low:
            up_val = inp.get("user_prompt")
            if up_val:
                res_up = _resolve_text(up_val)
                if res_up.strip():
                    allma_user_prompt = res_up.strip()
            en_val = inp.get("enabled")
            if en_val is not None:
                res_en = _resolve_bool(en_val)
                if res_en is not None:
                    allma_enhancer = res_en
                elif isinstance(en_val, bool):
                    allma_enhancer = en_val

        elif "cliptextencode" in low or "textencode" in low or "conditioning" in low and "text" in low:
            text = _resolve_text(inp.get("text") or inp.get("prompt") or "")
            if not text:
                w = _widgets(entry)
                if w and isinstance(w[0], str):
                    text = w[0]
            if isinstance(text, str) and text.strip():
                title = (entry.get("title") or entry.get("_meta", {}).get("title") or "").lower()
                if "neg" in title or "negative" in low:
                    negative.append(text.strip())
                else:
                    positive.append(text.strip())

        elif "loraloader" in low or "powerlora" in low or "lorastack" in low:
            # rgthree's Power Lora Loader keeps one dict per slot:
            # lora_N = {"on": bool, "lora": path, "strength": float}
            slot_hits = 0
            for k, v in inp.items():
                if not (isinstance(v, dict) and "lora" in v):
                    continue
                slot_hits += 1
                if v.get("on") is False:
                    continue  # disabled slot never touched the image
                loras.append({"name": str(v.get("lora")),
                              "strength": v.get("strength")})
            if slot_hits:
                continue

            name = inp.get("lora_name") or inp.get("name")
            strength = inp.get("strength_model") or inp.get("strength") or inp.get("lora_strength")
            if not name:
                w = _widgets(entry)
                if w:
                    if isinstance(w[0], str):
                        name = w[0]
                    if len(w) > 1 and isinstance(w[1], (int, float)):
                        strength = w[1]
            if name:
                try:
                    strength = round(float(strength), 3) if strength is not None else None
                except Exception:
                    strength = None
                loras.append({"name": str(name), "strength": strength})

        elif "samplercustom" in low:
            noise_ref = inp.get("noise")
            if isinstance(noise_ref, (list, tuple)) and noise_ref:
                n_node = nodes.get(str(noise_ref[0]))
                if n_node:
                    n_in = n_node.get("inputs", {})
                    s = n_in.get("noise_seed") or n_in.get("seed")
                    if s is None:
                        w = _widgets(n_node)
                        if w and isinstance(w[0], (int, float)):
                            s = w[0]
                    if s is not None and isinstance(s, (int, float)):
                        seeds.append(int(s))
            sigmas_ref = inp.get("sigmas")
            if isinstance(sigmas_ref, (list, tuple)) and sigmas_ref:
                s_node = nodes.get(str(sigmas_ref[0]))
                if s_node:
                    s_in = s_node.get("inputs", {})
                    st = s_in.get("steps")
                    if st is None:
                        w = _widgets(s_node)
                        if len(w) >= 2 and isinstance(w[1], (int, float)):
                            st = w[1]
                    if st is not None and isinstance(st, (int, float)):
                        steps_list.append(int(st))
                    sch = s_in.get("scheduler")
                    if sch and isinstance(sch, str):
                        sampler_bits.append(sch)
            samp_ref = inp.get("sampler")
            if isinstance(samp_ref, (list, tuple)) and samp_ref:
                sm_node = nodes.get(str(samp_ref[0]))
                if sm_node:
                    sm_in = sm_node.get("inputs", {})
                    sn = sm_in.get("sampler_name") or sm_in.get("sampler")
                    if sn and isinstance(sn, str):
                        sampler_bits.insert(0, sn)

        elif "ksampler" in low:
            steps = inp.get("steps")
            sampler = inp.get("sampler_name") or inp.get("sampler")
            scheduler = inp.get("scheduler")
            cfg = inp.get("cfg")
            seed = inp.get("seed") or inp.get("noise_seed")
            if not any([steps, sampler, scheduler, cfg, seed]):
                w = _widgets(entry)
                if w:
                    if len(w) >= 1 and isinstance(w[0], (int, float)):
                        seed = seed or w[0]
                    if len(w) >= 3 and isinstance(w[2], (int, float)):
                        steps = steps or w[2]
                    if len(w) >= 4 and isinstance(w[3], (int, float)):
                        cfg = cfg or w[3]
                    if len(w) >= 5 and isinstance(w[4], str):
                        sampler = sampler or w[4]
                    if len(w) >= 6 and isinstance(w[5], str):
                        scheduler = scheduler or w[5]
            if seed is not None and isinstance(seed, (int, float)):
                seeds.append(int(seed))
            if steps is not None and isinstance(steps, (int, float)):
                steps_list.append(int(steps))
            bits = []
            if sampler:
                bits.append(str(sampler))
            if scheduler:
                bits.append(str(scheduler))
            if cfg is not None:
                try:
                    bits.append(f"cfg {round(float(cfg), 2)}")
                except Exception:
                    pass
            if bits:
                sampler_bits.append(" / ".join(bits))

        elif "randomnoise" in low:
            s = inp.get("noise_seed") or inp.get("seed")
            if s is None:
                w = _widgets(entry)
                if w and isinstance(w[0], (int, float)):
                    s = w[0]
            if s is not None and isinstance(s, (int, float)):
                seeds.append(int(s))

        elif "basicscheduler" in low or ("scheduler" in low and "steps" in inp):
            st = inp.get("steps")
            if st is None:
                w = _widgets(entry)
                if len(w) >= 2 and isinstance(w[1], (int, float)):
                    st = w[1]
            if st is not None and isinstance(st, (int, float)):
                steps_list.append(int(st))
            sch = inp.get("scheduler")
            if sch and isinstance(sch, str):
                sampler_bits.append(sch)

        elif "seedvr" in low:
            s = inp.get("seed")
            if s is not None and isinstance(s, (int, float)):
                seeds.append(int(s))

    def _uniq(seq):
        seen: set = set()
        out: list = []
        for x in seq:
            k = json.dumps(x, sort_keys=True) if isinstance(x, dict) else x
            if k not in seen:
                seen.add(k)
                out.append(x)
        return out

    chosen_seed = seeds[0] if seeds else None
    chosen_steps = steps_list[0] if steps_list else None

    summary_parts = []
    if sampler_bits:
        summary_parts.append(" / ".join(_uniq(sampler_bits)))
    if chosen_steps is not None:
        summary_parts.append(f"{chosen_steps} steps")
    if chosen_seed is not None:
        summary_parts.append(f"seed {chosen_seed}")

    user_p = allma_user_prompt or ("\n\n".join(_uniq(positive)) if positive else "")

    return {
        "source": "ComfyUI",
        "model": ", ".join(_uniq(model_names)),
        "user_prompt": user_p,
        "positive_prompt": "\n\n".join(_uniq(positive)),
        "negative_prompt": "\n\n".join(_uniq(negative)),
        "enhancer_used": allma_enhancer,
        "loras": _uniq(loras),
        "seed": chosen_seed,
        "steps": chosen_steps,
        "sampler_summary": " | ".join(summary_parts) if summary_parts else " | ".join(_uniq(sampler_bits)),
        "raw_class_types": sorted(set(class_types)),
    }


A1111_SAMPLER_LINE = re.compile(r"^([A-Z][A-Za-z0-9_ ]*):\s*(.+?)(?:,|$)")


def parse_a1111_parameters(text: str) -> dict:
    """A1111 `parameters` tEXt chunk:

    line 1..N: positive prompt (until "Negative prompt:" or a "K: V, K: V" line)
    then "Negative prompt: ..."
    then "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1234, ..."
    """
    if not text:
        return {}
    lines = text.splitlines()
    positive: list[str] = []
    negative_lines: list[str] = []
    settings_raw = ""
    mode = "pos"
    for line in lines:
        stripped = line.strip()
        if mode == "pos":
            if stripped.lower().startswith("negative prompt:"):
                negative_lines.append(stripped.split(":", 1)[1].strip())
                mode = "neg"
                continue
            if re.match(r"^[A-Z][A-Za-z0-9 ]*:\s*", stripped) and "," in stripped:
                settings_raw = stripped
                mode = "settings"
                continue
            positive.append(line)
        elif mode == "neg":
            if re.match(r"^[A-Z][A-Za-z0-9 ]*:\s*", stripped) and "," in stripped:
                settings_raw = stripped
                mode = "settings"
                continue
            negative_lines.append(line)
        else:
            settings_raw += "\n" + line
    settings: dict = {}
    for m in re.finditer(r"([A-Z][A-Za-z0-9 ]*):\s*([^,]+?)(?:,|$)", settings_raw):
        settings[m.group(1).strip()] = m.group(2).strip()
    parts: list[str] = []
    for key in ("Sampler", "Schedule type", "Steps", "CFG scale", "Seed", "Size", "Model"):
        if key in settings:
            parts.append(f"{key.lower()}: {settings[key]}")
    return {
        "source": "A1111",
        "model": settings.get("Model", ""),
        "positive_prompt": "\n".join(positive).strip(),
        "negative_prompt": "\n".join(negative_lines).strip(),
        "loras": [],
        "sampler_summary": ", ".join(parts),
    }


def _read_jpeg_exif(path: str) -> dict:
    try:
        from PIL import Image, ExifTags
    except Exception:
        return {}
    try:
        with Image.open(path) as img:
            exif = img.getexif() or {}
    except Exception:
        return {}
    if not exif:
        return {}
    tag_map = {v: k for k, v in ExifTags.TAGS.items()}
    def _tag(name):
        tid = tag_map.get(name)
        if tid is None:
            return ""
        v = exif.get(tid, "")
        return str(v).strip() if v else ""
    make = _tag("Make")
    model = _tag("Model")
    dt = _tag("DateTimeOriginal") or _tag("DateTime")
    software = _tag("Software")
    bits = []
    if make or model:
        bits.append(f"camera: {(make + ' ' + model).strip()}")
    if dt:
        bits.append(f"date: {dt}")
    if software:
        bits.append(f"software: {software}")
    if not bits:
        return {}
    return {
        "source": "JPEG EXIF",
        "sampler_summary": ", ".join(bits),
    }


def read_image_metadata(path: str) -> dict:
    """Detect format, extract everything we can. Returns {} on total miss."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        head = p.open("rb").read(16)
    except Exception:
        return {}
    if head.startswith(PNG_MAGIC):
        chunks = read_png_text_chunks(str(p))
        best: dict = {}
        for k in ("prompt", "workflow"):
            if k in chunks:
                parsed = parse_comfyui_workflow(chunks[k])
                if parsed and (parsed.get("positive_prompt") or parsed.get("model") or parsed.get("loras")):
                    best = parsed
                    break
        if not best and "parameters" in chunks:
            best = parse_a1111_parameters(chunks["parameters"])
        if not best and chunks:
            joined = "\n".join(f"{k}: {v[:400]}" for k, v in chunks.items() if v)
            best = {"source": "PNG (unrecognized)", "raw": joined}

        # The prompt Allma Generate stamped on its way past, which is NOT in the
        # two blobs above: those are serialised at submit time, before the run,
        # so the graph's copy of the prompt is whatever the widget held then —
        # stale, or empty. This chunk is the text the model was actually given.
        # Read last and unconditionally, so it survives every branch above,
        # including the "unrecognized PNG" fallback.
        raw_user = chunks.get("allma_user_prompt")
        if raw_user:
            try:
                raw_user = json.loads(raw_user)
            except Exception:
                pass
            if isinstance(raw_user, str) and raw_user.strip():
                best = dict(best)
                best["user_prompt"] = raw_user.strip()

        raw_enhancer = chunks.get("allma_enhancer")
        if raw_enhancer is not None:
            try:
                raw_enhancer = json.loads(raw_enhancer)
            except Exception:
                pass
            best = dict(best)
            if isinstance(raw_enhancer, str):
                best["enhancer_used"] = raw_enhancer.lower() in ("true", "1", "yes")
            elif isinstance(raw_enhancer, bool):
                best["enhancer_used"] = raw_enhancer

        raw = chunks.get("allma_prompt")
        if raw:
            try:
                raw = json.loads(raw)   # stored through json.dumps by SaveImage
            except Exception:
                pass
            if isinstance(raw, str) and raw.strip():
                best = dict(best)
                best.setdefault("source", "ComfyUI")
                raw_stripped = raw.strip()
                best["allma_prompt"] = raw_stripped
                user_p = (best.get("user_prompt") or "").strip()
                if best.get("enhancer_used") is True:
                    best["final_prompt"] = raw_stripped
                elif best.get("enhancer_used") is None:
                    if not user_p or user_p != raw_stripped:
                        best["enhancer_used"] = True
                        best["final_prompt"] = raw_stripped
                    else:
                        best["enhancer_used"] = False
                elif best.get("enhancer_used") is False and not user_p:
                    best["user_prompt"] = raw_stripped
        return best
    if head.startswith(JPEG_MAGIC):
        return _read_jpeg_exif(str(p))
    return {}


def format_metadata_for_llm(meta: dict, label: str = "Image metadata") -> str:
    """Compact, LLM-friendly block. Returns "" when nothing useful."""
    if not meta:
        return ""
    lines = [f"{label}:"]
    if meta.get("source"):
        lines.append(f"  source: {meta['source']}")
    if meta.get("model"):
        lines.append(f"  model: {meta['model']}")

    # 1. User prompt
    user_prompt = (meta.get("user_prompt") or meta.get("positive_prompt") or "").strip()
    if user_prompt:
        lines.append(f"  user prompt: \"{user_prompt}\"")

    if meta.get("negative_prompt"):
        text = meta["negative_prompt"].strip()
        lines.append(f"  negative prompt: \"{text}\"")

    # 2. Caso tenha sido usado enhancer, o prompt final inteiro
    enhancer_used = meta.get("enhancer_used")
    final_prompt = (meta.get("final_prompt") or meta.get("allma_prompt") or "").strip()
    if enhancer_used is True and final_prompt and final_prompt != user_prompt:
        lines.append("  enhancer: used")
        lines.append(f"  final prompt: \"{final_prompt}\"")
    elif enhancer_used is False:
        lines.append("  enhancer: disabled")
    elif final_prompt and final_prompt != user_prompt:
        lines.append(f"  final prompt: \"{final_prompt}\"")

    # 3. Seed do sampling
    if meta.get("seed") is not None:
        lines.append(f"  seed: {meta['seed']}")

    # 4. LoRAs com seus strengths
    loras = meta.get("loras") or []
    if loras:
        bits = []
        for l in loras:
            if isinstance(l, dict):
                name = l.get("name", "?")
                s = l.get("strength")
                bits.append(f"{name}" + (f" ({s})" if s is not None else ""))
            else:
                bits.append(str(l))
        lines.append(f"  LoRAs: {', '.join(bits)}")

    # 5. Steps
    if meta.get("steps") is not None:
        lines.append(f"  steps: {meta['steps']}")

    if meta.get("sampler_summary"):
        lines.append(f"  sampler: {meta['sampler_summary']}")
    if meta.get("raw"):
        lines.append(f"  raw:\n    {meta['raw']}")
    return "\n".join(lines)
