"""
Font management helper for assets/fonts/.

Usage:
    # List all bundled fonts and their internal family names
    uv run python scripts/add_font.py --list

    # Inspect a single font file
    uv run python scripts/add_font.py --check assets/fonts/Inter-Regular.ttf
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"


# ---------------------------------------------------------------------------
# Minimal TTF name-table reader (no dependencies beyond stdlib)
# ---------------------------------------------------------------------------

_NAME_IDS = {
    1: "Family",
    2: "Style",
    4: "Full",
    6: "PostScript",
}

_PLATFORM_UTF16 = {3}  # Windows / Unicode platform: encode as UTF-16 BE


def _read_name_records(data: bytes) -> dict[int, str]:
    """Extract name records from a TTF/OTF binary (no external deps)."""
    # Offset table
    num_tables = struct.unpack_from(">H", data, 4)[0]
    table_map: dict[str, int] = {}
    offset = 12
    for _ in range(num_tables):
        tag = data[offset : offset + 4].decode("ascii", errors="ignore")
        _checksum, tbl_offset, _length = struct.unpack_from(">III", data, offset + 4)
        table_map[tag] = tbl_offset
        offset += 16

    name_offset = table_map.get("name")
    if name_offset is None:
        return {}

    count, string_offset = struct.unpack_from(">HH", data, name_offset + 2)
    records: dict[int, str] = {}
    rec_offset = name_offset + 6
    for _ in range(count):
        platform_id, _enc_id, _lang_id, name_id, length, value_offset = (
            struct.unpack_from(">HHHHHH", data, rec_offset)
        )
        rec_offset += 12
        abs_offset = name_offset + string_offset + value_offset
        raw = data[abs_offset : abs_offset + length]
        if name_id in _NAME_IDS:
            encoding = "utf-16-be" if platform_id in _PLATFORM_UTF16 else "latin-1"
            try:
                records[name_id] = raw.decode(encoding)
            except UnicodeDecodeError:
                records[name_id] = raw.decode("latin-1", errors="replace")
    return records


def inspect_font(path: Path) -> dict[int, str]:
    """Return the name records from a TTF/OTF file."""
    data = path.read_bytes()
    return _read_name_records(data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_list(_args: argparse.Namespace) -> None:
    """List all fonts in assets/fonts/ with their family names."""
    font_files = sorted(
        p for p in FONTS_DIR.iterdir() if p.suffix.lower() in {".ttf", ".otf"}
    )
    if not font_files:
        print(f"No font files found in {FONTS_DIR}")
        return

    col_w = max(len(p.name) for p in font_files)
    print(f"\n{'File':<{col_w}}  {'Family':<30}  Style")
    print("-" * (col_w + 50))
    for path in font_files:
        records = inspect_font(path)
        family = records.get(1, "?")
        style = records.get(2, "?")
        print(f"{path.name:<{col_w}}  {family:<30}  {style}")
    print()


def cmd_check(args: argparse.Namespace) -> None:
    """Print name records for a specific font file."""
    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    records = inspect_font(path)
    labels = {1: "Family", 2: "Style", 4: "Full", 6: "PostScript"}
    print(f"\n{path.name}")
    print("-" * len(path.name))
    for name_id, label in labels.items():
        print(f"  {label:<12}: {records.get(name_id, '<not present>')}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Font helper for assets/fonts/")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        action="store_true",
        help="List all bundled fonts and their internal family names",
    )
    group.add_argument(
        "--check",
        metavar="FILE",
        dest="file",
        help="Inspect a single font file and print its name records",
    )
    args = parser.parse_args()

    if args.list:
        cmd_list(args)
    else:
        cmd_check(args)


if __name__ == "__main__":
    main()
