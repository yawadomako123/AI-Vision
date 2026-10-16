"""Draw zones and tripwires on a live camera view; saves to config/rules.json.

    uv run python -m app.draw_rules                 # cam1 from CAMERA_SOURCES
    uv run python -m app.draw_rules --camera cam2
    uv run python -m app.draw_rules --source ../../samples/clip.mp4 --camera cam1

Keys:  1 zone entry   2 tripwire (line)   3 loitering zone   4 occupancy zone
       click = add point   Enter = finish zone   u = undo point   Esc = cancel
       d = delete last rule   q = quit
Every finished or deleted rule is saved immediately; a running worker picks it up in ~2 s.
Fine-tune names, classes, dwell_seconds, max_count etc. by editing the JSON afterwards.
"""

import argparse
import json
import time

import cv2
import numpy as np

from app.config import settings
from app.frame_reader import FrameReader
from app.overlay import COLORS, draw_rules, label
from app.rule_engine import Rule

TOOLS = {ord("1"): "zone_entry", ord("2"): "line_crossing", ord("3"): "loitering", ord("4"): "occupancy"}
TITLES = {"zone_entry": "Zone", "line_crossing": "Tripwire", "loitering": "Loitering zone", "occupancy": "Occupancy zone"}
HELP = "1 zone  2 tripwire  3 loitering  4 occupancy | click points, Enter=finish u=undo Esc=cancel | d=delete last  q=quit"


def load(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic, so the worker never reads a half-written file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", default="cam1", help="camera id the rules belong to (cam1, cam2, ...)")
    parser.add_argument("--source", help="stream/file to draw on (default: matching CAMERA_SOURCES entry)")
    args = parser.parse_args()

    index = int(args.camera.removeprefix("cam")) - 1
    source = args.source or settings.sources[index]
    reader = FrameReader(args.camera, source).start()
    print(f"Connecting to {source} ...")

    data = load(settings.rules_path)
    data.setdefault(args.camera, [])
    tool: str | None = None
    points: list[tuple[int, int]] = []
    size = {"w": 1, "h": 1}

    def finish():
        nonlocal tool, points
        n = 1 + sum(1 for r in data[args.camera] if r["type"] == tool)
        existing = {r["id"] for r in data[args.camera]}
        rule_id = f"{tool}_{n}"
        while rule_id in existing:
            n += 1
            rule_id = f"{tool}_{n}"
        rule = {
            "id": rule_id,
            "type": tool,
            "name": f"{TITLES[tool]} {n}",
            "points": [[round(x / size["w"], 4), round(y / size["h"], 4)] for x, y in points],
            "classes": ["person"],
        }
        if tool == "loitering":
            rule["dwell_seconds"] = 20
        if tool == "occupancy":
            rule["max_count"] = 3
        data[args.camera].append(rule)
        save(settings.rules_path, data)
        print(f"Saved {rule['name']} ({rule_id})")
        tool, points = None, []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and not tool:
            print("Press 1-4 first to choose what to draw.")
        elif event == cv2.EVENT_LBUTTONDOWN and tool:
            points.append((x, y))
            print(f"  point {len(points)} at ({x}, {y})")
            if tool == "line_crossing" and len(points) == 2:
                finish()

    window = f"Draw rules - {args.camera}"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)
    # Keep it above the worker's preview window; keys only reach the focused window.
    cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 1)
    print(f"\nClick the '{window}' window first so it has focus, then:\n  {HELP}\n")

    last = None
    while True:
        frame = reader.latest(timeout=0.05)
        if frame is not None:
            last = frame.image
        if last is None:
            img = np.zeros((480, 640, 3), np.uint8)
            label(img, f"Waiting for {args.camera} ({reader.status}) ...", (20, 220), (80, 80, 80), 0.6)
        else:
            size["h"], size["w"] = last.shape[:2]
            rules = [Rule.from_dict(r) for r in data[args.camera]]
            img = draw_rules(last.copy(), rules)
            if tool:
                color = COLORS[tool]
                for p in points:
                    cv2.circle(img, p, 5, color, -1)
                if len(points) > 1:
                    cv2.polylines(img, [np.array(points)], tool != "line_crossing", color, 2, cv2.LINE_AA)
                need = "click 2 points" if tool == "line_crossing" else "click 3+ points, Enter to finish"
                label(img, f"Drawing {TITLES[tool]}: {need}", (8, 8), color, 0.6)
            label(img, HELP, (8, img.shape[0] - 26), (40, 40, 40), 0.4)
        cv2.imshow(window, img)

        key = cv2.waitKey(30) & 0xFF
        if key == ord("q") or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            break
        if key in TOOLS and last is None:
            print("No video yet: start streaming from the phone first.")
        elif key in TOOLS:
            tool, points = TOOLS[key], []
            need = "click 2 points" if tool == "line_crossing" else "click 3+ points, then Enter"
            print(f"Drawing {TITLES[tool]}: {need}")
        elif key == 13 and tool and tool != "line_crossing":  # Enter
            if len(points) >= 3:
                finish()
            else:
                print(f"  need at least 3 points (have {len(points)})")
        elif key == ord("u") and points:
            points.pop()
        elif key == 27:  # Esc
            tool, points = None, []
        elif key == ord("d") and data[args.camera] and not tool:
            removed = data[args.camera].pop()
            save(settings.rules_path, data)
            print(f"Deleted {removed['name']}")

    reader.stop()
    cv2.destroyAllWindows()
    time.sleep(0.1)


if __name__ == "__main__":
    main()
