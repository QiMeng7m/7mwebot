"""Extract the bundled native DLLs from a released webot.exe.

The repository intentionally does not track ``native/windows/*.dll`` (see
``.gitignore``); CI restores them from a previous release archive.  When
running from source, use this script to do the same:

    # 1. Download webot.exe from the releases page
    # 2. Install the build dependency
    pip install pyinstaller

    # 3. Extract all six DLLs into native/windows/
    python tools/extract_native_dlls.py --archive path/to/webot.exe --dest native/windows

The EXE is only read as a PyInstaller archive; nothing in it is executed.
"""
import argparse
import sys
from pathlib import Path

REQUIRED_DLLS = [
    "MSVCP140.dll",
    "VCRUNTIME140.dll",
    "VCRUNTIME140_1.dll",
    "WCDB.dll",
    "wcdb_api.dll",
    "wx_key.dll",
]


def find_entry_name(toc, dll: str):
    """Find the archive entry for *dll*, preferring paths under lib/."""
    dll_lower = dll.lower()
    preferred: list = []
    fallbacks: list = []
    for name in toc:
        normalized = str(name).replace("\\", "/").lower()
        if normalized == dll_lower:
            fallbacks.append(name)
        elif normalized.endswith("/" + dll_lower):
            if normalized.startswith("lib/"):
                preferred.append(name)
            else:
                fallbacks.append(name)
    return (preferred or fallbacks or [None])[0]


def extract(archive_path: Path, dest: Path) -> int:
    if not archive_path.is_file():
        print(f"archive not found: {archive_path}", file=sys.stderr)
        return 2

    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        print(
            "PyInstaller is required to read the archive.\n"
            "Install it with:  pip install pyinstaller",
            file=sys.stderr,
        )
        return 2

    archive = CArchiveReader(str(archive_path))
    dest.mkdir(parents=True, exist_ok=True)

    missing: list[str] = []
    for dll in REQUIRED_DLLS:
        entry = find_entry_name(archive.toc, dll)
        if not entry:
            missing.append(dll)
            continue
        (dest / dll).write_bytes(archive.extract(entry))
        print(f"OK  {dll}  <- {entry}")

    if missing:
        print("missing from archive: " + ", ".join(missing), file=sys.stderr)
        return 1

    print(f"\nExtracted {len(REQUIRED_DLLS)} DLLs to {dest.resolve()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract native windows DLLs from a released webot.exe"
    )
    parser.add_argument("--archive", required=True, help="path to webot.exe")
    parser.add_argument(
        "--dest", default="native/windows", help="output directory (default: native/windows)"
    )
    args = parser.parse_args()
    return extract(Path(args.archive), Path(args.dest))


if __name__ == "__main__":
    raise SystemExit(main())
