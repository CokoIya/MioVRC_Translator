# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

datas = [('config.example.json', '.'), ('assets', 'assets')]
if Path('models').exists():
    datas.append(('models', 'models'))
binaries = []
hiddenimports = []

tmp_ret = collect_all('faster_whisper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('ctranslate2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('huggingface_hub')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

hiddenimports += [
    'faster_whisper',
    'ctranslate2',
    'huggingface_hub',
    'src.asr.factory',
    'src.asr.model_manager',
    'src.asr.whisper_asr',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # これらの大型依存関係は推移的に取り込まれるが、実行時には使用しない。
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'keras',
        'numba', 'llvmlite',
        'scipy',
        'sklearn', 'scikit_learn',
        'matplotlib',
        'IPython', 'ipykernel', 'ipywidgets',
        'notebook', 'jupyter',
        'pandas',
        'lxml',
        'aliyunsdkcore',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MioTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icons/app_icon_mio.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MioTranslator',
)
