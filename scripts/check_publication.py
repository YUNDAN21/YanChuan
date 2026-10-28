"""Check Git's publication candidates without printing secret values or computing hashes."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "possible_api_token": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})"
    ),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "private_service_url": re.compile(
        r"https?://(?:192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?=[:/\s\"']|$)"
    ),
    "credential_in_url": re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
    "personal_absolute_path": re.compile(
        r"\b[A-Za-z]:[\\/](?:Users[\\/][^\\/\s\"'<>]+|(?:project|programs)[\\/])",
        re.IGNORECASE,
    ),
}
BLOCKED_SUFFIXES = {
    ".exe",
    ".dll",
    ".pdb",
    ".zip",
    ".7z",
    ".gguf",
    ".safetensors",
    ".onnx",
    ".pt",
    ".pth",
    ".ckpt",
    ".wav",
    ".flac",
    ".mp3",
    ".m4a",
    ".mp4",
    ".ogg",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".log",
    ".key",
    ".pem",
    ".pfx",
    ".p12",
}
LOCAL_DIRECTORIES = {".git", ".venv", "data", "models", "work", "dist", "bin", "obj"}
LOCAL_ROOTS = {"plan", "archive"}
MAX_SOURCE_BYTES = 10 * 1024 * 1024


def git_candidates(root):
    """Include tracked files even when a later .gitignore rule would hide them."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    return sorted(set(result.stdout.decode("utf-8").strip("\0").split("\0")) - {""})


def ignored_candidates(root, candidates):
    if not candidates:
        return set()
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "--stdin", "-z"],
        input=("\0".join(candidates) + "\0").encode("utf-8"),
        check=False,
        capture_output=True,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Cannot inspect Git ignore rules")
    return set(result.stdout.decode("utf-8").strip("\0").split("\0")) - {""}


def local_secrets(root):
    """Read only application config credentials; values never enter the report."""
    paths = [root / "data/meeting-settings.json"]
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        paths.extend(
            Path(local_app_data) / name / "data/meeting-settings.json"
            for name in ("LiveCaptions", "YanChuan")
        )
    found = set()

    def collect(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if name in {"api_key", "access_token", "refresh_token", "password"}:
                    if isinstance(item, str) and len(item) >= 8:
                        found.add(item.encode("utf-8"))
                elif isinstance(item, (dict, list)):
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for path in paths:
        if path.is_file():
            collect(json.loads(path.read_text("utf-8")))
    return found


def scan_paths(root, candidates, *, secrets=(), ignored=()):
    root = Path(root).resolve()
    findings, total, scanned = [], 0, 0

    def issue(name, category, line=None):
        value = {"file": name, "category": category}
        if line is not None:
            value["line"] = line
        findings.append(value)

    for name in candidates:
        relative = Path(name)
        path = root / relative
        if relative.is_absolute() or ".." in relative.parts:
            issue(name, "outside_repository")
            continue
        if (
            path.is_symlink()
            or path.is_junction()
            or any(
                parent.is_symlink() or parent.is_junction()
                for parent in path.parents
                if parent != root and parent.is_relative_to(root)
            )
        ):
            issue(name, "linked_file")
            continue
        if not path.is_file():
            issue(name, "missing_candidate")
            continue
        scanned += 1
        size = path.stat().st_size
        total += size
        if name in ignored:
            issue(name, "ignored_file_still_tracked")
        if (
            relative.parts[0] in LOCAL_ROOTS
            or any(part in LOCAL_DIRECTORIES for part in relative.parts)
            or path.suffix.lower() in BLOCKED_SUFFIXES
        ):
            issue(name, "local_or_generated_asset")
        if size > MAX_SOURCE_BYTES:
            issue(name, "oversized_source_file")
            continue
        raw = path.read_bytes()
        if any(secret in raw for secret in secrets):
            issue(name, "known_local_credential")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for category, pattern in PATTERNS.items():
                if pattern.search(line):
                    issue(name, category, number)
    return {
        "passed": not findings,
        "candidate_files": scanned,
        "candidate_bytes": total,
        "findings": findings,
        "file_hash_computation": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--report", type=Path, help="Optional report inside ignored data/verification"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    candidates = git_candidates(root)
    report = scan_paths(
        root,
        candidates,
        secrets=local_secrets(root),
        ignored=ignored_candidates(root, candidates),
    )
    if args.report:
        destination = args.report.resolve()
        if not destination.is_relative_to((root / "data/verification").resolve()):
            raise ValueError("Reports must stay inside data/verification")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
