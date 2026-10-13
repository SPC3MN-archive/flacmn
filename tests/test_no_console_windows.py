import ast
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from iaflac import binaries

PACKAGE = Path(__file__).resolve().parent.parent / "iaflac"
# calls that start no console program: macOS's `open` and Linux's `xdg-open` (never run on Windows)
EXEMPT = {("gui/service.py", "open"), ("gui/service.py", "xdg-open")}


class NoConsoleWindows(unittest.TestCase):
    """On Windows each tool the app runs (ffmpeg, flac) must be started without a console window of its own."""

    def test_every_tool_call_passes_no_window(self):
        missing = []
        for path in sorted(PACKAGE.rglob("*.py")):
            rel = path.relative_to(PACKAGE).as_posix()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                        and node.func.attr in ("run", "Popen", "call", "check_call", "check_output")):
                    continue
                first = node.args[0] if node.args else None
                prog = first.elts[0].value if isinstance(first, ast.List) and first.elts and isinstance(first.elts[0], ast.Constant) else None
                if (rel, prog) in EXEMPT:
                    continue
                if not any(k.arg is None and "NO_WINDOW" in ast.unparse(k.value) for k in node.keywords):
                    missing.append(f"{rel}:{node.lineno}")
        self.assertEqual(missing, [], "subprocess calls without **binaries.NO_WINDOW")

    def test_flag_only_on_windows(self):
        if sys.platform == "win32":
            self.assertEqual(binaries.NO_WINDOW, {"creationflags": subprocess.CREATE_NO_WINDOW})
        else:
            self.assertEqual(binaries.NO_WINDOW, {})


if __name__ == "__main__":
    unittest.main()
