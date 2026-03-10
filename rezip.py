import os
import pathlib
import subprocess
import sys
import zipfile


src = pathlib.Path('b:/python_project/vrc-translator/dist/MioTranslator')
exe = src / 'MioTranslator.exe'
out = pathlib.Path('b:/python_project/vrc-translator/dist/MioTranslator-windows-x64.zip')


def verify_bundle() -> None:
    env = os.environ.copy()
    env['MIO_TRANSLATOR_SELFTEST'] = '1'
    proc = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    output = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        raise SystemExit(
            f'Release bundle self-test failed ({proc.returncode}): {output or "no output"}'
        )
    print(output or 'Release bundle self-test passed.')


if not exe.is_file():
    raise SystemExit(f'Missing release executable: {exe}')

verify_bundle()
out.unlink(missing_ok=True)
files = [f for f in src.rglob('*') if f.is_file()]
print(f'Zipping {len(files)} files...')
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for i, f in enumerate(files):
        zf.write(f, f.relative_to(src.parent))
        if i % 100 == 0:
            print(f'  {i}/{len(files)}', flush=True)
print(f'Done: {out.stat().st_size // 1024 // 1024} MB')

