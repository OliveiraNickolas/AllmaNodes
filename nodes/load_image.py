"""AllmaLoadImage — same UX as the built-in LoadImage, but with an extra
METADATA STRING output that reads the PNG/JPEG sidecar prompt info.

Use this instead of the stock `Load Image` when you want the LLM to see the
generative history of the image (positive prompt, model, LoRAs, sampler…).
"""
import hashlib
import os

import numpy as np
import torch
from PIL import Image, ImageOps, ImageSequence

import folder_paths
import node_helpers

from ..api.image_metadata import format_metadata_for_llm, read_image_metadata

LOG = "[ComfyUI-Allma/load_image]"


class AllmaLoadImage:
    """Load an image plus its embedded prompt metadata."""

    @classmethod
    def INPUT_TYPES(cls):
        input_dir = folder_paths.get_input_directory()
        try:
            files = [
                f for f in os.listdir(input_dir)
                if os.path.isfile(os.path.join(input_dir, f))
            ]
            files = folder_paths.filter_files_content_types(files, ["image"])
        except Exception:
            files = []
        return {
            "required": {
                "image": (sorted(files), {"image_upload": True}),
            }
        }

    CATEGORY = "Allma"
    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("image", "mask", "metadata")
    FUNCTION = "load"

    def load(self, image):
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
        return (image_out, mask_out, meta_str)

    @classmethod
    def IS_CHANGED(cls, image):
        image_path = folder_paths.get_annotated_filepath(image)
        m = hashlib.sha256()
        with open(image_path, "rb") as f:
            m.update(f.read())
        return m.digest().hex()

    @classmethod
    def VALIDATE_INPUTS(cls, image):
        if not folder_paths.exists_annotated_filepath(image):
            return f"Invalid image file: {image}"
        return True
