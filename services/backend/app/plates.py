"""Number plates on vehicle sightings: fuzzy matching, search and the watchlist.

Plate reading misses characters now and then (O/0, B/8, I/1), so plates are compared with a
similarity score rather than exact equality: search shows the closest plates first, and a
watched plate matches when it's at least 85% similar (one character off in a typical plate).
"""

import difflib
import re

WATCH_MATCH = 0.85
SEARCH_MATCH = 0.6
# Characters plate readers commonly confuse: compared as the same when matching.
LOOKALIKE = str.maketrans({"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "B": "8", "S": "5", "Z": "2", "G": "6"})


def normalize(text: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", (text or "").upper())


def similarity(a: str, b: str) -> float:
    """0..1, tolerant of look-alike characters; a query found inside a plate counts as a match."""
    a, b = normalize(a).translate(LOOKALIKE), normalize(b).translate(LOOKALIKE)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 3 and (a in b or b in a):
        return 0.9  # partial plate ("GR12" finds "GR123423")
    return difflib.SequenceMatcher(None, a, b).ratio()
