from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


_DROP_PARTS = {
    "__pycache__",
    "benchmark",
    "benchmarks",
    "doc",
    "docs",
    "example",
    "examples",
    "test",
    "tests",
    "testing",
}


def _keep_submodule(module_name: str) -> bool:
    parts = tuple(part.lower() for part in str(module_name or "").split(".") if part)
    if not parts:
        return False
    if parts[:4] in {
        ("sklearn", "externals", "array_api_compat", "cupy"),
        ("sklearn", "externals", "array_api_compat", "dask"),
    }:
        return False
    return not any(part in _DROP_PARTS for part in parts)


hiddenimports = collect_submodules("sklearn", filter=_keep_submodule)
datas = collect_data_files("sklearn")
binaries = collect_dynamic_libs("sklearn")
