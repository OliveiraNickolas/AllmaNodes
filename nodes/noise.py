"""Allma RandomNoise — A clone of ComfyUI's RandomNoise that supports Subgraph promotion.

Why this exists
--------------
ComfyUI's built-in RandomNoise uses an attached `control_after_generate=True` flag
on `noise_seed`. That creates an internal canvasOnly widget rather than an explicit
schema input. As a result, ComfyUI's Subgraph system does NOT allow promoting
`control_after_generate` out of a subgraph.

AllmaRandomNoise exposes `control_after_generate` as a first-class promotable input,
allowing both `noise_seed` and its generation behavior (randomize, fixed, increment, decrement)
to be promoted and controlled directly from the outside of any Subgraph.
"""
import comfy.sample
from comfy_api.latest import io


class Noise_RandomNoise:
    def __init__(self, seed: int):
        self.seed = int(seed)

    def generate_noise(self, input_latent):
        latent_image = input_latent["samples"]
        batch_inds = input_latent["batch_index"] if "batch_index" in input_latent else None
        return comfy.sample.prepare_noise(latent_image, self.seed, batch_inds)


class AllmaRandomNoise(io.ComfyNode):
    """Generates noise with a seed and promotable control_after_generate behavior."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AllmaRandomNoise",
            display_name="Allma RandomNoise",
            category="Allma/sampling",
            search_aliases=["noise", "random", "seed", "randomnoise", "sampling", "subgraph"],
            description=(
                "RandomNoise clone designed for Subgraphs: both noise_seed and "
                "control_after_generate are first-class inputs that can be promoted "
                "to the containing Subgraph node."
            ),
            inputs=[
                io.Int.Input(
                    "noise_seed",
                    default=0,
                    min=0,
                    max=0xffffffffffffffff,
                    control_after_generate=False,
                    tooltip="The random seed used for creating the noise.",
                ),
                io.Combo.Input(
                    "control_after_generate",
                    options=["randomize", "fixed", "increment", "decrement"],
                    default="randomize",
                    socketless=True,
                    tooltip="Control behavior after each generation: randomize, fixed, increment, or decrement. Promotable to Subgraphs.",
                ),
            ],
            outputs=[io.Noise.Output(display_name="NOISE")],
        )

    @classmethod
    def execute(cls, noise_seed: int, control_after_generate: str = "randomize") -> io.NodeOutput:
        return io.NodeOutput(Noise_RandomNoise(noise_seed))

    get_noise = execute
