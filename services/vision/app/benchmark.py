"""Day-1 benchmark: how fast can this machine run the detector?

    uv run python -m app.benchmark                          # synthetic frames, all devices
    uv run python -m app.benchmark --video ../../samples/door.mp4
    uv run python -m app.benchmark --devices GPU --iterations 200

Reports latency per device and the max cameras supportable at the target sampling rate.
"""

import argparse
import logging
import statistics
import time

import av
import numpy as np

from app.config import settings
from app.detector import Detector


def load_frames(video: str | None, count: int = 50) -> list[np.ndarray]:
    if not video:
        rng = np.random.default_rng(0)
        return [rng.integers(0, 255, (480, 640, 3), dtype=np.uint8) for _ in range(count)]
    frames = []
    with av.open(video) as container:
        for frame in container.decode(video=0):
            frames.append(frame.to_ndarray(format="bgr24"))
            if len(frames) >= count:
                break
    return frames


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", help="video file to sample frames from (default: synthetic)")
    parser.add_argument("--devices", default="GPU,CPU,CPU-ONLY", help="OpenVINO GPU/CPU, or CPU-ONLY for ONNX Runtime")
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    frames = load_frames(args.video)
    print(f"Model: {settings.model_path.name}   frames: {frames[0].shape[1]}x{frames[0].shape[0]}"
          f"   target: {settings.inference_fps} fps/camera\n")
    print(f"{'device':<10}{'provider':<28}{'mean ms':>9}{'p95 ms':>9}{'max fps':>9}{'cameras':>9}  detections")

    for device in args.devices.split(","):
        try:
            det = Detector(settings.model_path, device, settings.detection_confidence, settings.classes)
        except Exception as exc:  # noqa: BLE001
            print(f"{device:<10}unavailable: {exc}")
            continue
        for f in frames[:5]:  # warm-up (OpenVINO GPU compiles on first runs)
            det(f)
        times, n_det = [], 0
        for i in range(args.iterations):
            t0 = time.perf_counter()
            n_det += len(det(frames[i % len(frames)]))
            times.append((time.perf_counter() - t0) * 1000)
        mean = statistics.mean(times)
        p95 = sorted(times)[int(len(times) * 0.95) - 1]
        max_fps = 1000 / mean
        provider = det.provider
        print(f"{device:<10}{provider:<28}{mean:>9.1f}{p95:>9.1f}{max_fps:>9.1f}"
              f"{int(max_fps // settings.inference_fps):>9}  {n_det / args.iterations:.1f}/frame")

    print("\n'cameras' = how many streams this device can sustain at the target fps (ignores decode cost;"
          " keep ~30% headroom).")


if __name__ == "__main__":
    main()
