"""HTTP client for talking to the Allma backend (OpenAI-compatible).

Uses only stdlib (urllib + json + base64) so we don't ship extra deps.
Handles 503 "loading" transparently by retrying with a short backoff.
"""
import base64
import io
import json
import time
import urllib.error
import urllib.request
import wave

from .interrupt import clear_response, is_cancelled, register_response

LOG = "[AllmaNodes]"


class Cancelled(Exception):
    """Raised internally when the stop button fires. Carries partial output."""

    def __init__(self, content: str = "", thinking: str = ""):
        super().__init__("generation interrupted")
        self.content = content
        self.thinking = thinking


def _url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def list_models(host: str, port: int, timeout: float = 5.0) -> list[str]:
    """Return the list of model IDs the allma exposes, or [] if unreachable."""
    try:
        req = urllib.request.Request(
            _url(host, port, "/v1/models"),
            headers={"Authorization": "Bearer dummy"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        return [m["id"] for m in data.get("data", []) if "id" in m]
    except Exception as e:
        print(f"{LOG} list_models failed: {e}")
        return []


def image_tensor_to_data_url(tensor) -> str:
    """ComfyUI IMAGE tensor (1, H, W, C) in [0,1] → data:image/png;base64,..."""
    import numpy as np
    from PIL import Image

    if tensor is None:
        return ""
    if hasattr(tensor, "detach"):
        arr = tensor.detach().cpu().numpy()
    else:
        arr = np.asarray(tensor)
    if arr.ndim == 4:
        arr = arr[0]
    arr = (arr.clip(0.0, 1.0) * 255.0).round().astype("uint8")
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def audio_dict_to_wav_b64(audio: dict) -> tuple[str, str]:
    """ComfyUI AUDIO ({"waveform": tensor, "sample_rate": int}) → (base64_data, "wav").

    Returns ("", "") if audio is empty.
    """
    if not audio:
        return "", ""
    wf = audio.get("waveform")
    sr = int(audio.get("sample_rate", 16000))
    if wf is None:
        return "", ""
    if hasattr(wf, "detach"):
        arr = wf.detach().cpu().numpy()
    else:
        import numpy as np
        arr = np.asarray(wf)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.ndim == 2 and arr.shape[0] < arr.shape[1]:
        arr = arr[0]
    elif arr.ndim == 2:
        arr = arr.mean(axis=1)
    import numpy as np
    pcm = (arr.clip(-1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return base64.b64encode(buf.getvalue()).decode(), "wav"


def build_user_content(
    text: str,
    image_data_urls: list[str],
    audio_b64: str,
    audio_format: str,
) -> list[dict] | str:
    """Assemble OpenAI-style multimodal content array. Returns a plain string when
    there are no attachments (some backends prefer the simpler form).

    Each image is preceded by a short text part. This is not cosmetic: with
    llama.cpp b10433 and a Qwen3-VL mmproj, ADJACENT image parts collapse in
    pairs — send four and the model receives two, send six and it receives
    three, silently. Measured by sending numbered colour swatches and asking
    the model to name them back: consecutive images returned every other one,
    while the same images separated by any text part all arrived.

    The label doubles as the numbering the prompts rely on, so "Image 3" in a
    system prompt now points at the picture the model actually saw.
    """
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for idx, url in enumerate(image_data_urls, start=1):
        if url:
            parts.append({"type": "text", "text": f"Image {idx}:"})
            parts.append({"type": "image_url", "image_url": {"url": url}})
    if audio_b64:
        parts.append(
            {"type": "input_audio", "input_audio": {"data": audio_b64, "format": audio_format}}
        )
    if len(parts) == 0:
        return ""
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts


def _consume_stream(r, relay=None) -> tuple[str, str, str]:
    """Read an OpenAI-style SSE stream → (content, thinking, finish_reason).

    Checks the cancel token between chunks. On stop, raises Cancelled carrying
    whatever was produced so far, so the node can still return partial text.

    `relay`, when given, receives each piece as it arrives so the UI can show
    the reasoning live instead of only after the answer lands.
    """
    content_parts: list[str] = []
    think_parts: list[str] = []
    finish_reason = ""

    def _partial() -> tuple[str, str]:
        return "".join(content_parts), "".join(think_parts)

    try:
        for raw in r:
            if is_cancelled():
                c, t = _partial()
                raise Cancelled(c, t)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                if relay is not None:
                    relay.add(piece, "content")
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                think_parts.append(reasoning)
                if relay is not None:
                    relay.add(reasoning, "reasoning")
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"]).lower()
    except Cancelled:
        raise
    except Exception:
        # A read that blows up right after the stop button is the socket being
        # closed under us on purpose — treat it as a clean cancel, not an error.
        if is_cancelled():
            c, t = _partial()
            raise Cancelled(c, t) from None
        raise

    if is_cancelled():
        c, t = _partial()
        raise Cancelled(c, t)

    return "".join(content_parts), "".join(think_parts), finish_reason


def chat_completion(
    host: str,
    port: int,
    timeout: float,
    model: str,
    system_prompt: str,
    user_content,
    temperature: float = 1.0,
    top_p: float = 0.95,
    top_k: int = 20,
    max_tokens: int = 2048,
    seed: int | None = None,
    enable_thinking: bool = False,
    retry_on_loading: bool = True,
    max_retries: int = 40,
    relay=None,
) -> tuple[str, str, str]:
    """POST /v1/chat/completions. Returns (content, thinking, status).

    `status` is a human-readable note about anything that went wrong (e.g. the
    answer was truncated). It is deliberately kept OUT of `content` so callers
    never emit a diagnostic where a prompt is expected — empty means clean run.

    When enable_thinking is False, we ask the chat template to skip the
    <think>...</think> block via chat_template_kwargs; Qwen3-style models
    respect this. The thinking channel is returned separately so callers can
    surface it in a dedicated output slot without polluting the main response.

    On 503 "loading model" we back off and retry — allma may be starting a
    fresh backend. Raises RuntimeError on unrecoverable errors.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_content})

    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "top_p": float(top_p),
        "max_tokens": int(max_tokens),
        # Streaming is what makes the stop button possible: we get control back
        # between chunks. It also turns `timeout` into an inactivity timeout
        # (per socket read) instead of a deadline for the whole answer, so a
        # long-but-progressing generation no longer dies at the 120s mark.
        "stream": True,
    }
    if top_k > 0:
        body["top_k"] = int(top_k)
    if seed is not None and seed >= 0:
        body["seed"] = int(seed)
    if not enable_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _url(host, port, "/v1/chat/completions"),
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer dummy",
        },
        method="POST",
    )

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                register_response(r)
                try:
                    content, thinking, finish_reason = _consume_stream(r, relay)
                finally:
                    clear_response()
            content = content.strip()
            thinking = thinking.strip()
            if not content and thinking:
                if "</think>" in thinking:
                    parts = thinking.rsplit("</think>", 1)
                    thinking, tail = parts[0], parts[1].strip()
                    if tail:
                        content = tail
                        thinking = thinking.replace("<think>", "", 1).strip()
            # Logged on every run: when an answer comes back clipped, the split
            # between the two channels and the finish_reason are what tell a
            # real truncation apart from text that merely landed in 'thinking'.
            print(
                f"{LOG} finish_reason={finish_reason or 'stop'} "
                f"content={len(content)} chars  reasoning={len(thinking)} chars"
            )

            status = ""
            if finish_reason == "length":
                if not content and thinking:
                    status = (
                        f"response cut off — max_tokens={max_tokens} was exhausted "
                        f"mid-thinking, so no answer was ever produced. Fix: turn "
                        f"'thinking' OFF, or raise max_tokens to 4096+, or reset "
                        f"sampling to Qwen3 official thinking-mode "
                        f"(temperature=1.0, top_p=0.95, top_k=20)."
                    )
                else:
                    status = (
                        f"response truncated — max_tokens={max_tokens} was reached. "
                        f"The text below is incomplete."
                    )
                print(f"{LOG} ⚠ {status}")
            return content, thinking, status
        except Cancelled:
            # Deliberate stop — must not be retried nor wrapped as a failure.
            raise
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code == 503 and retry_on_loading and "loading" in body_text.lower():
                sleep_for = min(2 + attempt, 10)
                print(f"{LOG} backend loading — retry in {sleep_for}s ({attempt + 1}/{max_retries})")
                time.sleep(sleep_for)
                last_err = e
                continue
            raise RuntimeError(f"HTTP {e.code}: {body_text or e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection failed: {e.reason}") from e
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}") from e

    raise RuntimeError(
        f"Backend never became ready after {max_retries} retries: {last_err}"
    )
    # Unreachable; keeps type checkers happy about the return type.
    return "", "", ""
