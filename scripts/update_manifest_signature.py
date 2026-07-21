from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.updater.manifest_signature import (  # noqa: E402
    SIGNATURE_ALGORITHM,
    SIGNATURE_ALGORITHM_FIELD,
    SIGNATURE_FIELD,
    SIGNATURE_KEY_ID_FIELD,
    generate_seed_hex,
    public_key_from_seed,
    sign_manifest,
    verify_manifest_signature,
)


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_link_or_reparse(file_stat: os.stat_result) -> bool:
    attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        common = os.path.commonpath(
            (os.path.normcase(os.fspath(path)), os.path.normcase(os.fspath(parent)))
        )
    except ValueError:
        return False
    return common == os.path.normcase(os.fspath(parent))


def _assert_safe_ancestors(path: Path, *, label: str) -> None:
    for ancestor in reversed(path.parents):
        try:
            ancestor_stat = ancestor.lstat()
        except OSError as exc:
            raise RuntimeError(f"Unable to inspect {label} parent: {ancestor}") from exc
        if _is_link_or_reparse(ancestor_stat) or not stat.S_ISDIR(
            ancestor_stat.st_mode
        ):
            raise RuntimeError(f"{label} parent is unsafe: {ancestor}")


def _windows_security_modules():
    try:
        import ntsecuritycon
        import win32api
        import win32security
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Secure Windows key generation requires the locked pywin32 runtime"
        ) from exc
    return ntsecuritycon, win32api, win32security


def _allowed_windows_sids():
    _ntsecuritycon, win32api, win32security = _windows_security_modules()
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        win32security.TOKEN_QUERY,
    )
    try:
        current_sid = win32security.GetTokenInformation(
            token,
            win32security.TokenUser,
        )[0]
    finally:
        token.Close()
    return (
        current_sid,
        win32security.ConvertStringSidToSid("S-1-5-18"),
        win32security.ConvertStringSidToSid("S-1-5-32-544"),
    )


def _protect_windows_path(path: Path) -> None:
    ntsecuritycon, _win32api, win32security = _windows_security_modules()
    dacl = win32security.ACL()
    for sid in _allowed_windows_sids():
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            ntsecuritycon.FILE_ALL_ACCESS,
            sid,
        )
    win32security.SetNamedSecurityInfo(
        os.fspath(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )


def _assert_private_parent_secure(path: Path) -> None:
    parent = path.parent
    if os.name != "nt":
        if stat.S_IMODE(parent.stat().st_mode) & 0o077:
            raise RuntimeError(
                "Private seed directory must not grant group or other permissions"
            )
        return

    ntsecuritycon, _win32api, win32security = _windows_security_modules()
    descriptor = win32security.GetFileSecurity(
        os.fspath(parent),
        win32security.DACL_SECURITY_INFORMATION,
    )
    control, _revision = descriptor.GetSecurityDescriptorControl()
    if not control & win32security.SE_DACL_PROTECTED:
        raise RuntimeError(
            "Private seed directory must use protected, non-inherited access rules"
        )
    allowed = {
        win32security.ConvertSidToStringSid(sid) for sid in _allowed_windows_sids()
    }
    sensitive = (
        ntsecuritycon.FILE_ALL_ACCESS
        | ntsecuritycon.FILE_GENERIC_READ
        | ntsecuritycon.FILE_GENERIC_WRITE
        | ntsecuritycon.DELETE
        | ntsecuritycon.WRITE_DAC
        | ntsecuritycon.WRITE_OWNER
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:
        raise RuntimeError("Private seed directory has no access-control list")
    for index in range(dacl.GetAceCount()):
        header, mask, sid = dacl.GetAce(index)
        ace_type = header[0]
        sid_text = win32security.ConvertSidToStringSid(sid)
        if (
            ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE
            and mask & sensitive
            and sid_text not in allowed
        ):
            raise RuntimeError(
                "Private seed directory grants access to an unauthorized principal"
            )


def _prepare_new_output(
    raw_path: Path,
    *,
    label: str,
    require_external: bool,
    require_private_parent: bool,
) -> Path:
    raw_text = os.fspath(raw_path)
    if os.name == "nt" and raw_text.startswith(("\\\\?\\", "\\\\.\\")):
        raise RuntimeError(f"{label} path must not use a device or extended namespace")
    path = _absolute_path(raw_path)
    repo = _absolute_path(REPO_ROOT)
    if require_external and _is_within(path, repo):
        raise RuntimeError(f"{label} must be stored outside the repository")
    _assert_safe_ancestors(path, label=label)
    if not path.parent.is_dir():
        raise RuntimeError(f"{label} parent directory does not exist: {path.parent}")
    if os.path.lexists(path):
        raise RuntimeError(f"Refusing to overwrite existing {label}: {path}")
    if require_private_parent:
        _assert_private_parent_secure(path)
    return path


def _write_new_file(path: Path, payload: bytes, *, private: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600 if private else 0o644)
    try:
        if private and os.name == "nt":
            _protect_windows_path(path)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if private and os.name != "nt":
            os.chmod(path, 0o600)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_seed(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip().split()[0]


def _generate_key(args: argparse.Namespace) -> int:
    seed = generate_seed_hex()
    public_key = public_key_from_seed(seed)
    seed_path = _prepare_new_output(
        Path(args.seed_out),
        label="private seed file",
        require_external=True,
        require_private_parent=True,
    )
    public_path = None
    if args.public_out:
        public_path = _prepare_new_output(
            Path(args.public_out),
            label="public key file",
            require_external=False,
            require_private_parent=False,
        )
        if public_path == seed_path:
            raise RuntimeError("Private seed and public key paths must be different")
    _write_new_file(seed_path, (seed + "\n").encode("ascii"), private=True)
    if public_path is not None:
        _write_new_file(
            public_path,
            (public_key + "\n").encode("ascii"),
            private=False,
        )
    print(f"Private seed written to: {seed_path}")
    print(f"Public key: {public_key}")
    if public_path is not None:
        print(f"Public key written to: {public_path}")
    return 0


def _sign(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = _read_json(manifest_path)
    signature = sign_manifest(manifest, _read_seed(Path(args.seed_file)))
    manifest[SIGNATURE_ALGORITHM_FIELD] = SIGNATURE_ALGORITHM
    if args.key_id:
        manifest[SIGNATURE_KEY_ID_FIELD] = str(args.key_id).strip()
    manifest[SIGNATURE_FIELD] = signature
    _write_json(Path(args.output or manifest_path), manifest)
    print(f"Signed manifest: {Path(args.output or manifest_path)}")
    print(f"Signature: {signature}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = _read_json(manifest_path)
    public_key = str(args.public_key or "").strip()
    if not public_key and args.public_key_file:
        public_key = (
            Path(args.public_key_file).read_text(encoding="utf-8").strip().split()[0]
        )
    verify_manifest_signature(
        manifest,
        public_key,
        required=True,
        expected_key_id=args.key_id,
    )
    print(f"Manifest signature OK: {manifest_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate and verify Mio update manifest signatures."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate-key", help="Generate an Ed25519 signing seed."
    )
    generate.add_argument(
        "--seed-out", required=True, help="Path for the private seed hex file."
    )
    generate.add_argument(
        "--public-out", default="", help="Optional path for the public key hex file."
    )
    generate.set_defaults(func=_generate_key)

    sign = subparsers.add_parser("sign", help="Sign an update manifest JSON file.")
    sign.add_argument("--manifest", required=True, help="Manifest JSON path.")
    sign.add_argument("--seed-file", required=True, help="Private seed hex file.")
    sign.add_argument(
        "--output",
        default="",
        help="Output path. Defaults to updating the manifest in place.",
    )
    sign.add_argument(
        "--key-id",
        default="",
        help="Optional signature key id to write into the manifest.",
    )
    sign.set_defaults(func=_sign)

    verify = subparsers.add_parser("verify", help="Verify a signed update manifest.")
    verify.add_argument("--manifest", required=True, help="Manifest JSON path.")
    verify.add_argument("--public-key", default="", help="Public key hex.")
    verify.add_argument(
        "--public-key-file", default="", help="File containing public key hex."
    )
    verify.add_argument(
        "--key-id", default="", help="Optional expected signature key id."
    )
    verify.set_defaults(func=_verify)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
