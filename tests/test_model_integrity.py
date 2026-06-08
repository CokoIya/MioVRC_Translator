import hashlib
import shutil
import unittest
from pathlib import Path

from src.asr.model_manager import verify_model_integrity
from src.asr.model_registry import ASRRuntimeSpec
from src.asr.sensevoice_asr import SenseVoiceASR
from src.asr.sensevoice_model_manager import _resolve_spec


class ModelIntegrityTests(unittest.TestCase):
    def test_trusted_hash_is_required_when_present(self):
        root = Path("tests/.tmp_model_integrity")
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        try:
            model_file = root / "model.pt"
            model_file.write_bytes(b"trusted model")
            digest = hashlib.sha256(b"trusted model").hexdigest()
            spec = ASRRuntimeSpec(
                engine="test",
                label="Test",
                config_key="test",
                model_id="example/model",
                model_revision="rev",
                required_files=("model.pt",),
                required_file_sha256=(("model.pt", digest),),
            )

            self.assertTrue(verify_model_integrity(root, spec))
            model_file.write_bytes(b"tampered model")
            self.assertFalse(verify_model_integrity(root, spec))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_sensevoice_runtime_spec_keeps_trusted_hashes(self):
        self.assertTrue(SenseVoiceASR()._runtime_spec().required_file_sha256)
        self.assertTrue(_resolve_spec("iic/SenseVoiceSmall").required_file_sha256)

    def test_trusted_hashes_allow_unhashed_required_support_files(self):
        root = Path("tests/.tmp_model_integrity_support")
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        try:
            model_file = root / "model.pt"
            model_file.write_bytes(b"trusted model")
            (root / "support.txt").write_text("support", encoding="utf-8")
            digest = hashlib.sha256(b"trusted model").hexdigest()
            spec = ASRRuntimeSpec(
                engine="test",
                label="Test",
                config_key="test",
                model_id="example/model",
                model_revision="rev",
                required_files=("model.pt", "support.txt"),
                required_file_sha256=(("model.pt", digest),),
            )

            self.assertTrue(verify_model_integrity(root, spec))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
