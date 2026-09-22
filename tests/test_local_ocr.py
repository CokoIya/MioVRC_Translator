"""The bundled recognizer: several scripts per frame, best reading per line."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from src.core import local_ocr
from src.core.local_ocr import LocalOcr, _Reading


class _FakeEngine:
    """Stands in for RapidOCR: fixed boxes, per-model readings."""

    readings_by_model: dict[str, list[tuple[str, float]]] = {}
    instances: list["_FakeEngine"] = []

    def __init__(self, rec_model_path: str = "ch"):
        self.model = "ja" if "japan" in rec_model_path else ("ko" if "korean" in rec_model_path else "ch")
        self.use_cls = False
        self.rec_calls: list[int] = []
        _FakeEngine.instances.append(self)

    def preprocess(self, image):
        return image, 1.0, 1.0

    def maybe_add_letterbox(self, image, op_record):
        return image, op_record

    def auto_text_det(self, image):
        boxes = [
            np.array([[10, 10], [110, 10], [110, 40], [10, 40]], dtype=np.float32),
            np.array([[10, 60], [210, 60], [210, 90], [10, 90]], dtype=np.float32),
        ]
        return boxes, 0.0

    def get_crop_img_list(self, image, boxes):
        return [np.zeros((30, 100, 3), dtype=np.uint8) for _ in boxes]

    def text_rec(self, crops):
        self.rec_calls.append(len(crops))
        readings = self.readings_by_model[self.model]
        return readings[: len(crops)], 0.0

    def _get_origin_points(self, boxes, op_record, height, width):
        return boxes


@pytest.fixture
def fake_rapidocr(monkeypatch, tmp_path):
    _FakeEngine.instances = []
    module = types.ModuleType("rapidocr_onnxruntime")
    module.RapidOCR = _FakeEngine
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", module)
    monkeypatch.setitem(sys.modules, "onnxruntime", types.ModuleType("onnxruntime"))
    for name in local_ocr.EXTRA_RECOGNIZERS.values():
        (tmp_path / name).write_bytes(b"model")
    monkeypatch.setattr(local_ocr, "models_directory", lambda: str(tmp_path))
    return module


def _png() -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    image = QImage(300, 120, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 255, 255))
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(storage.data())


class TestChoice:
    def test_the_most_confident_reading_wins(self):
        best = LocalOcr._choose(
            [_Reading("《喜你", 0.62, "zh"), _Reading("恭喜你", 0.97, "ja")], "zh"
        )

        assert best.text == "恭喜你"

    def test_a_tie_goes_to_the_language_being_listened_to(self):
        best = LocalOcr._choose(
            [_Reading("設定", 0.90, "zh"), _Reading("設定", 0.90, "ja")], "ja"
        )

        assert best.language == "ja"

    def test_empty_readings_are_nothing(self):
        assert LocalOcr._choose([], "ja") is None
        assert LocalOcr._choose([_Reading("  ", 0.99, "zh")], "zh") is None


class TestRecognition:
    def test_each_line_gets_the_best_of_the_recognizers(self, fake_rapidocr, qapp):
        _FakeEngine.readings_by_model = {
            "ch": [("恭喜你", 0.95), ("Ｐラーム", 0.40)],
            "ja": [("恭喜伱", 0.70), ("アラーム", 0.93)],
            "ko": [("", 0.10), ("", 0.10)],
        }
        ocr = LocalOcr(languages=("zh", "ja", "ko"))

        result = ocr.recognize(_png(), language_hint="ja")

        assert result.ok
        assert [line.text for line in result.lines] == ["恭喜你", "アラーム"]
        first = result.lines[0]
        assert (first.left, first.top, first.width, first.height) == (10, 10, 100, 30)

    def test_confident_chinese_lines_skip_the_other_recognizers(self, fake_rapidocr, qapp):
        _FakeEngine.readings_by_model = {
            "ch": [("自动出征", 0.99), ("升级", 0.98)],
            "ja": [("x", 0.1), ("x", 0.1)],
            "ko": [("x", 0.1), ("x", 0.1)],
        }
        ocr = LocalOcr(languages=("zh", "ja", "ko"))

        ocr.recognize(_png(), language_hint="zh")

        extras = [engine for engine in _FakeEngine.instances if engine.model != "ch"]
        assert all(engine.rec_calls == [] for engine in extras)

    def test_the_listened_script_reads_every_line(self, fake_rapidocr, qapp):
        _FakeEngine.readings_by_model = {
            "ch": [("自动出征", 0.99), ("升级", 0.98)],
            "ja": [("自動出征", 0.99), ("昇級", 0.99)],
            "ko": [("x", 0.1), ("x", 0.1)],
        }
        ocr = LocalOcr(languages=("zh", "ja", "ko"))

        ocr.recognize(_png(), language_hint="ja")

        japanese = next(engine for engine in _FakeEngine.instances if engine.model == "ja")
        assert japanese.rec_calls == [2]

    def test_noise_below_the_score_floor_is_dropped(self, fake_rapidocr, qapp):
        _FakeEngine.readings_by_model = {
            "ch": [("恭喜你", 0.95), ("|||", 0.30)],
            "ja": [("恭喜你", 0.50), ("｜｜", 0.35)],
            "ko": [("", 0.0), ("", 0.0)],
        }
        ocr = LocalOcr()

        result = ocr.recognize(_png(), language_hint="zh")

        assert [line.text for line in result.lines] == ["恭喜你"]

    def test_a_tilted_box_keeps_its_own_height_and_tilt(self, fake_rapidocr, qapp, monkeypatch):
        import math

        # A 200 px line descending six degrees to the right, 30 px tall: the
        # upright box around it would be 51 px tall.
        c, s = math.cos(math.radians(6)), math.sin(math.radians(6))
        tilted = np.array(
            [
                [10, 10],
                [10 + 200 * c, 10 + 200 * s],
                [10 + 200 * c - 30 * s, 10 + 200 * s + 30 * c],
                [10 - 30 * s, 10 + 30 * c],
            ],
            dtype=np.float32,
        )
        monkeypatch.setattr(_FakeEngine, "auto_text_det", lambda self, image: ([tilted], 0.0))
        _FakeEngine.readings_by_model = {"ch": [("恭喜你", 0.95)], "ja": [("x", 0.1)], "ko": [("x", 0.1)]}
        ocr = LocalOcr()

        line = ocr.recognize(_png(), language_hint="zh").lines[0]

        assert (line.left, line.top) == (10, 10)
        assert line.width == pytest.approx(200, abs=0.01)
        assert line.height == pytest.approx(30, abs=0.01)
        assert line.angle == pytest.approx(6, abs=0.01)

    def test_the_gpu_is_used_when_directml_is_there_and_wanted(self, fake_rapidocr, monkeypatch, qapp):
        built: list[dict] = []

        class _GpuEngine(_FakeEngine):
            def __init__(self, rec_model_path: str = "ch", **kwargs):
                built.append(dict(kwargs))
                super().__init__(rec_model_path)

        fake_rapidocr.RapidOCR = _GpuEngine
        monkeypatch.setattr(local_ocr, "directml_available", lambda: True)
        _FakeEngine.readings_by_model = {"ch": [("你好", 0.9), ("x", 0.1)], "ja": [("", 0.0), ("", 0.0)], "ko": [("", 0.0), ("", 0.0)]}

        ocr = LocalOcr(gpu=True)
        assert ocr.warm_up()

        assert ocr.gpu_active is True
        assert built[0] == {"det_use_dml": True, "cls_use_dml": True, "rec_use_dml": True}
        # The extra recognizers ride on the same provider.
        assert all(entry.get("rec_use_dml") for entry in built[1:])

    def test_without_directml_or_when_declined_the_cpu_is_used(self, fake_rapidocr, monkeypatch, qapp):
        built: list[dict] = []

        class _Engine(_FakeEngine):
            def __init__(self, rec_model_path: str = "ch", **kwargs):
                built.append(dict(kwargs))
                super().__init__(rec_model_path)

        fake_rapidocr.RapidOCR = _Engine
        monkeypatch.setattr(local_ocr, "directml_available", lambda: False)
        assert LocalOcr(gpu=True).warm_up()
        assert built and all(entry == {} for entry in built)

        built.clear()
        monkeypatch.setattr(local_ocr, "directml_available", lambda: True)
        declined = LocalOcr(gpu=False)
        assert declined.warm_up()
        assert declined.gpu_active is False
        assert all(entry == {} for entry in built)

    def test_a_gpu_that_refuses_falls_back_to_the_cpu(self, fake_rapidocr, monkeypatch, qapp):
        built: list[dict] = []

        class _Picky(_FakeEngine):
            def __init__(self, rec_model_path: str = "ch", **kwargs):
                built.append(dict(kwargs))
                if kwargs.get("det_use_dml"):
                    raise RuntimeError("DML device lost")
                super().__init__(rec_model_path)

        fake_rapidocr.RapidOCR = _Picky
        monkeypatch.setattr(local_ocr, "directml_available", lambda: True)

        ocr = LocalOcr(gpu=True)

        assert ocr.warm_up() is True
        assert ocr.gpu_active is False
        assert built[0].get("det_use_dml") is True and built[1] == {}

    def test_a_missing_package_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
        ocr = LocalOcr()

        result = ocr.recognize(b"png")

        assert result.error.startswith("local_ocr_unavailable")
        assert local_ocr.local_ocr_available() is False
