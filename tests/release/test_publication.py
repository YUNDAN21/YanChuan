import importlib.util
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_publication.py"
spec = importlib.util.spec_from_file_location("check_publication", SCRIPT)
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


def test_scan_reports_locations_without_exposing_secret_values(tmp_path):
    secret = "sk-" + "A" * 32
    private_url = "http://" + "192.168." + "12.34:8317/v1"
    drive = "C:"
    private_path = f"{drive}/Users/private-person/file.txt"
    document = tmp_path / "example.py"
    document.write_text(f"{secret}\n{private_url}\n{private_path}", "utf-8")
    report = publication.scan_paths(tmp_path, ["example.py"], secrets=[secret.encode()])
    categories = {item["category"] for item in report["findings"]}
    assert categories == {
        "known_local_credential",
        "possible_api_token",
        "private_service_url",
        "personal_absolute_path",
    }
    serialized = json.dumps(report)
    assert (
        secret not in serialized
        and private_url not in serialized
        and private_path not in serialized
    )
    assert not report["file_hash_computation"]


def test_local_assets_rejected_and_localhost_example_allowed(tmp_path):
    (tmp_path / "example.py").write_text("base_url = 'http://127.0.0.1:1234/v1'", "utf-8")
    assert publication.scan_paths(tmp_path, ["example.py"])["passed"]
    (tmp_path / "record.wav").write_bytes(b"recording")
    result = publication.scan_paths(tmp_path, ["record.wav"])
    assert result["findings"][0]["category"] == "local_or_generated_asset"


def test_force_added_private_file_is_not_hidden_by_ignore_rules(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".env.*\n", "utf-8")
    (tmp_path / ".env.local").write_text("PRIVATE_TOKEN=private", "utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", ".env.local"], check=True)
    candidates = publication.git_candidates(tmp_path)
    ignored = publication.ignored_candidates(tmp_path, candidates)
    assert ".env.local" in candidates and ".env.local" in ignored
    result = publication.scan_paths(tmp_path, candidates, ignored=ignored)
    assert any(item["category"] == "ignored_file_still_tracked" for item in result["findings"])


def test_missing_oversized_and_external_candidates_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, "MAX_SOURCE_BYTES", 8)
    (tmp_path / "large.py").write_text("too much source text", "utf-8")
    result = publication.scan_paths(tmp_path, ["missing.py", "large.py", "../outside.py"])
    assert {item["category"] for item in result["findings"]} == {
        "missing_candidate",
        "oversized_source_file",
        "outside_repository",
    }


def test_repository_ignore_rules_exclude_local_state_but_keep_public_examples(tmp_path):
    root = SCRIPT.parents[1]
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text((root / ".gitignore").read_text("utf-8"), "utf-8")
    private = [
        ".env",
        ".env.local",
        "apps/desktop-backend/.env.production",
        "secrets.json",
        "apps/settings.local.json",
        "meeting-settings.json",
        "data/meeting.sqlite3",
        "plan/progress.md",
        "archive/pytorch/uv.lock",
        "models/weights.gguf",
        "runtime/native/win-x64/crispasr.dll",
        "bin/YanChuan.exe",
        "sample.wav",
        "session.log",
        "signing.pfx",
    ]
    public = [
        ".env.example",
        "apps/asr-test/.env.example",
        "src/livecaptions_asr/domain.py",
        "apps/desktop-winui/Assets/AppIcon.png",
        "uv.lock",
    ]
    ignored = publication.ignored_candidates(tmp_path, private + public)
    assert set(private) <= ignored
    assert not set(public) & ignored
