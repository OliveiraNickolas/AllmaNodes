"""AllmaLoadImage — same UX as the built-in LoadImage, but it also reads the
PNG/JPEG sidecar prompt info.

Use this instead of the stock `Load Image` when you want the LLM to see the
generative history of the image (positive prompt, model, LoRAs, sampler…).

AllmaGenerate no longer needs this node — it recovers metadata by walking the
graph back to whichever loader supplied the picture, the stock Load Image
included. This stays for when you want the metadata as text in the graph.
"""
import hashlib
import os

import numpy as np
import torch
from comfy_api.latest import io
from PIL import Image, ImageOps, ImageSequence

import folder_paths
import node_helpers

from ..api.image_metadata import format_metadata_for_llm, read_image_metadata

LOG = "[ComfyUI-Allma/load_image]"


def _image_files() -> list[str]:
    input_dir = folder_paths.get_input_directory()
    try:
        files = [
            f for f in os.listdir(input_dir)
            if os.path.isfile(os.path.join(input_dir, f))
        ]
        return sorted(folder_paths.filter_files_content_types(files, ["image"]))
    except Exception:
        return []


class AllmaLoadImage(io.ComfyNode):
    """Load an image plus its embedded prompt metadata."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaLoadImage",
            display_name="Allma Load Image",
            category="Allma/utils",
            description=(
                "Load an image and expose the prompt metadata embedded in its "
                "file as text."
            ),
            inputs=[
                io.Combo.Input(
                    "image",
                    options=_image_files(),
                    upload=io.UploadType.image,
                ),
            ],
            outputs=[
                io.Image.Output(display_name="image"),
                io.Mask.Output(display_name="mask"),
                io.String.Output(display_name="metadata"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, image, **_kwargs):
        image_path = folder_paths.get_annotated_filepath(image)
        m = hashlib.sha256()
        with open(image_path, "rb") as f:
            m.update(f.read())
        return m.digest().hex()

    @classmethod
    def execute(cls, image) -> io.NodeOutput:
        image_path = folder_paths.get_annotated_filepath(image)
        try:
            meta = read_image_metadata(image_path)
        except Exception as e:
            print(f"{LOG} metadata read failed for {image_path}: {e}")
            meta = {}
        meta_str = format_metadata_for_llm(meta) if meta else ""

        img = node_helpers.pillow(Image.open, image_path)

        output_images: list[torch.Tensor] = []
        output_masks: list[torch.Tensor] = []
        w: int | None = None
        h: int | None = None

        for frame in ImageSequence.Iterator(img):
            frame = node_helpers.pillow(ImageOps.exif_transpose, frame)
            rgb = frame.convert("RGB")
            if w is None:
                w, h = rgb.size
            if rgb.size != (w, h):
                continue
            arr = np.array(rgb).astype(np.float32) / 255.0
            tensor = torch.from_numpy(arr)[None,]
            if "A" in frame.getbands():
                mask_arr = np.array(frame.getchannel("A")).astype(np.float32) / 255.0
                mask = 1.0 - torch.from_numpy(mask_arr)
            else:
                mask = torch.zeros((64, 64), dtype=torch.float32)
            output_images.append(tensor)
            output_masks.append(mask.unsqueeze(0))

        if not output_images:
            raise RuntimeError(f"Could not decode image: {image_path}")

        image_out = torch.cat(output_images, dim=0)
        mask_out = torch.cat(output_masks, dim=0)
        return io.NodeOutput(image_out, mask_out, meta_str)
