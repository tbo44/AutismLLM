"""Regression checks for glossary updates in an already-open chat."""
import subprocess
from pathlib import Path


def test_acronym_refresh():
    runner = Path(__file__).with_name("acronym_refresh_runner.cjs")
    result = subprocess.run(
        ["node", str(runner)], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr