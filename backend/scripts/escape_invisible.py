"""Replace invisible/control characters in Python sources with escape sequences.

Source files must not contain literal bidi overrides, line separators or other invisible
characters (they hide what code does; cf. "Trojan Source", CVE-2021-42574). Run with
``--check`` in CI to fail when such characters are present.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

INVISIBLE = {
    *range(0x00, 0x09),
    *range(0x0B, 0x0D),
    *range(0x0E, 0x20),
    *range(0x7F, 0xA0),
    0x2028,
    0x2029,
    *range(0x200B, 0x2010),
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
    0xFEFF,
}


def escape(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if code in INVISIBLE and ch not in "\r":
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    bad: list[Path] = []
    for root in args.paths:
        for path in sorted(root.rglob("*.py")) if root.is_dir() else [root]:
            text = path.read_text(encoding="utf-8")
            fixed = escape(text)
            if fixed != text:
                bad.append(path)
                if not args.check:
                    path.write_text(fixed, encoding="utf-8")
    for p in bad:
        sys.stdout.write(f"{'invisible characters in' if args.check else 'escaped'} {p}\n")
    return 1 if (bad and args.check) else 0


if __name__ == "__main__":
    raise SystemExit(main())
