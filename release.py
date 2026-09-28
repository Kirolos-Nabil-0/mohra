#!/usr/bin/env python3
"""Bump Mohra's version, commit the release metadata, and push it to GitHub.

GitHub Actions builds the Windows packages and publishes the GitHub Release
after this script pushes to main or master.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "version.json"
INSTALLER_FILE = ROOT / "installer.iss"
VERSION_PATTERN = re.compile(r'(?m)^(#define MyAppVersion ")[^"]+("\s*)$')
SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def require_release_ready() -> str:
    branch = git("branch", "--show-current")
    if branch not in {"main", "master"}:
        raise RuntimeError(f"Release from main or master; current branch is {branch!r}.")

    if git("diff", "--cached", "--name-only"):
        raise RuntimeError("The Git index has staged changes. Commit or unstage them before releasing.")

    changed_release_files = git(
        "status", "--porcelain", "--", "version.json", "installer.iss"
    )
    if changed_release_files:
        raise RuntimeError(
            "version.json or installer.iss already has local changes. Commit or restore those files before releasing."
        )

    if not git("remote", "get-url", "origin", check=False):
        raise RuntimeError("Git remote 'origin' is not configured.")
    return branch


def next_version(current: str, requested: str) -> str:
    match = SEMVER_PATTERN.fullmatch(current)
    if not match:
        raise RuntimeError(f"version.json must contain a stable x.y.z version; got {current!r}.")

    major, minor, patch = map(int, match.groups())
    if requested == "patch":
        patch += 1
        return f"{major}.{minor}.{patch}"
    if requested == "minor":
        return f"{major}.{minor + 1}.0"
    if requested == "major":
        return f"{major + 1}.0.0"
    if not SEMVER_PATTERN.fullmatch(requested):
        raise RuntimeError("Choose patch, minor, major, or provide a stable version like 1.2.3.")
    if tuple(map(int, requested.split("."))) <= (major, minor, patch):
        raise RuntimeError(f"New version {requested} must be greater than current version {current}.")
    return requested


def ensure_tag_is_new(version: str) -> None:
    tag = f"v{version}"
    if git("tag", "--list", tag):
        raise RuntimeError(f"Tag {tag} already exists locally.")
    remote_tag = git("ls-remote", "--tags", "origin", f"refs/tags/{tag}")
    if remote_tag:
        raise RuntimeError(f"Tag {tag} already exists on origin.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump Mohra's version, commit the release, and push it to GitHub."
    )
    parser.add_argument(
        "version",
        nargs="?",
        default="patch",
        help="version increment: patch (default), minor, major, or a higher x.y.z version",
    )
    parser.add_argument(
        "--set",
        dest="explicit_version",
        metavar="X.Y.Z",
        help="set a specific higher stable version, for example --set 2.0.0",
    )
    args = parser.parse_args()

    try:
        branch = require_release_ready()
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        current = str(data.get("version", ""))
        if args.explicit_version and args.version != "patch":
            raise RuntimeError("Use either a version increment or --set, not both.")
        requested = args.explicit_version or args.version
        new_version = next_version(current, requested)
        ensure_tag_is_new(new_version)

        data["version"] = new_version
        data["release_date"] = date.today().isoformat()
        VERSION_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        installer_text = INSTALLER_FILE.read_text(encoding="utf-8")
        updated_text, replacements = VERSION_PATTERN.subn(
            rf'\g<1>{new_version}\g<2>', installer_text, count=1
        )
        if replacements != 1:
            raise RuntimeError("Could not find the installer version in installer.iss.")
        INSTALLER_FILE.write_text(updated_text, encoding="utf-8")

        git("add", "--", "release.py", "version.json", "installer.iss")
        git("commit", "-m", f"chore(release): v{new_version}")
        git("push", "origin", branch)

        print(f"Pushed Mohra v{new_version} to origin/{branch}.")
        print("GitHub Actions will build the Windows packages and publish the release.")
        return 0
    except (OSError, json.JSONDecodeError, RuntimeError) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
