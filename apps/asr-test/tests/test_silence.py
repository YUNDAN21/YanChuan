import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", ["verify_live.py", "verify_long_audio.py"])
def test_audible_device_tests_require_explicit_opt_in(script, tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts" / script
    result = subprocess.run(
        [sys.executable, str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2 and "verify_library.py" in result.stderr
    assert list(tmp_path.iterdir()) == []
