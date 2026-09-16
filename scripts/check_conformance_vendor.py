#!/usr/bin/env python3
"""Verify the vendored Letterbox fixture snapshot without network access."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / "vendor" / "letterbox-conformance-source.json",
    ROOT / "vendor" / "doorbell-outcome-source.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_one(source_path: Path) -> None:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    snapshot = ROOT / source["snapshot_directory"]
    manifest = snapshot / "SHA256SUMS"
    actual_manifest = sha256(manifest)
    if actual_manifest != source["manifest_sha256"]:
        raise SystemExit(
            f"conformance manifest mismatch ({source_path.name}): {actual_manifest}"
        )

    listed: set[Path] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, relative = line.split("  ", 1)
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe conformance manifest path: {relative}")
        target = snapshot / path
        listed.add(path)
        actual = sha256(target)
        if actual != digest:
            raise SystemExit(f"conformance fixture mismatch: {relative}")

    present = {
        path.relative_to(snapshot)
        for path in snapshot.rglob("*")
        if path.is_file() and path != manifest
    }
    if present != listed:
        raise SystemExit(
            f"conformance fixture inventory mismatch: "
            f"missing={sorted(map(str, listed - present))} "
            f"extra={sorted(map(str, present - listed))}"
        )
    print(
        "conformance vendor: PASS "
        f"{source['snapshot_directory']} "
        f"({len(listed)} files, upstream {source['upstream_commit'][:8]})"
    )


def verify() -> None:
    for source_path in SOURCES:
        verify_one(source_path)


if __name__ == "__main__":
    verify()
