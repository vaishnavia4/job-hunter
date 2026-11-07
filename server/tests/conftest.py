"""Run the script-style tests (tests/test_*.py with a main() / __main__ block) under pytest.

Each such file is collected as ONE item that runs `python <file>` in a subprocess and passes when it exits 0,
so the scripts keep working standalone and their own check() bookkeeping is respected. Files written in plain
pytest style (no __main__ block) are collected normally.
"""
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]


def _is_script(path):
    try:
        return "__name__ == \"__main__\"" in Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


class ScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name=self.path.stem)


class ScriptItem(pytest.Item):
    def runtest(self):
        r = subprocess.run([sys.executable, str(self.path)], cwd=SERVER, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, check=False)
        self.output = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            raise ScriptFailed(r.returncode, self.output)

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ScriptFailed):
            return f"{self.path.name} exited {excinfo.value.args[0]}\n" + excinfo.value.args[1][-4000:]
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"script: {self.path.name}"


class ScriptFailed(Exception):
    pass


def pytest_pycollect_makemodule(module_path, parent):
    if module_path.name.startswith("test_") and _is_script(module_path):
        return ScriptFile.from_parent(parent, path=module_path)
    return None
