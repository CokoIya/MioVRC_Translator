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
    "anthropic",
    "tiktoken",
    "pythonosc",
    "torch",
    "torchaudio",
    "whisper",
    "sklearn",
    "websockets",
    "style_bert_vits2",
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
)

_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")


# Pins that requirements.txt cannot reach because nothing imports them at
# runtime. Each one has to earn its place here, because the alternative is how
# the bundle silently kept shipping engines that had already been deleted.
BUILD_ONLY_LOCK_PINS = {
    # PyInstaller and the packages it needs to freeze the app.
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
    "pefile",
    "pywin32-ctypes",
    # psutil declares this on Windows; the marker is easy to lose in a graph
    # walk, and dropping a Win32 binding from a Windows-only app is not worth
    # the few megabytes.
    "pywin32",
    # modelscope resolves an ONNX runtime for some model formats. It is not
    # imported directly, so a graph walk cannot see the need for it.
    "onnxruntime",
    "flatbuffers",
}

# Dependency markers are evaluated for the machine the release is built on.
_LOCK_MARKER_ENVIRONMENT = {
    "python_version": "3.11",
    "python_full_version": "3.11.9",
    "sys_platform": "win32",
    "platform_system": "Windows",
    "platform_machine": "AMD64",
    "os_name": "nt",
    "implementation_name": "cpython",
    "extra": "",
}


def _canonical_project_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = _INLINE_COMMENT_RE.sub("", raw_line.split("#", 1)[0]).strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[; ]", line, 1)[0].strip()
        if name:
            names.add(_canonical_project_name(name))
    return names


def _installed_dependencies(dist) -> set[str]:
    """Return the runtime dependencies a distribution declares here."""

    from packaging.markers import Marker

    resolved: set[str] = set()
    for spec in dist.requires or []:
        name = re.split(r"[<>=!~\[; ]", spec, 1)[0].strip()
        if not name:
            continue
        if ";" in spec:
            marker_text = spec.split(";", 1)[1].strip()
            if "extra ==" in marker_text or "extra==" in marker_text:
                # Optional extras are not installed unless something asks.
                continue
            try:
                if not Marker(marker_text).evaluate(_LOCK_MARKER_ENVIRONMENT):
                    continue
            except Exception:
                pass
        resolved.add(_canonical_project_name(name))
    return resolved


def _orphaned_lock_pins(
    requirements_path: Path = ROOT / "requirements.txt",
    lock_path: Path = ROOT / "requirements.lock.txt",
) -> list[str]:
    """Report locked packages nothing in the app can reach any more.

    Deleting a feature leaves its dependency pinned, and the pin keeps the
    package installed, which keeps PyInstaller collecting it into the bundle.
    """

    from importlib import metadata

    installed = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            installed[_canonical_project_name(name)] = dist

    reachable: set[str] = set()
    queue = [
        name for name in _requirement_names(requirements_path) if name in installed
    ]
    while queue:
        name = queue.pop()
        if name in reachable:
            continue
        reachable.add(name)
        for dependency in _installed_dependencies(installed[name]):
            if dependency in installed and dependency not in reachable:
                queue.append(dependency)

    orphans = sorted(
        _requirement_names(lock_path) - reachable - BUILD_ONLY_LOCK_PINS
    )
    return [
        f"{name} is pinned in requirements.lock.txt but nothing in "
        "requirements.txt depends on it"
        for name in orphans
    ]


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
        # librosa (via funasr/SenseVoice and transformers) requires sklearn, so a
        # bundle without it fails only once speech recognition starts.
        import sklearn as _sklearn  # noqa: F401
        from transformers import GenerationMixin as _GenerationMixin  # noqa: F401
        from transformers.models.deberta_v2 import (  # noqa: F401
            modeling_deberta_v2 as _deberta_modeling,
            tokenization_deberta_v2 as _deberta_tokenization,
        )
    except Exception as exc:
        errors.append(f"shared model runtime imports: {exc}")

    try:
        import av as _av  # noqa: F401
        from av.audio.resampler import AudioResampler as _AudioResampler  # noqa: F401
    except Exception as exc:
        errors.append(f"audio decode runtime imports: {exc}")
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
        lock_dependency_errors += _orphaned_lock_pins()
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
            "Release builds must use the pinned CPU PyTorch < 2.9 on Windows; "
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
