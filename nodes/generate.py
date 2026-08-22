"""AllmaGenerate — the coração. Assembles prompts, images, audio and calls
the Allma backend via the OpenAI-compatible endpoint.

Image and audio slots grow on demand (Autogrow): only the next empty one is
shown, and disconnecting the last one hides it again. Names are 1-based so
`image_1` is <Picture 1> everywhere downstream — MiniMax H3 counts its own
ref_image_0 as Picture 1, and matching that keeps the prompt honest.
"""
import json
import os

import folder_paths
from comfy_api.latest import io

from ..api.allma_client import (
    Cancelled,
    audio_dict_to_wav_b64,
    build_user_content,
    chat_completion,
    image_tensor_to_data_url,
)
from ..api.interrupt import begin_run
from ..api.stream import Relay
from ..api.lora_sniffer import (
    format_loras_for_prompt,
    format_triggers_only,
    sniff_loras,
)
from ..api.image_metadata import (
    format_metadata_for_llm,
    parse_comfyui_workflow,
    read_image_metadata,
)
from .connectivity import AllmaConnectivityType
from .preset import list_preset_names

LOG = "[ComfyUI-Allma/generate]"

MAX_IMAGES = 9
MAX_AUDIOS = 3


def _preset_choices() -> list[str]:
    names = list_preset_names()
    return ["(none)"] + names if names else ["(none)"]


def _duration_block(seconds) -> str:
    """Render the clip length as a system-prompt constraint, or '' if unknown.

    Seconds only, deliberately: frame counts follow model-specific rules (H3
    rounds to a multiple-of-17 offset, other models do not), so deriving frames
    here would print a confident wrong number on every non-H3 graph. The prompt
    writer reasons in seconds anyway.
    """
    if seconds is None:
        return ""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""

    return (
        "TARGET DURATION\n"
        f"The finished clip is {value:.2f} seconds long. This is fixed by the "
        "workflow, not a suggestion.\n"
        "Script the action to fill exactly this span: allocate the beats across the "
        "whole length, describe no more events than fit, and leave no stretch "
        "unaccounted for. A short clip gets fewer, slower beats; a long one gets more "
        "without becoming frantic. Any duration you state uses exactly two decimals. "
        "Ignore any different length assumed elsewhere in this prompt."
    )


def _ordered(group: dict | None, names: list[str]) -> list:
    """Autogrow hands back a dict keyed by slot name; read it in slot order so
    image_1 stays <Picture 1> no matter which slots were filled."""
    group = group or {}
    return [group.get(n) for n in names]


# Keys most likely to carry the picture, tried first so a node with several
# inputs does not send the walk down an unrelated branch.
_IMAGE_KEYS = ("image", "images", "pixels", "source", "input_image", "img")


def _named_file(inputs: dict) -> str | None:
    """A literal filename on this node that resolves to a real image, or None."""
    for key, val in inputs.items():
        if not isinstance(val, str) or not val.strip():
            continue
        if "image" not in key.lower() and key.lower() not in ("filename", "file"):
            continue
        try:
            path = folder_paths.get_annotated_filepath(val)
        except Exception:
            continue
        if os.path.isfile(path):
            return path
    return None


def _trace_image_file(prompt: dict, node_id: str, depth: int = 0, seen=None) -> str | None:
    """Walk upstream from node_id until a node names an image file on disk.

    An IMAGE is a bare tensor: once it leaves the loader nothing about its
    origin survives. But the prompt graph still records which node loaded it,
    so the file — and the metadata baked into it — can be recovered from there.

    Resizes and other image nodes in the path are walked straight through: the
    metadata describes the picture, and a resized copy is still that picture.
    """
    if depth > 8 or not isinstance(prompt, dict):
        return None
    seen = seen if seen is not None else set()
    if node_id in seen:
        return None
    seen.add(node_id)

    node = prompt.get(node_id)
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs") or {}

    found = _named_file(inputs)
    if found:
        return found

    def _follow(val):
        if isinstance(val, list) and len(val) == 2:
            return _trace_image_file(prompt, str(val[0]), depth + 1, seen)
        return None

    # Image-ish inputs first, then anything else, so a MODEL or CLIP link is
    # only explored once the plausible paths are exhausted.
    for key, val in inputs.items():
        if any(h in key.lower() for h in _IMAGE_KEYS):
            hit = _follow(val)
            if hit:
                return hit
    for val in inputs.values():
        hit = _follow(val)
        if hit:
            return hit
    return None


def _upstream_nodes(prompt: dict, node_id: str, seen=None, depth: int = 0) -> set:
    """Every node feeding node_id, transitively — the branch that built it."""
    seen = seen if seen is not None else set()
    if node_id in seen or depth > 40 or len(seen) > 400:
        return seen
    node = prompt.get(node_id)
    if not isinstance(node, dict):
        return seen
    seen.add(node_id)
    for val in (node.get("inputs") or {}).values():
        if isinstance(val, list) and len(val) == 2:
            _upstream_nodes(prompt, str(val[0]), seen, depth + 1)
    return seen


def _metadata_from_graph(prompt: dict, origin: str) -> str:
    """Reconstruct metadata from the live graph, for an image with no file.

    Only the branch feeding this slot is parsed. Handing the parser the whole
    prompt would blend unrelated branches together — in a graph with three
    images and an upscaler, 'Image 1 metadata' would describe all of them.
    """
    ids = _upstream_nodes(prompt, origin)
    if not ids:
        return ""
    branch = {i: prompt[i] for i in ids if i in prompt}
    try:
        meta = parse_comfyui_workflow(json.dumps(branch))
    except Exception as e:
        print(f"{LOG} graph metadata parse failed: {e}")
        return ""
    return format_metadata_for_llm(meta) if meta else ""


def _metadata_for_slot(prompt: dict, my_id: str, slot: str) -> str:
    """Metadata for whatever feeds `slot`, from its file or from the graph."""
    node = (prompt or {}).get(my_id) or {}
    inputs = node.get("inputs") or {}
    # Autogrow serializes namespaced ("images.image_1"); accept the bare form too.
    link = inputs.get(f"images.{slot}", inputs.get(slot))
    if not (isinstance(link, list) and len(link) == 2):
        return ""
    origin = str(link[0])

    # A loaded image: the file it came from is the authority on its history.
    path = _trace_image_file(prompt, origin)
    if path:
        try:
            meta = read_image_metadata(path)
        except Exception as e:
            print(f"{LOG} metadata read failed for {path}: {e}")
            meta = {}
        text = format_metadata_for_llm(meta) if meta else ""
        if text.strip():
            return text

    # Generated in this graph: no file exists, but the nodes that produced it
    # are right here, and they describe the run more faithfully than an
    # embedded workflow would.
    return _metadata_from_graph(prompt, origin)


IMAGE_SLOTS = [f"image_{i}" for i in range(1, MAX_IMAGES + 1)]
AUDIO_SLOTS = [f"audio_{i}" for i in range(1, MAX_AUDIOS + 1)]


class AllmaGenerate(io.ComfyNode):
    """One prompt + up to nine images + up to three audio clips + optional
    MODEL for LoRA sniff. Returns the text the backend produced."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaGenerate",
            display_name="Allma Generate",
            category="Allma/llm",
            # Widget order below MIRRORS GENERATE_ORDER in web/allma_ui.js.
            # ComfyUI writes widgets_values in the order the widgets RENDER, and
            # external readers (comfy-cli's UI->API converter, the MCP) map that
            # array positionally against this schema. When the two disagree every
            # value lands on the wrong field. Reorder here and there together.
            inputs=[
                AllmaConnectivityType.Input("connectivity"),
                io.Boolean.Input(
                    "use_image_metadata", default=True,
                    tooltip="When ON, each connected image is traced back through the "
                    "graph to the file it was loaded from, and that file's embedded "
                    "prompt metadata is added to the system prompt — so the model can "
                    "reason over the original prompt/model/LoRAs behind the picture.",
                ),
                io.Boolean.Input(
                    "thinking", default=False,
                    tooltip="OFF: model skips the <think> block and returns the answer "
                    "directly. ON: model reasons first; the reasoning is exposed on the "
                    "'thinking' output so it doesn't leak into 'output_prompt'. Note the "
                    "reasoning spends the SAME max_tokens budget as the answer.",
                ),
                io.Boolean.Input(
                    "read_lora_metadata", default=True,
                    tooltip="ON: when MODEL is connected, inject each LoRA's full "
                    "sidecar block (trigger_words + notes + usage_tips + author "
                    "description) into the system prompt so the LLM can mine the "
                    "author's prompt-format guidance. Costs more tokens + more "
                    "reasoning effort. OFF: still inject trigger_words (they are "
                    "literal tokens the LoRA needs to activate and are practically "
                    "free), but skip the rest.",
                ),
                io.Combo.Input("preset", options=_preset_choices()),
                io.String.Input("user_prompt", multiline=True, default=""),
                io.String.Input("system_prompt", multiline=True, default=""),
                io.Boolean.Input(
                    "enabled", default=True,
                    tooltip="OFF: skip the LLM entirely and pass user_prompt straight "
                    "through to 'output_prompt'. Nothing is sent to Allma, so the graph "
                    "still runs with the backend down. ON: normal enhancement.",
                ),
                io.Model.Input("model", optional=True),
                io.Autogrow.Input(
                    "images", optional=True,
                    template=io.Autogrow.TemplateNames(
                        input=io.Image.Input(
                            "image",
                            tooltip="Reference image. When it comes from a loader — the "
                            "stock Load Image included — its file's prompt metadata is "
                            "found by walking the graph, no extra wiring needed.",
                        ),
                        names=IMAGE_SLOTS, min=0,
                    ),
                ),
                io.Autogrow.Input(
                    "audios", optional=True,
                    template=io.Autogrow.TemplateNames(
                        input=io.Audio.Input(
                            "audio",
                            tooltip="EXPERIMENTAL — sent as an OpenAI 'input_audio' part. "
                            "Needs a backend model with audio input; text/vision models "
                            "reject or ignore it.",
                        ),
                        names=AUDIO_SLOTS, min=0,
                    ),
                ),
                io.Float.Input(
                    "duration", optional=True, force_input=True,
                    tooltip="Clip length in SECONDS — wire the same FLOAT that drives "
                    "the workflow's length. Stated in the system prompt so the model "
                    "scripts the action to fit the real clip instead of guessing.",
                ),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.unique_id],
            outputs=[
                io.String.Output(display_name="output_prompt"),
                io.String.Output(display_name="thinking"),
                io.String.Output(display_name="assembled_system_prompt"),
                io.String.Output(display_name="status"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        return float("nan")

    @classmethod
    def execute(
        cls,
        connectivity,
        preset,
        system_prompt,
        user_prompt,
        enabled=True,
        use_image_metadata=True,
        thinking=False,
        read_lora_metadata=True,
        model=None,
        images=None,
        audios=None,
        duration=None,
    ) -> io.NodeOutput:
        # 'output_prompt' must always carry a usable prompt, never a diagnostic:
        # whenever the LLM produces nothing we fall back to this and report the
        # reason on 'status' instead.
        passthrough = (user_prompt or "").strip()

        # Checked before anything else: with the enhancer off the node must not
        # touch connectivity at all, so the graph keeps running with Allma down.
        if not enabled:
            print(f"{LOG} disabled — passing user_prompt through ({len(passthrough)} chars)")
            return io.NodeOutput(passthrough, "", system_prompt or "", "")

        if not isinstance(connectivity, dict):
            raise RuntimeError("Missing connectivity input — connect AllmaConnectivity.")

        model_name = connectivity.get("model") or ""
        if not model_name or model_name.startswith("("):
            status = (
                "no valid model selected — Allma was offline when ComfyUI started, "
                "so the model dropdown is empty. Start Allma ('allma serve') and "
                "restart ComfyUI (or refresh the browser) to repopulate the list."
            )
            print(f"{LOG} ⚠ {status} — passing user_prompt through")
            return io.NodeOutput(passthrough, "", system_prompt or "", status)

        # The `preset` widget is JS-only: selecting a preset in the dropdown
        # writes the preset's system_prompt into the system_prompt widget on
        # the client. By the time we get here, `system_prompt` already holds
        # the effective text, so we ignore the `preset` name on the server.
        del preset
        effective_system = system_prompt or ""

        # Stated before the LoRA/metadata blocks: how long the clip actually is
        # governs how much action the model may describe, so it should be the
        # first constraint it reads rather than a footnote after the references.
        duration_block = _duration_block(duration)
        if duration_block:
            effective_system = (effective_system + "\n\n" + duration_block).strip()
            print(f"{LOG} {duration_block.splitlines()[1].strip()}")

        if model is not None:
            loras = sniff_loras(model)
            if loras:
                if read_lora_metadata:
                    block = format_loras_for_prompt(loras)
                    mode = "full metadata"
                else:
                    block = format_triggers_only(loras)
                    mode = "triggers only"
                if block:
                    effective_system = (effective_system + "\n\n" + block).strip()
                print(
                    f"{LOG} sniffed {len(loras)} LoRA(s) [{mode}]: "
                    f"{[l['name'] for l in loras]}"
                )

        # Slot order, not connection order: image_1 must stay <Picture 1> even
        # when only slots 1 and 3 are wired.
        raw_images = _ordered(images, IMAGE_SLOTS)
        prompt_graph = cls.hidden.prompt or {}
        my_id = str(cls.hidden.unique_id or "")

        image_urls: list[str] = []
        meta_blocks: list[str] = []
        traced = 0
        for idx, (slot, img) in enumerate(zip(IMAGE_SLOTS, raw_images), start=1):
            if img is None:
                continue
            try:
                image_urls.append(image_tensor_to_data_url(img))
            except Exception as e:
                print(f"{LOG} failed to encode {slot}: {e}")
            if not use_image_metadata:
                continue
            meta = _metadata_for_slot(prompt_graph, my_id, slot)
            if not meta.strip():
                continue
            traced += 1
            text = meta.strip()
            if not text.lower().startswith("image metadata"):
                text = f"Image {idx} metadata:\n" + text
            else:
                text = text.replace("Image metadata", f"Image {idx} metadata", 1)
            meta_blocks.append(text)
        if use_image_metadata and image_urls and not traced:
            print(f"{LOG} no image traced back to a file — metadata unavailable")

        if meta_blocks:
            effective_system = (
                effective_system + "\n\n" + "\n\n".join(meta_blocks)
            ).strip()
            print(f"{LOG} attached metadata for {len(meta_blocks)} image(s)")

        audio_b64, audio_fmt = "", ""
        for slot, audio in zip(AUDIO_SLOTS, _ordered(audios, AUDIO_SLOTS)):
            if audio is None:
                continue
            try:
                # The OpenAI content schema takes one audio part; extras are
                # reported rather than silently dropped.
                if audio_b64:
                    print(f"{LOG} ignoring {slot}: only one audio part is sent")
                    continue
                audio_b64, audio_fmt = audio_dict_to_wav_b64(audio)
            except Exception as e:
                print(f"{LOG} failed to encode {slot}: {e}")

        content = build_user_content(user_prompt or "", image_urls, audio_b64, audio_fmt)
        if content == "" and not effective_system:
            raise RuntimeError("Nothing to send — both prompts are empty and no inputs attached.")

        run_id = begin_run()
        thought = ""
        status = ""
        # Pushes reasoning to the node's live box as it arrives, so a thinking
        # loop is visible while it happens instead of after the fact.
        relay = Relay(cls.hidden.unique_id)
        relay.start()
        try:
            response, thought, status = chat_completion(
                host=connectivity["host"],
                port=connectivity["port"],
                timeout=connectivity["timeout"],
                model=connectivity["model"],
                system_prompt=effective_system,
                user_content=content,
                temperature=connectivity.get("temperature", 1.0),
                top_p=connectivity.get("top_p", 0.95),
                top_k=connectivity.get("top_k", 20),
                max_tokens=connectivity.get("max_tokens", 2048),
                seed=connectivity.get("seed", -1),
                enable_thinking=bool(thinking),
                relay=relay,
            )
        except Cancelled as c:
            # Stop button: keep whatever the model already produced instead of
            # failing the graph, same as a chat UI does.
            response = (c.content or "").strip()
            thought = (c.thinking or "").strip()
            status = (
                f"interrupted by user after {len(response)} chars."
                if response
                else "interrupted by user before any text was produced."
            )
            print(f"{LOG} 🟥 interrupted by user (run={run_id}) — kept {len(response)} chars")
        except Exception as e:
            # A backend failure must not take the whole graph down: report it on
            # 'status' and let the raw user_prompt carry on downstream.
            response = ""
            status = f"backend call failed: {e}"
            print(f"{LOG} ✖ {status}")

        relay.done(status)

        if not response:
            response = passthrough
            if status:
                status = f"{status} Falling back to the raw user_prompt."
            print(f"{LOG} empty response — passing user_prompt through ({len(response)} chars)")

        return io.NodeOutput(
            response, thought if thinking else "", effective_system, status
        )
