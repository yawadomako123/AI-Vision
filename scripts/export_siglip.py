"""One-time: export Google's SigLIP (Apache-2.0) to ONNX for sightings search.

    cd services/vision
    uv run --group export python ../../scripts/export_siglip.py

Writes models/siglip/:
    vision.onnx          image -> embedding   (used by the vision worker, once per sighting)
    text.int8.onnx       text  -> embedding   (used by the API, once per search; 8-bit to save RAM)
    spiece.model         SentencePiece vocabulary
    siglip.json          sizes, normalisation, special token ids, logit scale/bias
    tokenizer_parity.json  test phrases + the official tokenizer's ids (checked by the API tests)
"""

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import SiglipModel, SiglipTokenizer as HfTokenizer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend"))
from app.siglip_tokenizer import SiglipTokenizer  # noqa: E402

MODEL_ID = "google/siglip-base-patch16-224"
OUT = ROOT / "models" / "siglip"
PHRASES = [
    "person in a white hoodie", "a man wearing a red jacket", "Woman with a backpack!", "white van",
    "someone carrying a box", "red car", "person on a bicycle", "kid in a yellow T-shirt",
    "delivery driver, holding a parcel", "black SUV parked", "two people walking", "PERSON IN BLUE",
    "a dog", "motorcycle rider with helmet", "man's grey coat", "person   with  extra   spaces",
    "émigré café — naïve", "truck", "", "a very long description " * 12,
]


class Vision(torch.nn.Module):
    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, pixel_values):
        out = self.m.get_image_features(pixel_values=pixel_values)
        return out.pooler_output if hasattr(out, "pooler_output") else out


class Text(torch.nn.Module):
    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, input_ids):
        out = self.m.get_text_features(input_ids=input_ids)
        return out.pooler_output if hasattr(out, "pooler_output") else out


def norm(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    model = SiglipModel.from_pretrained(MODEL_ID).eval()
    hf_tok = HfTokenizer.from_pretrained(MODEL_ID)
    size = model.config.vision_config.image_size
    max_len = model.config.text_config.max_position_embeddings

    # Tokenizer files + our dependency-free tokenizer, verified against the official one.
    hf_tok.save_pretrained(OUT / "_tok")
    shutil.copy(OUT / "_tok" / "spiece.model", OUT / "spiece.model")
    shutil.rmtree(OUT / "_tok", ignore_errors=True)
    ours = SiglipTokenizer(OUT / "spiece.model", hf_tok.eos_token_id, hf_tok.pad_token_id, max_len)
    parity = []
    for p in PHRASES:
        expected = hf_tok(p, padding="max_length", max_length=max_len, truncation=True)["input_ids"]
        got = ours.encode(p)
        if got != expected:
            raise SystemExit(f"tokenizer mismatch for {p!r}:\n  official {expected[:16]}\n  ours     {got[:16]}")
        parity.append({"text": p, "ids": expected})
    (OUT / "tokenizer_parity.json").write_text(json.dumps(parity), encoding="utf-8")
    print(f"tokenizer parity OK on {len(parity)} phrases")

    meta = {
        "model": MODEL_ID, "license": "Apache-2.0", "image_size": size, "mean": [0.5] * 3, "std": [0.5] * 3,
        "max_length": max_len, "eos_id": hf_tok.eos_token_id, "pad_id": hf_tok.pad_token_id,
        "logit_scale": float(model.logit_scale.exp()), "logit_bias": float(model.logit_bias),
        "dim": model.config.vision_config.hidden_size,
    }
    (OUT / "siglip.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    pixels = torch.rand(1, 3, size, size) * 2 - 1
    ids = torch.tensor([ours.encode("person in a white hoodie")])
    with torch.no_grad():
        torch.onnx.export(Vision(model), (pixels,), OUT / "vision.onnx", input_names=["pixel_values"],
                          output_names=["embedding"], dynamic_axes={"pixel_values": {0: "n"}, "embedding": {0: "n"}},
                          opset_version=17, dynamo=False)
        torch.onnx.export(Text(model), (ids,), OUT / "text.onnx", input_names=["input_ids"],
                          output_names=["embedding"], dynamic_axes={"input_ids": {0: "n"}, "embedding": {0: "n"}},
                          opset_version=17, dynamo=False)
        ref_img = model.get_image_features(pixel_values=pixels)
        ref_txt = model.get_text_features(input_ids=ids)
    ref_img = getattr(ref_img, "pooler_output", ref_img).numpy()
    ref_txt = getattr(ref_txt, "pooler_output", ref_txt).numpy()

    quantize_dynamic(str(OUT / "text.onnx"), str(OUT / "text.int8.onnx"), weight_type=QuantType.QInt8)
    (OUT / "text.onnx").unlink()

    vis = ort.InferenceSession(str(OUT / "vision.onnx"), providers=["CPUExecutionProvider"])
    txt = ort.InferenceSession(str(OUT / "text.int8.onnx"), providers=["CPUExecutionProvider"])
    cos_img = float((norm(vis.run(None, {"pixel_values": pixels.numpy()})[0]) * norm(ref_img)).sum())
    cos_txt = float((norm(txt.run(None, {"input_ids": ids.numpy()})[0]) * norm(ref_txt)).sum())
    print(f"ONNX vs PyTorch cosine: vision {cos_img:.4f}, text (int8) {cos_txt:.4f}")
    if cos_img < 0.999 or cos_txt < 0.98:
        raise SystemExit("exported models drift too far from the original")
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name:24s} {f.stat().st_size / 1e6:8.1f} MB")


if __name__ == "__main__":
    main()
