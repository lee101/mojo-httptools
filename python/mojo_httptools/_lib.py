from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src", "parser.mojo")
LIB = os.environ.get("MOJO_HTTPTOOLS_LIB") or os.path.join(
    ROOT, "dist", "libmojo-httptools.so"
)

I = ctypes.c_int64


class BuildError(RuntimeError):
    pass


def mojo_command() -> list[str]:
    override = os.environ.get("MOJO_HTTPTOOLS_MOJO")
    if override:
        return override.split()
    found = shutil.which("mojo")
    if found:
        return [found]
    pixi = shutil.which("pixi") or os.path.expanduser("~/.pixi/bin/pixi")
    manifest = os.path.join(ROOT, "pixi.toml")
    if os.path.exists(pixi) and os.path.exists(manifest):
        return [pixi, "run", "--manifest-path", manifest, "mojo"]
    raise BuildError("mojo not found; set MOJO_HTTPTOOLS_MOJO=/path/to/mojo")


def build(force: bool = False) -> str:
    if os.environ.get("MOJO_HTTPTOOLS_LIB") and os.path.exists(LIB) and not force:
        return LIB
    if not os.path.exists(SRC):
        if os.path.exists(LIB):
            return LIB
        raise BuildError(f"no Mojo source at {SRC} and no shared library at {LIB}")
    if (
        not force
        and os.path.exists(LIB)
        and os.path.getmtime(LIB) >= os.path.getmtime(SRC)
    ):
        return LIB
    os.makedirs(os.path.dirname(LIB), exist_ok=True)
    command = mojo_command() + ["build", "--emit", "shared-lib", SRC, "-o", LIB]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not os.path.exists(LIB):
        raise BuildError((proc.stderr or proc.stdout).strip()[:4000])
    return LIB


_library: ctypes.PyDLL | None = None


def lib() -> ctypes.CDLL:
    global _library
    if _library is None:
        _library = ctypes.PyDLL(build())
        function = _library.mht_parse_request
        function.argtypes = [I, I, I, I, I]
        function.restype = I
    return _library


def main() -> int:
    print(build(force="--force" in sys.argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
