"""One-time: download RF-DETR (Apache-2.0) and export it to ONNX in ./models.

Run from services/vision:
    uv run --group export python ../../scripts/export_model.py [nano|small|medium]
"""

import shutil
import sys
from pathlib import Path

import rfdetr

VARIANTS = {"nano": rfdetr.RFDETRNano, "small": rfdetr.RFDETRSmall, "medium": rfdetr.RFDETRMedium}

variant = sys.argv[1] if len(sys.argv) > 1 else "nano"
models_dir = Path(__file__).resolve().parents[1] / "models"
tmp_dir = models_dir / f"_export_{variant}"

model = VARIANTS[variant]()
model.export(output_dir=str(tmp_dir))

onnx_files = list(tmp_dir.rglob("*.onnx"))
if not onnx_files:
    raise SystemExit(f"Export produced no .onnx file in {tmp_dir}")

target = models_dir / f"rfdetr-{variant}.onnx"
shutil.move(str(onnx_files[0]), target)
shutil.rmtree(tmp_dir, ignore_errors=True)
print(f"Exported {target} (input resolution {model.model_config.resolution})")
