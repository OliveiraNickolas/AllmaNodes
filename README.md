# ComfyUI-Allma

Custom nodes for wiring [ComfyUI](https://github.com/comfyanonymous/ComfyUI) to the [Allma](https://github.com/nickolasarthur/allma) LLM backend (or any OpenAI-compatible endpoint).

Two nodes, one purpose: send a prompt (plus up to three images and audio) to a
local LLM and get the generated text back into your workflow.

## What's in the box (Fase 1 — MVP)

### `Allma Connectivity`
Says *how* to reach the backend and *how* to sample.

- `host` / `port` / `timeout`
- `model` — dropdown fed by `GET /v1/models` on the backend
- `temperature` / `top_p` / `top_k` / `max_tokens` / `seed`

Outputs a `ALLMA_CONNECTIVITY` slot that plugs into `Allma Generate`.

### `Allma Generate`
The one that actually calls the model.

Inputs:

| Slot         | Type       | Notes                                     |
|--------------|------------|-------------------------------------------|
| connectivity | required   | from `Allma Connectivity`                 |
| model        | optional   | native ComfyUI `MODEL` — sniffs LoRAs     |
| image_1..3   | optional   | native `IMAGE`, one per slot              |
| audio        | optional   | native `AUDIO`                            |
| preset       | dropdown   | pre-baked system prompt                   |
| system_prompt| textbox    | auto-filled from preset if set            |
| user_prompt  | textbox    | your actual question / instruction        |

Output: `STRING` with the model's response.

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

- **Fase 2** — LoRA sniffer wiring + preset auto-augment ("read the LoRAs
  currently in the workflow, ask the LLM to rewrite the system prompt with the
  trigger words baked in, save it")
- **Fase 3** — First-class Whisper fallback for audio when the target model
  isn't multimodal-audio

## License

MIT — see `LICENSE`.
