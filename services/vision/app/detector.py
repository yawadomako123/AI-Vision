"""RF-DETR ONNX detector.

Backends:
  GPU / CPU / AUTO -> OpenVINO (Intel iGPU or CPU), compiled model cached on disk
  CPU-ONLY         -> plain ONNX Runtime, portable fallback
"""

import logging
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

log = logging.getLogger(__name__)

# COCO category ids as used by RF-DETR's pretrained head (ids have gaps, 0 = unused).
COCO_CLASSES = {
    1: "person", 2: "bicycle", 3: "car", 4: "motorcycle", 5: "airplane", 6: "bus", 7: "train",
    8: "truck", 9: "boat", 10: "traffic light", 11: "fire hydrant", 13: "stop sign",
    14: "parking meter", 15: "bench", 16: "bird", 17: "cat", 18: "dog", 19: "horse", 20: "sheep",
    21: "cow", 22: "elephant", 23: "bear", 24: "zebra", 25: "giraffe", 27: "backpack",
    28: "umbrella", 31: "handbag", 32: "tie", 33: "suitcase", 34: "frisbee", 35: "skis",
    36: "snowboard", 37: "sports ball", 38: "kite", 39: "baseball bat", 40: "baseball glove",
    41: "skateboard", 42: "surfboard", 43: "tennis racket", 44: "bottle", 46: "wine glass",
    47: "cup", 48: "fork", 49: "knife", 50: "spoon", 51: "bowl", 52: "banana", 53: "apple",
    54: "sandwich", 55: "orange", 56: "broccoli", 57: "carrot", 58: "hot dog", 59: "pizza",
    60: "donut", 61: "cake", 62: "chair", 63: "couch", 64: "potted plant", 65: "bed",
    67: "dining table", 70: "toilet", 72: "tv", 73: "laptop", 74: "mouse", 75: "remote",
    76: "keyboard", 77: "cell phone", 78: "microwave", 79: "oven", 80: "toaster", 81: "sink",
    82: "refrigerator", 84: "book", 85: "clock", 86: "vase", 87: "scissors", 88: "teddy bear",
    89: "hair drier", 90: "toothbrush",
}

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class Detector:
    def __init__(
        self,
        model_path: Path,
        device: str = "GPU",
        confidence: float = 0.4,
        classes: set[str] | None = None,
    ):
        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"{model_path} not found. Run: uv run --group export python ../../scripts/export_model.py"
            )
        self.confidence = confidence
        self.keep_ids = (
            np.array([i for i, n in COCO_CLASSES.items() if n in classes])
            if classes
            else None
        )
        self._run = None
        if device.upper() != "CPU-ONLY":
            try:
                self._init_openvino(Path(model_path), device.upper())
            except Exception as exc:  # noqa: BLE001
                log.warning("OpenVINO %s unavailable (%s); falling back to ONNX Runtime CPU", device, exc)
        if self._run is None:
            self._init_onnxruntime(Path(model_path))

    def _init_openvino(self, model_path: Path, device: str) -> None:
        import openvino as ov

        core = ov.Core()
        # GPU compilation takes ~1 min the first time; the cache makes later starts take seconds.
        core.set_property({"CACHE_DIR": str(model_path.parent / ".ov_cache")})
        compiled = core.compile_model(core.read_model(model_path), device, {"PERFORMANCE_HINT": "LATENCY"})
        request = compiled.create_infer_request()
        self.resolution = int(compiled.input(0).get_partial_shape()[2].get_length())
        names = [o.get_any_name() for o in compiled.outputs]

        def run(x: np.ndarray) -> dict[str, np.ndarray]:
            result = request.infer({0: x})
            return {name: result[out] for name, out in zip(names, compiled.outputs)}

        self._run = run
        self.provider = f"OpenVINO {device}"

    def _init_onnxruntime(self, model_path: Path) -> None:
        import onnxruntime as ort

        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        inp = session.get_inputs()[0]
        self.resolution = int(inp.shape[2])
        names = [o.name for o in session.get_outputs()]
        self._run = lambda x: dict(zip(names, session.run(None, {inp.name: x})))
        self.provider = "ONNX Runtime CPU"

    def preprocess(self, bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(cv2.resize(bgr, (self.resolution, self.resolution)), cv2.COLOR_BGR2RGB)
        x = (rgb.astype(np.float32) / 255.0 - _MEAN) / _STD
        return x.transpose(2, 0, 1)[None]

    def __call__(self, bgr: np.ndarray) -> sv.Detections:
        h, w = bgr.shape[:2]
        outputs = self._run(self.preprocess(bgr))
        boxes = outputs["dets"][0]  # (N, 4) cx, cy, w, h normalized
        logits = outputs["labels"][0]  # (N, C)

        scores = 1.0 / (1.0 + np.exp(-logits))
        class_ids = scores.argmax(axis=1)
        conf = scores[np.arange(len(scores)), class_ids]

        keep = conf >= self.confidence
        if self.keep_ids is not None:
            keep &= np.isin(class_ids, self.keep_ids)
        boxes, class_ids, conf = boxes[keep], class_ids[keep], conf[keep]

        cx, cy, bw, bh = boxes.T
        xyxy = np.stack([(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h], axis=1)
        return sv.Detections(
            xyxy=xyxy.astype(np.float32),
            confidence=conf.astype(np.float32),
            class_id=class_ids.astype(int),
            data={"class_name": np.array([COCO_CLASSES.get(int(c), str(c)) for c in class_ids])},
        )
