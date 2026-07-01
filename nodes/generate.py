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
                    "default": "You are a helpful assistant.",
                }),
                "user_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                }),
            },
            "optional": {
                "model": ("MODEL",),
                "image_1": ("IMAGE",),
                "image_2": ("IMAGE",),
                "image_3": ("IMAGE",),
                "audio": ("AUDIO",),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("response",)
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
        model=None,
        image_1=None,
        image_2=None,
        image_3=None,
        audio=None,
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

        loras = sniff_loras(model) if model is not None else []
        if loras:
            block = format_loras_for_prompt(loras)
            effective_system = (effective_system + "\n\n" + block).strip()
            print(f"{LOG} sniffed {len(loras)} LoRA(s): {[l['name'] for l in loras]}")

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

        response = chat_completion(
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
        )
        return (response,)
