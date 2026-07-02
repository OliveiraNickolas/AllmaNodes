"""AllmaGenerate — the coração. Assembles prompts, images, audio and calls
the Allma backend via the OpenAI-compatible endpoint."""
from ..api.allma_client import (
    audio_dict_to_wav_b64,
    build_user_content,
    chat_completion,
    image_tensor_to_data_url,
)
from ..api.lora_sniffer import format_loras_for_prompt, sniff_loras
from .preset import list_preset_names, load_preset

LOG = "[ComfyUI-Allma/generate]"


def _preset_choices() -> list[str]:
    names = list_preset_names()
    return ["(none)"] + names if names else ["(none)"]


class AllmaGenerate:
    """One prompt + up to three images + optional audio + optional MODEL for
    LoRA sniff. Returns the text the backend produced."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "connectivity": ("ALLMA_CONNECTIVITY",),
                "preset": (_preset_choices(), {}),
                "system_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                }),
                "user_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                }),
                "use_image_metadata": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "When ON and image_N_meta is connected, the METADATA STRING "
                        "from AllmaLoadImage is added to the system prompt so the model can "
                        "reason over the original prompt/model/LoRAs behind the image.",
                    },
                ),
                "thinking": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "OFF: model skips the <think> block and returns the answer directly. "
                        "ON: model reasons first; the reasoning is exposed on the second output "
                        "('thinking') so it doesn't leak into 'response'.",
                    },
                ),
                "use_lora_triggers": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "ON: when MODEL is connected, sniff applied LoRAs (name + trigger "
                        "words from Lora Manager / rgthree-info sidecars) and inject them into the "
                        "system prompt so the LLM weaves them into the final prompt. OFF: ignore "
                        "LoRAs even if MODEL is plugged.",
                    },
                ),
            },
            "optional": {
                "model": ("MODEL",),
                "image_1": ("IMAGE",),
                "image_1_meta": ("STRING", {"forceInput": True}),
                "image_2": ("IMAGE",),
                "image_2_meta": ("STRING", {"forceInput": True}),
                "image_3": ("IMAGE",),
                "image_3_meta": ("STRING", {"forceInput": True}),
                "audio": ("AUDIO",),
                "lora_hints": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Manual override / addition — one LoRA per line, e.g.\n"
                    "sulphur_lora (0.7) — trigger: sulphur_v3, cinematic\n"
                    "When 'use_lora_triggers' is ON, these are appended AFTER the auto-sniffed "
                    "list. When OFF, only these are sent (useful when the sniffer can't find your "
                    "custom LoRA loader).",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("response", "thinking")
    FUNCTION = "generate"
    CATEGORY = "Allma"

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")

    def generate(
        self,
        connectivity,
        preset,
        system_prompt,
        user_prompt,
        use_image_metadata,
        thinking,
        use_lora_triggers,
        model=None,
        image_1=None,
        image_1_meta=None,
        image_2=None,
        image_2_meta=None,
        image_3=None,
        image_3_meta=None,
        audio=None,
        lora_hints="",
    ):
        if not isinstance(connectivity, dict):
            raise RuntimeError("Missing connectivity input — connect AllmaConnectivity.")

        effective_system = system_prompt or ""
        if preset and preset != "(none)":
            data = load_preset(preset)
            if data is None:
                print(f"{LOG} preset '{preset}' not found; using inline system_prompt")
            else:
                preset_sp = (data.get("system_prompt") or "").strip()
                if preset_sp and not effective_system.strip():
                    effective_system = preset_sp

        lora_block = ""
        if use_lora_triggers:
            loras = sniff_loras(model) if model is not None else []
            if loras:
                lora_block = format_loras_for_prompt(loras)
                print(f"{LOG} sniffed {len(loras)} LoRA(s): {[l['name'] for l in loras]}")
            hints_text = (lora_hints or "").strip()
            if hints_text:
                extra = "Additional LoRA hints (user-provided):\n" + "\n".join(
                    "  " + line.strip() for line in hints_text.splitlines() if line.strip()
                )
                lora_block = (lora_block + "\n\n" + extra).strip() if lora_block else extra
                print(f"{LOG} added manual lora_hints ({len(hints_text.splitlines())} lines)")
            if lora_block:
                effective_system = (effective_system + "\n\n" + lora_block).strip()

        if use_image_metadata:
            meta_blocks: list[str] = []
            for idx, meta in enumerate((image_1_meta, image_2_meta, image_3_meta), start=1):
                if meta and isinstance(meta, str) and meta.strip():
                    text = meta.strip()
                    if not text.lower().startswith("image metadata"):
                        text = f"Image {idx} metadata:\n" + text
                    else:
                        text = text.replace("Image metadata", f"Image {idx} metadata", 1)
                    meta_blocks.append(text)
            if meta_blocks:
                effective_system = (
                    effective_system + "\n\n" + "\n\n".join(meta_blocks)
                ).strip()
                print(f"{LOG} attached metadata for {len(meta_blocks)} image(s)")

        image_urls: list[str] = []
        for tag, img in (("image_1", image_1), ("image_2", image_2), ("image_3", image_3)):
            if img is None:
                continue
            try:
                image_urls.append(image_tensor_to_data_url(img))
            except Exception as e:
                print(f"{LOG} failed to encode {tag}: {e}")

        audio_b64, audio_fmt = "", ""
        if audio is not None:
            try:
                audio_b64, audio_fmt = audio_dict_to_wav_b64(audio)
            except Exception as e:
                print(f"{LOG} failed to encode audio: {e}")

        content = build_user_content(user_prompt or "", image_urls, audio_b64, audio_fmt)
        if content == "" and not effective_system:
            raise RuntimeError("Nothing to send — both prompts are empty and no inputs attached.")

        response, thought = chat_completion(
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
        )
        return (response, thought if thinking else "")
