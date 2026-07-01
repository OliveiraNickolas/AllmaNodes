# ComfyUI-Allma

Custom nodes for wiring [ComfyUI](https://github.com/comfyanonymous/ComfyUI) to the [Allma](https://github.com/nickolasarthur/allma) LLM backend (or any OpenAI-compatible endpoint).

Two nodes, one purpose: send a prompt (plus up to three images and audio) to a
local LLM and get the generated text back into your workflow.

## What's in the box

### `Allma Connectivity`
Says *how* to reach the backend and *how* to sample.

- `host` / `port` / `timeout`
- `model` — dropdown fed by `GET /v1/models` on the backend
- `temperature` / `top_p` / `top_k` / `max_tokens` / `seed`

Outputs a `ALLMA_CONNECTIVITY` slot that plugs into `Allma Generate`.

### `Allma Load Image`
Drop-in replacement for the stock `Load Image` that *also* extracts the image's
embedded prompt metadata as a `STRING`.

Outputs:

| Slot     | Type   | Notes                                                        |
|----------|--------|--------------------------------------------------------------|
| image    | IMAGE  | identical to what the built-in `Load Image` gives you        |
| mask     | MASK   | idem                                                          |
| metadata | STRING | formatted block: source, model, positive/negative, LoRAs, sampler |

Formats we understand:

- **ComfyUI PNGs** — walks the embedded `prompt`/`workflow` graph and pulls
  positive/negative text, checkpoint, LoRAs and KSampler settings.
- **A1111 PNGs** — parses the `parameters` chunk.
- **JPEG EXIF** — best-effort camera info.
- **Unknown formats** — quietly returns an empty string, so nothing breaks
  downstream.

Feed the `metadata` output into one of `Allma Generate`'s `image_N_meta` inputs
and the LLM will know what generated the picture.

### `Allma Generate`
The one that actually calls the model.

Inputs:

| Slot           | Type   | Notes                                                |
|----------------|--------|------------------------------------------------------|
| connectivity   | required | from `Allma Connectivity`                          |
| model          | optional | native ComfyUI `MODEL` — sniffs LoRAs              |
| image_1..3     | optional | native `IMAGE`, one per slot                       |
| image_1_meta.. | optional | STRING from `Allma Load Image` — see below         |
| audio          | optional | native `AUDIO`                                     |
| preset         | dropdown | pre-baked system prompt                            |
| use_image_metadata | toggle | inject the connected metadata into the system prompt |
| system_prompt  | textbox  | auto-filled from preset if set                     |
| user_prompt    | textbox  | your actual question / instruction                 |

Output: `STRING` with the model's response.

When `use_image_metadata` is on and you connect `image_N_meta`, the block gets
appended to the system prompt as `"Image N metadata: ..."` so the model can do
things like *"same group of people, now on a beach"* — it sees the original
prompt/model/LoRAs behind the reference image and can replay them.

### Presets
Small JSON files under `presets/` shaped like `{"system_prompt": "...", "notes": "..."}`. Managed from the node UI:

- `new preset` — prompts for a name, saves current `system_prompt`
- `save current` — overwrites the selected preset
- `reload` — re-reads from disk
- `delete` — removes the selected preset

Selecting a preset in the dropdown auto-fills the `system_prompt` widget.

## LoRA sniff (roadmap Fase 2)

If you plug your `LoraLoader` output into `Allma Generate`'s `MODEL` slot, the
node walks the `ModelPatcher` looking for applied LoRA files. For each one it
also reads `<name>.metadata.json` (the [LoRA Manager](https://github.com/willmiao/ComfyUI-Lora-Manager) sidecar) — trigger words, notes, tags — and appends a compact
summary to the system prompt so the LLM knows *how* to use each LoRA.

If the sniffer can't find anything (ComfyUI internals shift between versions),
it silently returns empty and everything else keeps working.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/nickolasarthur/ComfyUI-Allma
```

Restart ComfyUI. No pip install needed — this only uses stdlib + torch/PIL which
ComfyUI already ships.

## Requires

- A running Allma (default `http://127.0.0.1:9000`) or any OpenAI-compatible endpoint
- ComfyUI recent enough to expose `AUDIO` type if you want audio input
- `models/loras/**/*.metadata.json` (LoRA Manager format) if you want LoRA sniff

## Roadmap

- **Fase 2 (next)** — LoRA sniffer refinement (ModelPatcher walk) + preset
  auto-augment ("read the LoRAs currently in the workflow, ask the LLM to
  rewrite the system prompt with the trigger words baked in, save it")
- **Fase 3** — Whisper fallback for audio when the target model isn't
  multimodal-audio; multi-turn `Allma Chat` node with history

## License

MIT — see `LICENSE`.
