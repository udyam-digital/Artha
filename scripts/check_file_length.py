#!/usr/bin/env python3
"""Pre-commit hook: reject Python files that exceed MAX_LINES lines.

Usage (called by pre-commit with staged file paths as arguments):
    python scripts/check_file_length.py file1.py file2.py ...
"""

import sys

MAX_LINES = 400

violations: list[tuple[str, int]] = []

for path in sys.argv[1:]:
    try:
        with open(path, encoding="utf-8") as f:
            count = sum(1 for _ in f)
        if count > MAX_LINES:
            violations.append((path, count))
    except OSError:
        pass  # deleted/renamed files — pre-commit handles these

if violations:
    print(f"\n\033[31m✗ File length limit exceeded ({MAX_LINES} lines max):\033[0m")
    for path, count in violations:
        print(f"  {path}: {count} lines  (+{count - MAX_LINES} over limit)")
    print("\n  Split the file before committing. See CLAUDE.md for refactor patterns.\n")
    sys.exit(1)
