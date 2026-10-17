"""Drawing helpers shared by the preview window, snapshots and the rule drawing tool."""

import cv2
import numpy as np

from app.rule_engine import Rule, RuleHit

# BGR
COLORS = {
    "zone_entry": (60, 60, 230),
    "line_crossing": (0, 200, 255),
    "loitering": (220, 120, 0),
    "occupancy": (200, 0, 200),
    "counting_line": (120, 200, 60),
}
HIT_COLOR = (0, 0, 255)


def draw_rules(img: np.ndarray, rules, highlight: str | None = None) -> np.ndarray:
    h, w = img.shape[:2]
    overlay = img.copy()
    for rule in rules:
        if not rule.enabled:
            continue
        pts = rule.pixel_points(w, h)
        color = COLORS.get(rule.type, (255, 255, 255))
        thick = 3 if rule.id == highlight else 2
        if rule.type in ("line_crossing", "counting_line"):
            cv2.line(img, tuple(pts[0]), tuple(pts[1]), color, thick + 1, cv2.LINE_AA)
            cv2.circle(img, tuple(pts[0]), 5, color, -1)
            label_at = pts[0]
        else:
            cv2.fillPoly(overlay, [pts], color)
            cv2.polylines(img, [pts], True, color, thick, cv2.LINE_AA)
            label_at = pts.min(axis=0)
        label(img, f"{rule.name} ({rule.type})", tuple(int(v) for v in label_at), color)
    return cv2.addWeighted(overlay, 0.18, img, 0.82, 0)


def draw_hit(img: np.ndarray, hit: RuleHit) -> np.ndarray:
    if hit.box:
        x1, y1, x2, y2 = (int(v) for v in hit.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), HIT_COLOR, 3)
        tag = f"{hit.class_name} #{hit.track_id}" if hit.track_id is not None else hit.class_name
        label(img, tag, (x1, max(0, y1 - 22)), HIT_COLOR)
    banner = f"{hit.event_type.upper()}  {hit.rule.name if hit.rule else ''}"
    label(img, banner, (8, 8), HIT_COLOR, scale=0.7)
    return img


def label(img, text: str, origin: tuple[int, int], color, scale: float = 0.5) -> None:
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = origin
    cv2.rectangle(img, (x, y), (x + tw + 8, y + th + base + 6), color, -1)
    cv2.putText(img, text, (x + 4, y + th + 3), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
