import zipfile, pathlib
src = pathlib.Path('b:/python_project/vrc-translator/dist/MioTranslator')
out = pathlib.Path('b:/python_project/vrc-translator/dist/MioTranslator-windows-x64.zip')
out.unlink(missing_ok=True)
files = [f for f in src.rglob('*') if f.is_file()]
print(f'Zipping {len(files)} files...')
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for i, f in enumerate(files):
        zf.write(f, f.relative_to(src.parent))
        if i % 100 == 0:
            print(f'  {i}/{len(files)}', flush=True)
print(f'Done: {out.stat().st_size // 1024 // 1024} MB')
