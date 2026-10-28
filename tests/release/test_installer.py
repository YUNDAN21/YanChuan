import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("package_installer", SCRIPTS / "package_installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
sys.path.remove(str(SCRIPTS))


def payload(tmp_path):
    package = tmp_path / "程序 包"
    package.mkdir()
    marker = {"version": "0.1.0", "native_runtime": "backend/native/crispasr.dll"}
    (package / "yanchuan.runtime.json").write_text(json.dumps(marker), "utf-8")
    (package / "YanChuan.exe").write_bytes(b"fixture")
    (package / "backend").mkdir()
    (package / "backend/runtime.dll").write_bytes(b"runtime")
    manifest = installer.audit_release(package, [], native_only=True)
    (package / "release-manifest.json").write_text(json.dumps(manifest), "utf-8")
    return package


def test_installer_only_embeds_exact_audited_files_and_uninstalls_exact_paths(tmp_path):
    package = payload(tmp_path)
    marker, audited = installer.verified_payload(package, [])
    include = installer.payload_include(package, tmp_path / "setup.exe", marker, audited)
    assert 'Delete "$INSTDIR\\YanChuan.exe"' in include
    assert 'Delete "$INSTDIR\\backend\\runtime.dll"' in include
    assert "RMDir /r" not in include and "File /r" not in include
    assert "*" not in include
    assert "程序 包" in include


def test_modified_payload_and_new_unlisted_files_are_rejected(tmp_path):
    package = payload(tmp_path)
    (package / "YanChuan.exe").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="manifest"):
        installer.verified_payload(package, [])


def test_installer_rescans_credentials_even_when_manifest_matches(tmp_path):
    package = payload(tmp_path)
    with pytest.raises(ValueError, match="credentials"):
        installer.verified_payload(package, [b"fixture"])


def test_nsis_literals_cannot_expand_variables_or_insert_lines():
    assert installer.nsis_string('path$INSTDIR"') == 'path$$INSTDIR$\\"'
    with pytest.raises(ValueError):
        installer.nsis_string("safe\nDelete dangerous")


def test_installer_branding_and_old_install_upgrade_identity():
    source = (SCRIPTS.parent / "installer/YanChuan.nsi").read_text("utf-8")
    assert 'Name "言川"' in source
    assert '"DisplayName" "言川"' in source
    assert "$INSTDIR\\YanChuan.exe" in source
    assert "$SMPROGRAMS\\言川\\言川.lnk" in source
    assert 'InstallDirRegKey HKCU "Software\\LiveCaptions" "InstallDir"' in source
    assert 'CheckFileUnlocked "$INSTDIR\\LiveCaptions.exe"' in source
    assert 'Delete "$INSTDIR\\LiveCaptions.exe"' in source
    assert "RMDir /r" not in source
