from __future__ import annotations

import importlib.util
from importlib import metadata
import re
import sys
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_PYTHON = (3, 11)
REQUIRED_MODULES = (
    "PyInstaller",
    "PySide6",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PIL",
    "requests",
    "truststore",
    "charset_normalizer",
    "cryptography",
    "numpy",
    "sounddevice",
    "soundcard",
    "pyaudiowpatch",
    "webrtcvad",
    "funasr",
    "modelscope",
    "huggingface_hub",
    "tokenizers",
    "tqdm",
    "editdistance",
    "openai",
    "google.genai",
    "anthropic",
    "tiktoken",
    "pythonosc",
    "torch",
    "torchaudio",
    "whisper",
    "TTS",
    "sklearn",
    "edge_tts",
    "gtts",
    "pyttsx3",
    "style_bert_vits2",
    "cutlet",
    "fugashi",
    "unidic_lite",
    "mojimoji",
    "websockets",
    "aiohttp",
    "librosa",
    "scipy",
    "numba",
    "llvmlite",
    "av",
    "av.audio.resampler",
    "transformers",
    "sentencepiece",
    "google.protobuf",
    "jieba",
    "pypinyin",
    "cn2an",
    "g2p_en",
    "nltk",
    "defusedxml",
    "pyworld",
    "num2words",
    "ko_speech_tools",
)

_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")


def _iter_requirements(path: Path) -> list[Requirement]:
    requirements: list[Requirement] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = _INLINE_COMMENT_RE.sub("", raw_line).strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            raise RuntimeError(
                f"Invalid requirement in {path.name}:{line_number}: {line!r}"
            ) from exc
        if requirement.url:
            raise RuntimeError(
                f"Direct-URL requirements are not permitted in {path.name}:{line_number}"
            )
        requirements.append(requirement)
    return requirements


def _unsatisfied_requirements(path: Path = ROOT / "requirements.txt") -> list[str]:
    errors: list[str] = []
    for requirement in _iter_requirements(path):
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            errors.append(f"{requirement.name} is not installed (requires {requirement})")
            continue
        if requirement.specifier and not requirement.specifier.contains(
            installed,
            prereleases=True,
        ):
            errors.append(
                f"{requirement.name} {installed} does not satisfy {requirement.specifier}"
            )
    return errors


def _lock_mismatches(path: Path = ROOT / "requirements.lock.txt") -> list[str]:
    errors: list[str] = []
    for requirement in _iter_requirements(path):
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            errors.append(f"{requirement.name} is missing (locked to {requirement.specifier})")
            continue
        if requirement.specifier and not requirement.specifier.contains(
            installed,
            prereleases=True,
        ):
            errors.append(
                f"{requirement.name} {installed} does not match lock {requirement.specifier}"
            )
    return errors


def _unpinned_lock_dependencies(
    path: Path = ROOT / "requirements.lock.txt",
) -> list[str]:
    """Detect active transitive dependencies omitted from the release lock."""

    locked_requirements = [
        requirement
        for requirement in _iter_requirements(path)
        if requirement.marker is None or requirement.marker.evaluate()
    ]
    locked_names = {
        canonicalize_name(requirement.name) for requirement in locked_requirements
    }
    errors: set[str] = set()
    for requirement in locked_requirements:
        try:
            distribution = metadata.distribution(requirement.name)
        except metadata.PackageNotFoundError:
            continue
        for raw_dependency in distribution.requires or ():
            try:
                dependency = Requirement(raw_dependency)
            except InvalidRequirement as exc:
                errors.add(
                    f"{requirement.name} has invalid dependency metadata: {raw_dependency!r} ({exc})"
                )
                continue
            if dependency.marker is not None and not dependency.marker.evaluate():
                continue
            if canonicalize_name(dependency.name) not in locked_names:
                errors.add(
                    f"{dependency.name} required by {requirement.name} is not pinned"
                )
    return sorted(errors)


def _missing_modules() -> list[str]:
    missing: list[str] = []
    for module_name in REQUIRED_MODULES:
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            spec = None
        if spec is None:
            missing.append(module_name)
    return missing


def _runtime_import_errors() -> list[str]:
    errors: list[str] = []
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        Ed25519PrivateKey.from_private_bytes(bytes(32)).sign(b"release-environment-check")
    except Exception as exc:
        errors.append(f"cryptography Ed25519 runtime: {exc}")
    try:
        import cutlet as _cutlet  # noqa: F401
        import fugashi as _fugashi  # noqa: F401
        import unidic_lite as _unidic_lite  # noqa: F401
        import mojimoji as _mojimoji  # noqa: F401
        import sklearn as _sklearn  # noqa: F401
        from src.tts.xtts_engine import _ensure_transformers_xtts_exports

        _ensure_transformers_xtts_exports()
        from transformers import GenerationMixin as _GenerationMixin  # noqa: F401
        from transformers import GPT2Config as _GPT2Config  # noqa: F401
        from transformers import GPT2PreTrainedModel as _GPT2PreTrainedModel  # noqa: F401
        from TTS.api import TTS as _TTS  # noqa: F401
        import TTS.tts.layers.xtts.gpt_inference as _xtts_gpt_inference  # noqa: F401
    except Exception as exc:
        errors.append(f"XTTS Japanese runtime / TTS.api: {exc}")

    try:
        from transformers.pytorch_utils import isin_mps_friendly as _isin  # noqa: F401
    except Exception as exc:
        errors.append(f"transformers.pytorch_utils.isin_mps_friendly: {exc}")

    try:
        import av as _av  # noqa: F401
        from av.audio.resampler import AudioResampler as _AudioResampler  # noqa: F401
    except Exception as exc:
        errors.append(f"TTS audio runtime imports: {exc}")
    try:
        import ko_speech_tools as _ko_speech_tools  # noqa: F401
        import num2words as _num2words  # noqa: F401
    except Exception as exc:
        errors.append(f"XTTS multilingual text frontend imports: {exc}")
    return errors


def main() -> int:
    if sys.version_info[:2] != REQUIRED_PYTHON:
        version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        required = f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}"
        print(
            f"Release builds must use Python {required}; current interpreter is Python {version}.",
            file=sys.stderr,
        )
        print(
            "Create/activate the release venv first, then run: python -m pip install -r requirements.lock.txt",
            file=sys.stderr,
        )
        return 1

    try:
        requirement_errors = _unsatisfied_requirements()
        lock_errors = _lock_mismatches()
        lock_dependency_errors = _unpinned_lock_dependencies()
    except (OSError, RuntimeError) as exc:
        print(f"Release dependency metadata is invalid: {exc}", file=sys.stderr)
        return 1
    if requirement_errors or lock_errors or lock_dependency_errors:
        details = requirement_errors + lock_errors + lock_dependency_errors
        print(
            "Release environment has dependency version mismatches: "
            + "; ".join(details),
            file=sys.stderr,
        )
        print(
            "Install the locked release dependencies: python -m pip install -r requirements.lock.txt",
            file=sys.stderr,
        )
        return 1

    missing = _missing_modules()
    if missing:
        print(
            "Release environment is missing required modules: " + ", ".join(missing),
            file=sys.stderr,
        )
        print(
            "Install the locked release dependencies: python -m pip install -r requirements.lock.txt",
            file=sys.stderr,
        )
        return 1

    import_errors = _runtime_import_errors()
    if import_errors:
        print(
            "Release environment has broken runtime imports: " + "; ".join(import_errors),
            file=sys.stderr,
        )
        print(
            "Install the locked release dependencies: python -m pip install -r requirements.lock.txt",
            file=sys.stderr,
        )
        return 1

    import torch
    from packaging.version import Version

    torch_cuda = torch.version.cuda or ""
    if torch_cuda:
        print(
            f"This Python environment uses CUDA PyTorch ({torch_cuda}). Install CPU-only torch before building.",
            file=sys.stderr,
        )
        return 1
    torch_version = Version(torch.__version__.split("+", 1)[0])
    if torch_version >= Version("2.9"):
        print(
            "Release builds must use CPU PyTorch < 2.9 for Coqui XTTS on Windows; "
            f"current torch is {torch.__version__}.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Release environment OK: Python {sys.version.split()[0]}, CPU-only PyTorch {torch.__version__}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
