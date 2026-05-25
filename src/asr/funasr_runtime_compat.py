from __future__ import annotations

import threading
from pathlib import Path

_PATCH_LOCK = threading.Lock()
_PATCHED = False


def patch_sentencepiece_unicode_path_support() -> None:
    global _PATCHED
    if _PATCHED:
        return

    with _PATCH_LOCK:
        if _PATCHED:
            return

        import sentencepiece as spm
        from funasr.tokenizer.sentencepiece_tokenizer import SentencepiecesTokenizer

        original_builder = SentencepiecesTokenizer._build_sentence_piece_processor

        def _build_sentence_piece_processor(self) -> None:
            if self.sp is not None:
                return

            try:
                model_bytes = Path(self.bpemodel).read_bytes()
            except OSError:
                original_builder(self)
                return

            processor = spm.SentencePieceProcessor()
            try:
                processor.LoadFromSerializedProto(model_bytes)
            except Exception:
                original_builder(self)
                return

            self.sp = processor

        SentencepiecesTokenizer._build_sentence_piece_processor = _build_sentence_piece_processor
        _PATCHED = True
