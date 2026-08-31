#!/usr/bin/env python3
"""Assert the shipped package declares no third-party RUNTIME dependencies.

This replaces an earlier check that forbade pyproject.toml existing at all.
That was right when we shipped no packaging and became wrong the moment we did,
so the check now asserts the property rather than the absence of a file.

THE DISTINCTION THAT MATTERS, and the reason this file explains itself: a BUILD
backend is install-time tooling and never imported by the running code. A
RUNTIME dependency is imported by the daemon that holds your messaging token.
Listing hatchling under build-system is fine. Listing anything under
project.dependencies is not.

Do not let a later reviewer ban pyproject.toml again.
"""
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Manifests that would reintroduce runtime dependencies by another route.
FORBIDDEN = (
    "requirements.txt", "requirements-dev.txt", "setup.py", "setup.cfg",
    "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock",
)


def main():
    failures = []

    for name in FORBIDDEN:
        if (ROOT / name).exists():
            failures.append(f"{name} present: runtime dependencies must not be declared here")

    path = ROOT / "pyproject.toml"
    if not path.is_file():
        failures.append("pyproject.toml is missing: the package must be installable")
    else:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        deps = data.get("project", {}).get("dependencies", [])
        if deps:
            failures.append(
                f"project.dependencies is not empty: {deps}. The core is stdlib-only "
                f"so a stranger's audit stays finishable; this is a security "
                f"property, not a style choice."
            )
        else:
            print("pyproject.toml: project.dependencies is empty")
        build = data.get("build-system", {}).get("requires", [])
        print(f"build backend (install-time only, not imported at runtime): {build}")

    if failures:
        print("\nDEPENDENCY GATE FAILED\n")
        for f in failures:
            print(f"  {f}")
        return 1
    print("dependency gate: core is stdlib-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
