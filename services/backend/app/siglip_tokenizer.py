"""SigLIP text tokenizer without the `transformers` dependency (only `sentencepiece`).

Reproduces transformers' SiglipTokenizer exactly (checked against it by
scripts/export_siglip.py, which saves parity cases next to the model):
lowercase, strip ASCII punctuation, SentencePiece with an <unk> prefix trick (the model was
trained without a dummy prefix), append </s>, pad/truncate to a fixed length.
"""

import string
from pathlib import Path

import sentencepiece as spm

SPIECE_UNDERLINE = "▁"
_PUNCT = str.maketrans("", "", string.punctuation)


class SiglipTokenizer:
    def __init__(self, model_file: Path, eos_id: int, pad_id: int, max_length: int = 64):
        self.sp = spm.SentencePieceProcessor(model_file=str(model_file))
        self.eos_id, self.pad_id, self.max_length = eos_id, pad_id, max_length
        self.unk = self.sp.id_to_piece(self.sp.unk_id())
        self.unk_len = len(self.sp.encode(self.unk))

    def _canonicalize(self, text: str) -> str:
        return text.lower().translate(_PUNCT).strip()

    def encode(self, text: str) -> list[int]:
        text = self._canonicalize(SPIECE_UNDERLINE + text.replace(SPIECE_UNDERLINE, " "))
        pieces = self.sp.encode(self.unk + text, out_type=str)
        pieces = pieces[self.unk_len:] if len(pieces) >= self.unk_len else pieces
        ids = [self.sp.piece_to_id(p) for p in pieces][: self.max_length - 1] + [self.eos_id]
        return ids + [self.pad_id] * (self.max_length - len(ids))
