"""
GawdBotE backup — create and verify local state archives.
Inspired by OpenClaw's `openclaw backup` command.
Usage:
  python -m cli.backup create [--no-data]
  python -m cli.backup verify <archive.tar.gz>
"""
from __future__ import annotations
import hashlib
import json
import sys
import tarfile
import time
from pathlib import Path

import config

BACKUP_DIR = config.PROJECT_ROOT / "backups"


def _manifest(paths: list[Path]) -> dict:
    files = {}
    for p in paths:
        if p.is_file():
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            files[str(p.relative_to(config.PROJECT_ROOT))] = h
    return {"created": time.time(), "files": files}


def create(include_data: bool = True) -> Path:
    """Create a .tar.gz backup of the project."""
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    archive_path = BACKUP_DIR / f"gawdbote-backup-{ts}.tar.gz"

    exclude = {".git", "__pycache__", "backups", ".venv", "venv", "node_modules"}
    if not include_data:
        exclude.add("data")

    collected: list[Path] = []
    for p in config.PROJECT_ROOT.rglob("*"):
        parts = set(p.relative_to(config.PROJECT_ROOT).parts)
        if parts & exclude:
            continue
        if p.is_file():
            collected.append(p)

    manifest = _manifest(collected)

    with tarfile.open(archive_path, "w:gz") as tar:
        for p in collected:
            tar.add(p, arcname=str(p.relative_to(config.PROJECT_ROOT)))
        # Embed manifest
        import io
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(name=".gawdbote-manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

    print(f"Backup created: {archive_path}")
    print(f"  Files: {len(collected)}")
    print(f"  Size:  {archive_path.stat().st_size / 1024:.1f} KB")
    return archive_path


def verify(archive_path: str) -> bool:
    """Verify a backup archive integrity."""
    path = Path(archive_path)
    if not path.exists():
        print(f"Archive not found: {archive_path}")
        return False

    with tarfile.open(path, "r:gz") as tar:
        try:
            manifest_file = tar.extractfile(".gawdbote-manifest.json")
            manifest = json.loads(manifest_file.read())
        except KeyError:
            print("No manifest found in archive — cannot verify")
            return False

        print(f"Backup created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(manifest['created']))}")
        print(f"Files in manifest: {len(manifest['files'])}")

        errors = 0
        for rel_path, expected_hash in manifest["files"].items():
            try:
                member = tar.extractfile(rel_path)
                if member is None:
                    print(f"  MISSING: {rel_path}")
                    errors += 1
                    continue
                actual_hash = hashlib.sha256(member.read()).hexdigest()
                if actual_hash != expected_hash:
                    print(f"  CORRUPT: {rel_path}")
                    errors += 1
            except KeyError:
                print(f"  MISSING: {rel_path}")
                errors += 1

    if errors:
        print(f"\nVerification FAILED: {errors} error(s)")
        return False
    else:
        print("\nVerification PASSED: all files intact")
        return True


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python -m cli.backup create [--no-data]")
        print("  python -m cli.backup verify <archive.tar.gz>")
        return

    cmd = args[0]
    if cmd == "create":
        include_data = "--no-data" not in args
        create(include_data=include_data)
    elif cmd == "verify":
        if len(args) < 2:
            print("Usage: python -m cli.backup verify <archive.tar.gz>")
            return
        ok = verify(args[1])
        sys.exit(0 if ok else 1)
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
