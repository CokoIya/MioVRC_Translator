from __future__ import annotations

import re


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _common_prefix(left: str, right: str) -> str:
    prefix_chars: list[str] = []
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        prefix_chars.append(left_char)
    return "".join(prefix_chars).rstrip()


class StreamingMerger:
    """流式 ASR 结果合并器。

    连续多帧 partial 结果的公共前缀如果稳定出现 stable_repeats 次，
    就把它锁定为稳定前缀，避免 partial 乱跳时回退到更短的文本。
    """

    def __init__(self, stable_repeats: int = 2):
        self._stable_repeats = max(int(stable_repeats), 1)
        self.reset()

    def reset(self):
        self._stable_prefix = ""
        self._candidate_prefix = ""
        self._candidate_hits = 0
        self._last_partial = ""
        self._current_text = ""

    def ingest_partial(self, text: str) -> str:
        normalized = _normalize_text(text)
        if not normalized:
            return self._current_text

        if not self._last_partial:
            self._last_partial = normalized
            self._current_text = normalized
            return normalized

        common = _common_prefix(self._last_partial, normalized)
        if len(common) > len(self._stable_prefix):
            # 候选前缀连续出现足够次数才晋升为稳定前缀
            if common == self._candidate_prefix:
                self._candidate_hits += 1
            else:
                self._candidate_prefix = common
                self._candidate_hits = 1
            if self._candidate_hits >= self._stable_repeats:
                self._stable_prefix = common
        else:
            self._candidate_prefix = common
            self._candidate_hits = 1 if common else 0

        self._last_partial = normalized
        self._current_text = normalized
        # partial 乱跳时不让显示文本短于已稳定的前缀
        if self._stable_prefix and not self._current_text.startswith(self._stable_prefix):
            self._current_text = self._stable_prefix
        return self._current_text

    def ingest_final(self, text: str) -> str:
        normalized = _normalize_text(text)
        final_text = normalized or self._current_text or self._stable_prefix
        self.reset()
        return final_text
