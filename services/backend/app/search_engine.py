"""Plain-English search over sightings with SigLIP.

The worker stores one L2-normalised image embedding per sighting. Here the query text is turned
into an embedding with the SigLIP text tower (8-bit ONNX, loaded on first use), and sightings are
ranked by cosine similarity. "Find similar" ranks by image-to-image similarity instead.
"""

import json
import logging
import threading
from pathlib import Path

import numpy as np

from app.siglip_tokenizer import SiglipTokenizer

log = logging.getLogger(__name__)


class SearchUnavailable(RuntimeError):
    pass


class TextEncoder:
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer: SiglipTokenizer | None = None
        self.meta: dict = {}

    @property
    def available(self) -> bool:
        return (self.model_dir / "text.int8.onnx").exists() and (self.model_dir / "spiece.model").exists()

    def _load(self) -> None:
        if self._session is not None:
            return
        if not self.available:
            raise SearchUnavailable("search model not installed: run scripts/export_siglip.py")
        import onnxruntime as ort

        self.meta = json.loads((self.model_dir / "siglip.json").read_text(encoding="utf-8"))
        self._tokenizer = SiglipTokenizer(self.model_dir / "spiece.model", self.meta["eos_id"],
                                          self.meta["pad_id"], self.meta["max_length"])
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2  # one query at a time; leave CPU for the worker
        self._session = ort.InferenceSession(str(self.model_dir / "text.int8.onnx"), opts,
                                             providers=["CPUExecutionProvider"])
        log.info("search text model loaded")

    def encode(self, text: str) -> np.ndarray:
        with self._lock:
            self._load()
            ids = np.array([self._tokenizer.encode(text)], np.int64)
            emb = self._session.run(None, {"input_ids": ids})[0][0].astype(np.float32)
        return emb / (np.linalg.norm(emb) + 1e-12)


def decode_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, np.float16).astype(np.float32)


def rank(query: np.ndarray, blobs: list[bytes]) -> np.ndarray:
    """Cosine similarity of the query to each stored embedding."""
    if not blobs:
        return np.zeros(0, np.float32)
    matrix = np.stack([decode_embedding(b) for b in blobs])
    return matrix @ query


# SigLIP text-to-image cosines sit in a narrow band; map them to a readable 0..1 relevance.
# Calibrated on a person cut-out from the test clip: "person in a white hoodie" +0.139,
# "a person" +0.066, wrong descriptions ("red car", "a dog", "woman in a red dress") <= +0.027.
# Below TEXT_LOW a result is treated as not matching at all and hidden.
TEXT_LOW, TEXT_HIGH = 0.03, 0.13
# Image-to-image: the same person (mirrored) 0.99, person vs an empty background 0.53.
IMAGE_LOW, IMAGE_HIGH = 0.6, 0.95


def text_relevance(cos: float) -> float:
    return float(np.clip((cos - TEXT_LOW) / (TEXT_HIGH - TEXT_LOW), 0, 1))


def image_relevance(cos: float) -> float:
    return float(np.clip((cos - IMAGE_LOW) / (IMAGE_HIGH - IMAGE_LOW), 0, 1))
