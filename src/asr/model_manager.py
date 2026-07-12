from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import secrets
import shutil
import stat
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import ExitStack, contextmanager

from src.asr.model_registry import (
    ASRRuntimeSpec,
    SENSEVOICE_DEFAULT_MODEL,
    SENSEVOICE_DEFAULT_REVISION,
)
from src.asr.hf_model_downloader import _is_safe_model_download_url
from src.utils.app_paths import resource_base_dirs, writable_app_dir
from src.utils.secure_http import open_validated_requests_response

_DOWNLOAD_LOCK = threading.Lock()
_FILE_HASH_CACHE_LOCK = threading.Lock()
_FILE_HASH_CACHE_MAX_ENTRIES = 256
_FILE_HASH_CACHE: OrderedDict[tuple[str, int, int, int, int, int], str] = (
    OrderedDict()
)
_MODEL_METADATA_FILENAME = ".mio-model.json"
_DOWNLOAD_ATTEMPTS = 3
_MODEL_CONFIG_FILENAMES = ("configuration.json", "config.yaml")
_MAX_CONFIG_BYTES = 2 * 1024 * 1024
_MAX_MODEL_FILE_BYTES = 2 * 1024 * 1024 * 1024
_URL_RE = re.compile(r"(?i)(?:https?|ftp|file)://")
_PYTHON_REFERENCE_RE = re.compile(
    r"(?ix)(?:^|\s)(?:from\s+[a-z_]\w*(?:\.[a-z_]\w*)*\s+import\s+|"
    r"import\s+[a-z_]\w*(?:\.[a-z_]\w*)*|__import__\s*\(|python\s*:|py\s*:)"
    r"|(?:^|[/\\])[^/\\]+\.py(?:$|[:#?])"
    r"|^[a-z_]\w*(?:\.[a-z_]\w*)+:[a-z_]\w*$"
)
logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, object]], None]


def _model_slug(model_id: str) -> str:
    return model_id.replace("/", "--")


def bundled_model_dirs(spec: ASRRuntimeSpec) -> list[pathlib.Path]:
    slug = _model_slug(spec.model_id)
    dirs: list[pathlib.Path] = []
    for base in resource_base_dirs():
        for bundled_name in spec.bundled_dir_names:
            dirs.append(base / "models" / bundled_name)
        dirs.append(base / "models" / slug)
    return dirs


def model_dir(spec: ASRRuntimeSpec) -> pathlib.Path:
    return writable_app_dir() / "runtime_models" / _model_slug(spec.model_id)


def cache_dir() -> pathlib.Path:
    override = os.environ.get("MIO_TRANSLATOR_MODELSCOPE_CACHE_DIR", "").strip()
    if not override:
        override = os.environ.get("MIO_TRANSLATOR_SENSEVOICE_CACHE_DIR", "").strip()
    if override:
        return pathlib.Path(override).expanduser()
    return writable_app_dir() / "runtime_cache" / "modelscope"


def _metadata_path(path: pathlib.Path) -> pathlib.Path:
    return path / _MODEL_METADATA_FILENAME


@contextmanager
def _ensure_download_stdio():
    # Packaged GUI builds may run without a console, leaving stdout/stderr as None.
    # ModelScope's snapshot_download uses plain print() and tqdm, both of which
    # expect writable text streams.
    with ExitStack() as stack:
        restored: list[tuple[str, object]] = []
        for attr in ("stdout", "stderr"):
            stream = getattr(sys, attr, None)
            if stream is not None and hasattr(stream, "write"):
                continue
            sink = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
            restored.append((attr, stream))
            setattr(sys, attr, sink)
        try:
            yield
        finally:
            for attr, original in restored:
                setattr(sys, attr, original)


def _load_model_metadata(path: pathlib.Path) -> dict[str, object]:
    metadata_path = _metadata_path(path)
    if not _is_regular_nonlink_file(metadata_path, root=path):
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _file_identity(path: pathlib.Path) -> tuple[str, int, int, int, int, int]:
    info = path.stat()
    return (
        str(path.resolve(strict=True)),
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _sha256_file(path: pathlib.Path) -> str:
    cache_key = _file_identity(path)
    with _FILE_HASH_CACHE_LOCK:
        cached = _FILE_HASH_CACHE.get(cache_key)
        if cached is not None:
            _FILE_HASH_CACHE.move_to_end(cache_key)
    if cached is not None:
        return cached

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    if _file_identity(path) != cache_key:
        raise OSError(f"Model file changed during integrity verification: {path.name}")
    digest = hasher.hexdigest()
    with _FILE_HASH_CACHE_LOCK:
        _FILE_HASH_CACHE[cache_key] = digest
        _FILE_HASH_CACHE.move_to_end(cache_key)
        while len(_FILE_HASH_CACHE) > _FILE_HASH_CACHE_MAX_ENTRIES:
            _FILE_HASH_CACHE.popitem(last=False)
    return digest


def _is_reparse_point(stat_result: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and (getattr(stat_result, "st_file_attributes", 0) & flag))


def _is_safe_directory_stat(stat_result: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(stat_result.st_mode)
        and not stat.S_ISLNK(stat_result.st_mode)
        and not _is_reparse_point(stat_result)
    )


def _is_safe_directory(path: pathlib.Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return _is_safe_directory_stat(info)


def _lexical_absolute_path(path: str | pathlib.Path) -> pathlib.Path:
    """Return an absolute path without resolving links or reparse points."""

    expanded = pathlib.Path(path).expanduser()
    absolute = pathlib.Path(os.path.abspath(os.fspath(expanded)))
    if not absolute.is_absolute() or not absolute.anchor:
        raise RuntimeError(f"ASR model path must be absolute: {path}")
    return absolute


def _lstat_optional(path: pathlib.Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"Unable to inspect ASR model path: {path}") from exc


def _directory_chain(path: pathlib.Path):
    anchor = pathlib.Path(path.anchor)
    current = anchor
    yield current
    for part in path.parts[len(anchor.parts) :]:
        current = current / part
        yield current


def _ensure_secure_directory_tree(
    path: str | pathlib.Path,
    *,
    create: bool,
) -> pathlib.Path:
    """Validate each path component and optionally create missing directories."""

    normalized = _lexical_absolute_path(path)
    anchor = pathlib.Path(normalized.anchor)
    for current in _directory_chain(normalized):
        info = _lstat_optional(current)
        if info is None:
            if not create or current == anchor:
                raise RuntimeError(f"ASR model directory does not exist: {current}")
            try:
                current.mkdir(mode=0o700, parents=False, exist_ok=False)
            except FileExistsError:
                # Another process may have created the component. It still has to
                # pass the same non-link/non-reparse validation below.
                pass
            except OSError as exc:
                raise RuntimeError(f"Unable to create ASR model directory: {current}") from exc
            info = _lstat_optional(current)
        if info is None or not _is_safe_directory_stat(info):
            raise RuntimeError(f"Unsafe ASR model directory: {current}")
    return normalized


def _prepare_secure_target_parent(target_dir: str | pathlib.Path) -> pathlib.Path:
    target = _lexical_absolute_path(target_dir)
    if target == pathlib.Path(target.anchor):
        raise RuntimeError(f"Refusing to use a filesystem root as an ASR model target: {target}")
    _ensure_secure_directory_tree(target.parent, create=True)
    target_info = _lstat_optional(target)
    if target_info is not None and not _is_safe_directory_stat(target_info):
        raise RuntimeError(f"Unsafe ASR model target directory: {target}")
    return target


def _prepare_secure_directory(path: str | pathlib.Path) -> pathlib.Path:
    return _ensure_secure_directory_tree(path, create=True)


def _directory_identity(path: pathlib.Path) -> tuple[int, int]:
    info = _lstat_optional(path)
    if info is None or not _is_safe_directory_stat(info):
        raise RuntimeError(f"Unsafe ASR model directory: {path}")
    return (int(getattr(info, "st_dev", 0)), int(getattr(info, "st_ino", 0)))


def _owned_sibling_prefix(target: pathlib.Path, purpose: str) -> str:
    target_digest = hashlib.sha256(os.fsencode(target.name)).hexdigest()[:12]
    return f".mio-{purpose}-{target_digest}-"


def _unused_owned_sibling_path(target: pathlib.Path, purpose: str) -> pathlib.Path:
    prefix = _owned_sibling_prefix(target, purpose)
    for _ in range(128):
        candidate = target.parent / f"{prefix}{secrets.token_hex(16)}"
        if _lstat_optional(candidate) is None:
            return candidate
    raise RuntimeError(f"Unable to reserve a private {purpose} directory for {target}")


def _create_owned_sibling_directory(
    target: pathlib.Path,
    purpose: str,
) -> tuple[pathlib.Path, tuple[int, int]]:
    _ensure_secure_directory_tree(target.parent, create=False)
    for _ in range(128):
        candidate = _unused_owned_sibling_path(target, purpose)
        try:
            candidate.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError:
            continue
        except OSError as exc:
            raise RuntimeError(f"Unable to create ASR model {purpose} directory") from exc
        return candidate, _directory_identity(candidate)
    raise RuntimeError(f"Unable to create a private {purpose} directory for {target}")


def _remove_owned_directory(
    path: pathlib.Path,
    expected_identity: tuple[int, int],
) -> None:
    normalized = _lexical_absolute_path(path)
    _ensure_secure_directory_tree(normalized.parent, create=False)
    info = _lstat_optional(normalized)
    if info is None:
        return
    if (
        not _is_safe_directory_stat(info)
        or _directory_identity(normalized) != expected_identity
    ):
        raise RuntimeError(f"Refusing to remove an unowned ASR model directory: {normalized}")
    # The root has been validated by path and filesystem identity. shutil.rmtree
    # does not traverse directory symlinks; it removes them as links.
    shutil.rmtree(normalized)


def _cleanup_owned_directory(
    path: pathlib.Path,
    expected_identity: tuple[int, int],
    *,
    purpose: str,
) -> None:
    try:
        _remove_owned_directory(path, expected_identity)
    except Exception:
        logger.warning(
            "Failed to remove ASR model %s directory %s",
            purpose,
            path,
            exc_info=True,
        )


def _install_staged_directory(
    staging_dir: pathlib.Path,
    staging_identity: tuple[int, int],
    target_dir: pathlib.Path,
) -> None:
    target_dir = _prepare_secure_target_parent(target_dir)
    if _directory_identity(staging_dir) != staging_identity:
        raise RuntimeError(f"ASR model staging directory changed unexpectedly: {staging_dir}")

    target_info = _lstat_optional(target_dir)
    if target_info is None:
        try:
            os.rename(staging_dir, target_dir)
        except OSError as exc:
            raise RuntimeError(f"Unable to install ASR model into {target_dir}") from exc
        if _directory_identity(target_dir) != staging_identity:
            raise RuntimeError(f"Installed ASR model directory changed unexpectedly: {target_dir}")
        return
    if not _is_safe_directory_stat(target_info):
        raise RuntimeError(f"Unsafe ASR model target directory: {target_dir}")

    backup_dir = _unused_owned_sibling_path(target_dir, "backup")
    try:
        os.rename(target_dir, backup_dir)
    except OSError as exc:
        raise RuntimeError(f"Unable to reserve the existing ASR model installation") from exc

    try:
        backup_identity = _directory_identity(backup_dir)
    except Exception as exc:
        try:
            if _lstat_optional(target_dir) is None:
                os.rename(backup_dir, target_dir)
        except OSError:
            logger.critical(
                "Failed to restore ASR model target after unsafe backup detection: %s",
                target_dir,
                exc_info=True,
            )
        raise RuntimeError("Existing ASR model backup could not be validated") from exc

    installed = False
    try:
        if _lstat_optional(target_dir) is not None:
            raise RuntimeError(f"ASR model target reappeared during installation: {target_dir}")
        os.rename(staging_dir, target_dir)
        installed = True
        if _directory_identity(target_dir) != staging_identity:
            raise RuntimeError(f"Installed ASR model directory changed unexpectedly: {target_dir}")
    except Exception as install_exc:
        rollback_errors: list[str] = []
        if installed:
            try:
                if (
                    _lstat_optional(staging_dir) is None
                    and _directory_identity(target_dir) == staging_identity
                ):
                    os.rename(target_dir, staging_dir)
                else:
                    rollback_errors.append("installed target ownership changed")
            except Exception as exc:
                rollback_errors.append(f"failed to move staged model back: {exc}")
        try:
            if _lstat_optional(target_dir) is None:
                os.rename(backup_dir, target_dir)
            else:
                rollback_errors.append("target path is occupied")
        except Exception as exc:
            rollback_errors.append(f"failed to restore original target: {exc}")

        detail = "; ".join(rollback_errors)
        if detail:
            raise RuntimeError(
                f"ASR model installation failed and rollback was incomplete: {detail}. "
                f"Original installation backup: {backup_dir}"
            ) from install_exc
        raise RuntimeError(
            "ASR model installation failed; the original installation was restored"
        ) from install_exc

    _cleanup_owned_directory(
        backup_dir,
        backup_identity,
        purpose="backup",
    )


def _prepare_secure_descendant_directory(
    root: pathlib.Path,
    directory: pathlib.Path,
) -> pathlib.Path:
    try:
        relative = directory.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"ASR model path escapes its target directory: {directory}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        info = _lstat_optional(current)
        if info is None:
            try:
                current.mkdir(mode=0o700, parents=False, exist_ok=False)
            except FileExistsError:
                pass
            except OSError as exc:
                raise RuntimeError(f"Unable to create ASR model directory: {current}") from exc
            info = _lstat_optional(current)
        if info is None or not _is_safe_directory_stat(info):
            raise RuntimeError(f"Unsafe ASR model directory: {current}")
    return directory


def _safe_required_file_path(root: pathlib.Path, filename: str) -> pathlib.Path:
    normalized = str(filename or "").strip().replace("\\", "/")
    pure = pathlib.PurePosixPath(normalized)
    if (
        not normalized
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"Unsafe model filename: {filename!r}")
    return root.joinpath(*pure.parts)


def _is_regular_nonlink_file(path: pathlib.Path, *, root: pathlib.Path) -> bool:
    if not _is_safe_directory(root):
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if not _is_safe_directory(current):
            return False
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and not _is_reparse_point(info)
        and int(getattr(info, "st_nlink", 1) or 1) == 1
    )


def _required_file_hashes(path: pathlib.Path, spec: ASRRuntimeSpec) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for filename in spec.required_files:
        try:
            file_path = _safe_required_file_path(path, filename)
        except ValueError:
            continue
        if _is_regular_nonlink_file(file_path, root=path):
            hashes[filename] = _sha256_file(file_path)
    return hashes


def _trusted_hashes_match(path: pathlib.Path, spec: ASRRuntimeSpec) -> bool:
    trusted_pairs = tuple(spec.required_file_sha256 or ())
    if not trusted_pairs:
        return True

    trusted_hashes = dict(trusted_pairs)
    required_files = tuple(spec.required_files)
    if (
        len(trusted_hashes) != len(trusted_pairs)
        or len(set(required_files)) != len(required_files)
        or set(trusted_hashes) != set(required_files)
    ):
        return False

    for filename in required_files:
        expected = str(trusted_hashes.get(filename) or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            return False
        try:
            file_path = _safe_required_file_path(path, filename)
        except ValueError:
            return False
        if not _is_regular_nonlink_file(file_path, root=path):
            return False
        if _sha256_file(file_path) != expected:
            return False
    return True


def _write_model_metadata(
    path: pathlib.Path,
    *,
    spec: ASRRuntimeSpec,
    requested_revision: str,
    resolved_revision: str,
) -> None:
    payload = {
        "engine": spec.engine,
        "label": spec.label,
        "model_id": spec.model_id,
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
        "required_file_sha256": _required_file_hashes(path, spec),
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    root = _ensure_secure_directory_tree(path, create=False)
    metadata_path = _metadata_path(root)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    temporary_path: pathlib.Path | None = None
    descriptor: int | None = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(128):
        candidate = root / f".{_MODEL_METADATA_FILENAME}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(candidate, flags, 0o600)
        except FileExistsError:
            continue
        temporary_path = candidate
        break
    if temporary_path is None or descriptor is None:
        raise RuntimeError(f"Unable to create model metadata in {root}")

    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, metadata_path)
        temporary_path = None
        if not _is_regular_nonlink_file(metadata_path, root=root):
            raise RuntimeError(f"Model metadata is not a regular file: {metadata_path}")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                info = _lstat_optional(temporary_path)
                if info is not None and stat.S_ISREG(info.st_mode) and not _is_reparse_point(info):
                    temporary_path.unlink()
            except OSError:
                logger.warning(
                    "Failed to remove temporary model metadata %s",
                    temporary_path,
                    exc_info=True,
                )


def _is_complete_model_dir(path: pathlib.Path, spec: ASRRuntimeSpec) -> bool:
    if not _is_safe_directory(path):
        return False
    try:
        required_paths = [
            _safe_required_file_path(path, filename) for filename in spec.required_files
        ]
    except ValueError:
        return False
    if not all(
        _is_regular_nonlink_file(file_path, root=path)
        for file_path in required_paths
    ):
        return False
    if any(name in spec.required_files for name in _MODEL_CONFIG_FILENAMES):
        return True
    return any(
        _is_regular_nonlink_file(path / name, root=path)
        for name in _MODEL_CONFIG_FILENAMES
    )


def _directive_key_is_unsafe(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    parts = {part for part in normalized.split("_") if part}
    if parts & {"import", "imports", "module", "modules", "script", "scripts"}:
        return True
    if normalized in {"auto_map", "class_path", "python_class", "code_revision"}:
        return True
    return ({"remote", "code"} <= parts) or ({"custom", "code"} <= parts)


def _string_value_is_unsafe(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if _URL_RE.search(text) or _PYTHON_REFERENCE_RE.search(text):
        return True
    if pathlib.PurePosixPath(text).is_absolute() or pathlib.PureWindowsPath(text).is_absolute():
        return True
    path_parts = [part for part in re.split(r"[/\\]+", text) if part]
    return ".." in path_parts


def _validate_config_value(value: object, *, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [100_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 64:
        raise ValueError("Model configuration is too deeply nested or too large")
    if isinstance(value, dict):
        for key, child in value.items():
            if _directive_key_is_unsafe(key):
                raise ValueError(f"Unsafe model configuration directive: {key!r}")
            if _string_value_is_unsafe(str(key)):
                raise ValueError(f"Unsafe model configuration key: {key!r}")
            _validate_config_value(child, depth=depth + 1, budget=budget)
        return
    if isinstance(value, (list, tuple, set)):
        for child in value:
            _validate_config_value(child, depth=depth + 1, budget=budget)
        return
    if isinstance(value, str) and _string_value_is_unsafe(value):
        raise ValueError(f"Unsafe model configuration value: {value!r}")


def _read_small_text_file(path: pathlib.Path, *, root: pathlib.Path) -> str:
    if not _is_regular_nonlink_file(path, root=root):
        raise ValueError(f"Model configuration is not a regular file: {path.name}")
    size = path.stat().st_size
    if size > _MAX_CONFIG_BYTES:
        raise ValueError(f"Model configuration is too large: {path.name}")
    return path.read_text(encoding="utf-8")


def validate_model_configuration(path: str | pathlib.Path) -> None:
    root = pathlib.Path(path)
    configuration_json = root / "configuration.json"
    config_yaml = root / "config.yaml"

    if configuration_json.exists():
        raw_json = _read_small_text_file(configuration_json, root=root)
        parsed_json = json.loads(raw_json)
        if not isinstance(parsed_json, dict):
            raise ValueError("configuration.json must contain a JSON object")
        _validate_config_value(parsed_json)

    if config_yaml.exists():
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to validate ASR model configuration") from exc
        raw_yaml = _read_small_text_file(config_yaml, root=root)
        parsed_yaml = yaml.safe_load(raw_yaml)
        if not isinstance(parsed_yaml, dict):
            raise ValueError("config.yaml must contain a YAML mapping")
        _validate_config_value(parsed_yaml)


def verify_model_integrity(path: str | pathlib.Path, spec: ASRRuntimeSpec) -> bool:
    root = pathlib.Path(path)
    if not _is_safe_directory(root):
        return False
    try:
        for filename in spec.required_files:
            file_path = _safe_required_file_path(root, filename)
            if not _is_regular_nonlink_file(file_path, root=root):
                return False
        if not _trusted_hashes_match(root, spec):
            return False
        validate_model_configuration(root)
        return True
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError):
        logger.debug("ASR model integrity validation failed for %s", root, exc_info=True)
        return False


def _downloaded_model_path(spec: ASRRuntimeSpec) -> pathlib.Path | None:
    downloaded = model_dir(spec)
    if _is_complete_model_dir(downloaded, spec) and verify_model_integrity(downloaded, spec):
        return downloaded
    return None


def _bundled_model_path(spec: ASRRuntimeSpec) -> pathlib.Path | None:
    for bundled_dir in bundled_model_dirs(spec):
        if _is_complete_model_dir(bundled_dir, spec) and verify_model_integrity(
            bundled_dir, spec
        ):
            return bundled_dir
    return None


def existing_model_path(spec: ASRRuntimeSpec) -> pathlib.Path | None:
    downloaded = _downloaded_model_path(spec)
    if downloaded is not None:
        return downloaded
    return _bundled_model_path(spec)


def model_exists(spec: ASRRuntimeSpec) -> bool:
    return existing_model_path(spec) is not None


def resolve_model_path(spec: ASRRuntimeSpec) -> str:
    path = existing_model_path(spec)
    if path is not None:
        return str(path)
    raise FileNotFoundError(
        f"{spec.label} model not found. It will be downloaded to: {model_dir(spec)}"
    )


def get_local_model_status(spec: ASRRuntimeSpec) -> dict[str, object]:
    downloaded = _downloaded_model_path(spec)
    if downloaded is not None:
        metadata = _load_model_metadata(downloaded)
        return {
            "engine": spec.engine,
            "label": spec.label,
            "model_id": spec.model_id,
            "requested_revision": spec.model_revision,
            "installed": True,
            "source": "downloaded",
            "path": str(downloaded),
            "local_revision": str(metadata.get("resolved_revision", "")).strip(),
            "metadata": metadata,
        }

    bundled = _bundled_model_path(spec)
    if bundled is not None:
        return {
            "engine": spec.engine,
            "label": spec.label,
            "model_id": spec.model_id,
            "requested_revision": spec.model_revision,
            "installed": True,
            "source": "bundled",
            "path": str(bundled),
            "local_revision": "",
            "metadata": {},
        }

    return {
        "engine": spec.engine,
        "label": spec.label,
        "model_id": spec.model_id,
        "requested_revision": spec.model_revision,
        "installed": False,
        "source": "missing",
        "path": str(model_dir(spec)),
        "local_revision": "",
        "metadata": {},
    }


def _emit_progress(
    progress_callback: ProgressCallback | None,
    *,
    stage: str,
    message: str,
    progress: float | None = None,
    indeterminate: bool = False,
    total_bytes: int | None = None,
    downloaded_bytes: int | None = None,
    **metadata: object,
) -> None:
    if progress_callback is None:
        return
    event: dict[str, object] = {
        "stage": stage,
        "message": message,
        "indeterminate": indeterminate,
    }
    if progress is not None:
        event["progress"] = max(0.0, min(float(progress), 1.0))
    if total_bytes is not None:
        event["total_bytes"] = int(total_bytes)
    if downloaded_bytes is not None:
        event["downloaded_bytes"] = int(downloaded_bytes)
    event.update(metadata)
    progress_callback(event)


def _repo_file_name(repo_file: dict[str, object]) -> str:
    for key in ("Name", "Path", "Key", "FilePath", "FileName"):
        value = str(repo_file.get(key) or "").strip().replace("\\", "/")
        if value:
            return value.lstrip("/")
    return ""


def _download_file_patterns(spec: ASRRuntimeSpec) -> tuple[str, ...]:
    patterns: list[str] = []
    for filename in ("configuration.json", "config.yaml", *spec.required_files):
        clean = str(filename or "").strip().replace("\\", "/")
        if clean and clean not in patterns:
            patterns.append(clean)
    return tuple(patterns)


def _file_matches_patterns(filename: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    normalized = filename.replace("\\", "/").lstrip("/")
    return any(
        normalized == pattern
        or normalized.endswith("/" + pattern)
        for pattern in patterns
    )


def _fetch_remote_snapshot_info(model_id: str, model_revision: str) -> dict[str, object]:
    try:
        from modelscope.hub.api import HubApi
    except ImportError as exc:
        raise RuntimeError(
            "modelscope is required to query and download ASR models."
        ) from exc

    try:
        api = HubApi()
        endpoint = api.get_endpoint_for_read(repo_id=model_id, repo_type="model")
        cookies = api.get_cookies()
        revision_detail = api.get_valid_revision_detail(
            model_id,
            revision=model_revision,
            cookies=cookies,
            endpoint=endpoint,
        )
        resolved_revision = str(revision_detail["Revision"])
        repo_files = api.get_model_files(
            model_id=model_id,
            revision=resolved_revision,
            recursive=True,
            use_cookies=False if cookies is None else cookies,
            headers={"Snapshot": "True"},
            endpoint=endpoint,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to query ASR model revision: {exc}") from exc

    total_size = 0
    for repo_file in repo_files:
        if repo_file.get("Type") == "tree":
            continue
        total_size += int(repo_file.get("Size") or 0)

    return {
        "resolved_revision": resolved_revision,
        "total_size": total_size or None,
    }


def _fetch_required_snapshot_info(spec: ASRRuntimeSpec, model_revision: str) -> dict[str, object]:
    snapshot_info = _fetch_remote_snapshot_info(spec.model_id, model_revision)
    patterns = _download_file_patterns(spec)
    try:
        from modelscope.hub.api import HubApi

        api = HubApi()
        endpoint = api.get_endpoint_for_read(repo_id=spec.model_id, repo_type="model")
        cookies = api.get_cookies()
        resolved_revision = str(snapshot_info.get("resolved_revision") or model_revision)
        repo_files = api.get_model_files(
            model_id=spec.model_id,
            revision=resolved_revision,
            recursive=True,
            use_cookies=False if cookies is None else cookies,
            headers={"Snapshot": "True"},
            endpoint=endpoint,
        )
    except Exception:
        return snapshot_info

    total_size = 0
    for repo_file in repo_files:
        if repo_file.get("Type") == "tree":
            continue
        filename = _repo_file_name(repo_file)
        if _file_matches_patterns(filename, patterns):
            total_size += int(repo_file.get("Size") or 0)
    if total_size > 0:
        snapshot_info["total_size"] = total_size
    return snapshot_info


class _AggregateDownloadProgress:
    def __init__(
        self,
        total_bytes: int | None,
        progress_callback: ProgressCallback | None,
    ) -> None:
        self.total_bytes = int(total_bytes or 0)
        self.progress_callback = progress_callback
        self.downloaded_bytes = 0
        self._lock = threading.Lock()

    def update(self, delta_bytes: int) -> None:
        if delta_bytes <= 0:
            return
        with self._lock:
            self.downloaded_bytes += delta_bytes
            total_bytes = self.total_bytes
            downloaded_bytes = self.downloaded_bytes

        if total_bytes > 0:
            progress = min(downloaded_bytes / total_bytes, 0.999)
            _emit_progress(
                self.progress_callback,
                stage="download",
                message="downloading",
                progress=progress,
                total_bytes=total_bytes,
                downloaded_bytes=downloaded_bytes,
            )
            return

        _emit_progress(
            self.progress_callback,
            stage="download",
            message="downloading",
            indeterminate=True,
            downloaded_bytes=downloaded_bytes,
        )

    def finish(self) -> None:
        _emit_progress(
            self.progress_callback,
            stage="download_complete",
            message="download_complete",
            progress=1.0,
            total_bytes=self.total_bytes or None,
            downloaded_bytes=self.downloaded_bytes or None,
        )


def _is_pinned_sensevoice_spec(spec: ASRRuntimeSpec) -> bool:
    return (
        spec.engine == "sensevoice-small"
        and spec.model_id == SENSEVOICE_DEFAULT_MODEL
        and spec.model_revision == SENSEVOICE_DEFAULT_REVISION
        and bool(spec.required_file_sha256)
    )


def _prepare_secure_target_directory(target_dir: pathlib.Path) -> pathlib.Path:
    target = _prepare_secure_target_parent(target_dir)
    info = _lstat_optional(target)
    if info is None:
        try:
            target.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RuntimeError(f"Unable to create ASR model target directory: {target}") from exc
        info = _lstat_optional(target)
    if info is None or not _is_safe_directory_stat(info):
        raise RuntimeError(f"Unsafe ASR model target directory: {target}")
    return target


def _open_part_file(part_path: pathlib.Path, *, root: pathlib.Path):
    _prepare_secure_descendant_directory(root, part_path.parent)
    info = _lstat_optional(part_path)
    if info is not None:
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse_point(info)
        ):
            raise RuntimeError(f"Unsafe partial model file: {part_path}")
        try:
            part_path.unlink()
        except OSError as exc:
            raise RuntimeError(f"Unable to replace partial model file: {part_path}") from exc

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(part_path, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"Partial model file changed concurrently: {part_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to create partial model file: {part_path}") from exc
    return os.fdopen(descriptor, "wb")


def _write_verified_stream(
    chunks,
    *,
    target_path: pathlib.Path,
    root: pathlib.Path,
    expected_sha256: str,
    tracker: _AggregateDownloadProgress,
) -> None:
    part_path = target_path.with_name(target_path.name + ".part")
    hasher = hashlib.sha256()
    written = 0
    try:
        with _open_part_file(part_path, root=root) as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                written += len(chunk)
                if written > _MAX_MODEL_FILE_BYTES:
                    raise RuntimeError(
                        f"ASR model file exceeds the safety limit: {target_path.name}"
                    )
                handle.write(chunk)
                hasher.update(chunk)
                tracker.update(len(chunk))
            handle.flush()
            os.fsync(handle.fileno())
        actual_sha256 = hasher.hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"SHA-256 mismatch for {target_path.name}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        os.replace(part_path, target_path)
        if not _is_regular_nonlink_file(target_path, root=root):
            raise RuntimeError(f"Downloaded model file is unsafe: {target_path.name}")
        if _sha256_file(target_path) != expected_sha256:
            raise RuntimeError(f"Downloaded model file changed after verification: {target_path.name}")
    except Exception:
        try:
            if part_path.exists() and not part_path.is_symlink():
                part_path.unlink()
        except OSError:
            logger.warning("Failed to remove partial model file %s", part_path, exc_info=True)
        raise


def _copy_verified_modelscope_file(
    source_path: pathlib.Path,
    *,
    target_path: pathlib.Path,
    root: pathlib.Path,
    expected_sha256: str,
    tracker: _AggregateDownloadProgress,
) -> None:
    if not source_path.is_file():
        raise RuntimeError(f"ModelScope did not return a regular file: {source_path}")

    def chunks():
        with source_path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    return
                yield chunk

    _write_verified_stream(
        chunks(),
        target_path=target_path,
        root=root,
        expected_sha256=expected_sha256,
        tracker=tracker,
    )


def _download_verified_modelscope_file(
    spec: ASRRuntimeSpec,
    filename: str,
    *,
    target_path: pathlib.Path,
    root: pathlib.Path,
    expected_sha256: str,
    tracker: _AggregateDownloadProgress,
    modelscope_cache: pathlib.Path,
) -> None:
    try:
        from modelscope.hub.file_download import model_file_download
    except ImportError as exc:
        raise RuntimeError("ModelScope per-file download is unavailable") from exc
    downloaded = model_file_download(
        spec.model_id,
        filename,
        revision=SENSEVOICE_DEFAULT_REVISION,
        cache_dir=str(modelscope_cache),
    )
    if not downloaded:
        raise RuntimeError(f"ModelScope returned no file for {filename}")
    _copy_verified_modelscope_file(
        pathlib.Path(downloaded),
        target_path=target_path,
        root=root,
        expected_sha256=expected_sha256,
        tracker=tracker,
    )


def _download_verified_http_file(
    spec: ASRRuntimeSpec,
    filename: str,
    *,
    target_path: pathlib.Path,
    root: pathlib.Path,
    expected_sha256: str,
    tracker: _AggregateDownloadProgress,
) -> None:
    from urllib.parse import quote

    import requests

    quoted_filename = quote(filename, safe="/")
    url = (
        f"https://www.modelscope.cn/models/{spec.model_id}/resolve/"
        f"{SENSEVOICE_DEFAULT_REVISION}/{quoted_filename}"
    )
    with open_validated_requests_response(
        url,
        url_validator=_is_safe_model_download_url,
        timeout=(10, 120),
        label=f"SenseVoice model file {filename}",
        stream=True,
        max_redirects=5,
        request_get=requests.get,
    ) as response:
        response.raise_for_status()
        _write_verified_stream(
            response.iter_content(chunk_size=1024 * 1024),
            target_path=target_path,
            root=root,
            expected_sha256=expected_sha256,
            tracker=tracker,
        )


def _download_pinned_sensevoice_to(
    spec: ASRRuntimeSpec,
    target_dir: pathlib.Path,
    *,
    model_revision: str,
    progress_callback: ProgressCallback | None,
) -> pathlib.Path:
    if model_revision != SENSEVOICE_DEFAULT_REVISION:
        raise RuntimeError(
            "SenseVoice downloads require the application-pinned immutable revision "
            f"{SENSEVOICE_DEFAULT_REVISION}."
        )
    trusted_hashes = dict(spec.required_file_sha256)
    if set(trusted_hashes) != set(spec.required_files):
        raise RuntimeError("SenseVoice trusted hashes do not cover every required file")

    target_dir = _prepare_secure_target_directory(target_dir)
    modelscope_cache = _prepare_secure_directory(cache_dir())
    tracker = _AggregateDownloadProgress(None, progress_callback)
    _emit_progress(
        progress_callback,
        stage="download_prepare",
        message="download_prepare",
        indeterminate=True,
        downloaded_bytes=0,
    )

    for filename in spec.required_files:
        expected_sha256 = str(trusted_hashes[filename]).lower()
        target_path = _safe_required_file_path(target_dir, filename)
        _prepare_secure_descendant_directory(target_dir, target_path.parent)
        if (
            _is_regular_nonlink_file(target_path, root=target_dir)
            and _sha256_file(target_path) == expected_sha256
        ):
            continue

        last_error: Exception | None = None
        for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
            try:
                _download_verified_modelscope_file(
                    spec,
                    filename,
                    target_path=target_path,
                    root=target_dir,
                    expected_sha256=expected_sha256,
                    tracker=tracker,
                    modelscope_cache=modelscope_cache,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Pinned ModelScope download failed for %s (attempt %d/%d)",
                    filename,
                    attempt,
                    _DOWNLOAD_ATTEMPTS,
                    exc_info=True,
                )
            try:
                _download_verified_http_file(
                    spec,
                    filename,
                    target_path=target_path,
                    root=target_dir,
                    expected_sha256=expected_sha256,
                    tracker=tracker,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Pinned HTTPS fallback failed for %s (attempt %d/%d)",
                    filename,
                    attempt,
                    _DOWNLOAD_ATTEMPTS,
                    exc_info=True,
                )
            if attempt < _DOWNLOAD_ATTEMPTS:
                _emit_progress(
                    progress_callback,
                    stage="download_retry",
                    message="",
                    indeterminate=True,
                    downloaded_bytes=tracker.downloaded_bytes,
                    attempt=attempt + 1,
                    max_attempts=_DOWNLOAD_ATTEMPTS,
                )
                time.sleep(min(2.0 * attempt, 5.0))
        if last_error is not None:
            raise RuntimeError(
                f"Failed to download verified SenseVoice file {filename}: {last_error}"
            ) from last_error

    if not _is_complete_model_dir(target_dir, spec) or not verify_model_integrity(
        target_dir, spec
    ):
        raise RuntimeError(
            "SenseVoice download completed but the pinned model failed integrity or configuration validation."
        )
    _write_model_metadata(
        target_dir,
        spec=spec,
        requested_revision=model_revision,
        resolved_revision=SENSEVOICE_DEFAULT_REVISION,
    )
    tracker.finish()
    return target_dir


def _download_model_to(
    spec: ASRRuntimeSpec,
    target_dir: pathlib.Path,
    *,
    model_revision: str,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    target_dir = _prepare_secure_target_parent(target_dir)
    if _is_pinned_sensevoice_spec(spec):
        return _download_pinned_sensevoice_to(
            spec,
            target_dir,
            model_revision=model_revision,
            progress_callback=progress_callback,
        )
    modelscope_cache = _prepare_secure_directory(cache_dir())
    try:
        from modelscope.hub.callback import ProgressCallback as ModelscopeProgressCallback
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "modelscope is required to download ASR models."
        ) from exc

    snapshot_info = _fetch_required_snapshot_info(spec, model_revision)
    resolved_revision = str(snapshot_info.get("resolved_revision", "")).strip() or model_revision
    total_bytes = snapshot_info.get("total_size")
    if not isinstance(total_bytes, int):
        total_bytes = None
    file_patterns = _download_file_patterns(spec)
    staging_dir, staging_identity = _create_owned_sibling_directory(
        target_dir,
        "staging",
    )
    tracker = _AggregateDownloadProgress(total_bytes, progress_callback)

    class _ModelscopeProgress(ModelscopeProgressCallback):
        def update(self, size: int):
            tracker.update(size)

        def end(self):
            return None

    try:
        _emit_progress(
            progress_callback,
            stage="download_prepare",
            message="download_prepare",
            progress=0.0 if total_bytes else None,
            indeterminate=not total_bytes,
            total_bytes=total_bytes,
            downloaded_bytes=0,
        )

        last_error: Exception | None = None
        for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
            try:
                with _ensure_download_stdio():
                    logger.info(
                        "Downloading %s model via ModelScope (revision=%s, files=%s, cache=%s, staging=%s)",
                        spec.label,
                        resolved_revision,
                        ", ".join(file_patterns),
                        modelscope_cache,
                        staging_dir,
                    )
                    snapshot_download(
                        spec.model_id,
                        revision=resolved_revision,
                        cache_dir=str(modelscope_cache),
                        local_dir=str(staging_dir),
                        allow_file_pattern=list(file_patterns),
                        enable_file_lock=True,
                        max_workers=1,
                        progress_callbacks=[_ModelscopeProgress],
                    )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "%s model download attempt %d/%d failed",
                    spec.label,
                    attempt,
                    _DOWNLOAD_ATTEMPTS,
                    exc_info=True,
                )
                if attempt >= _DOWNLOAD_ATTEMPTS:
                    break
                _emit_progress(
                    progress_callback,
                    stage="download_retry",
                    message="",
                    indeterminate=True,
                    total_bytes=total_bytes,
                    downloaded_bytes=tracker.downloaded_bytes,
                    attempt=attempt + 1,
                    max_attempts=_DOWNLOAD_ATTEMPTS,
                )
                time.sleep(min(2.0 * attempt, 5.0))
        if last_error is not None:
            raise RuntimeError(
                f"{spec.label} model download failed after {_DOWNLOAD_ATTEMPTS} attempts. "
                f"The ModelScope cache was kept in {modelscope_cache}. "
                f"Original error: {last_error}"
            ) from last_error

        if not _is_complete_model_dir(staging_dir, spec):
            raise RuntimeError(
                f"{spec.label} model download finished but the staging directory is incomplete"
            )
        if not verify_model_integrity(staging_dir, spec):
            raise RuntimeError(
                f"{spec.label} model download finished but failed integrity validation."
            )

        _write_model_metadata(
            staging_dir,
            spec=spec,
            requested_revision=model_revision,
            resolved_revision=resolved_revision,
        )
        if _directory_identity(staging_dir) != staging_identity:
            raise RuntimeError("ASR model staging directory changed before installation")
        _install_staged_directory(staging_dir, staging_identity, target_dir)
        tracker.finish()
        return target_dir
    finally:
        _cleanup_owned_directory(
            staging_dir,
            staging_identity,
            purpose="staging",
        )


def download_model(
    spec: ASRRuntimeSpec,
    *,
    force: bool = False,
    model_revision: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    if not force:
        existing = existing_model_path(spec)
        if existing is not None:
            return existing

    with _DOWNLOAD_LOCK:
        if not force:
            existing = existing_model_path(spec)
            if existing is not None:
                return existing
        return _download_model_to(
            spec,
            model_dir(spec),
            model_revision=str(model_revision or spec.model_revision).strip() or spec.model_revision,
            progress_callback=progress_callback,
        )


def download_model_to(
    spec: ASRRuntimeSpec,
    target_dir: str | pathlib.Path,
    *,
    force: bool = False,
    model_revision: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    target = _prepare_secure_target_parent(target_dir)
    revision = str(model_revision or spec.model_revision).strip() or spec.model_revision
    if not force and _is_complete_model_dir(target, spec) and verify_model_integrity(target, spec):
        return target
    with _DOWNLOAD_LOCK:
        target = _prepare_secure_target_parent(target)
        if not force and _is_complete_model_dir(target, spec) and verify_model_integrity(target, spec):
            return target
        return _download_model_to(
            spec,
            target,
            model_revision=revision,
            progress_callback=progress_callback,
        )


def check_model_update(spec: ASRRuntimeSpec) -> dict[str, object]:
    status = get_local_model_status(spec)
    local_revision = str(status.get("local_revision", "")).strip()
    local_revision_known = bool(local_revision)
    if _is_pinned_sensevoice_spec(spec):
        remote_revision = SENSEVOICE_DEFAULT_REVISION
        update_available = False
    else:
        snapshot_info = _fetch_remote_snapshot_info(spec.model_id, spec.model_revision)
        remote_revision = str(snapshot_info.get("resolved_revision", "")).strip()
        update_available = bool(
            status.get("installed")
            and local_revision_known
            and remote_revision
            and local_revision != remote_revision
        )
    status.update(
        {
            "remote_revision": remote_revision,
            "local_revision_known": local_revision_known,
            "update_available": update_available,
        }
    )
    return status
