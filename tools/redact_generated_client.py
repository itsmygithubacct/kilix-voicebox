#!/usr/bin/env python3
"""Restore the product's redacted logs after running the pinned client generator."""
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "app/src/lib/api"
    repairs = {
        "core/request.ts": ("console.error(error);",
                            "console.error('VOICEBOX_API_BODY_SERIALIZATION_FAILED');"),
        "core/CancelablePromise.ts": ("console.warn('Cancellation threw an error', error);",
                                      "console.warn('VOICEBOX_API_CANCELLATION_FAILED');"),
    }
    changes = {}
    for name, (before, after) in repairs.items():
        path = root / name
        content = path.read_text()
        if content.count(before) != 1 or after in content:
            raise SystemExit("generated client logging template changed")
        changes[path] = content.replace(before, after)
    for path in (root / "models").glob("*.ts"):
        changes[path] = path.read_text().rstrip() + "\n"
    for path, content in changes.items():
        path.write_text(content)


if __name__ == "__main__":
    main()
