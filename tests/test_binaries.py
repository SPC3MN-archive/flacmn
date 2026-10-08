import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from iaflac import binaries


class Lookup(unittest.TestCase):
    def test_a_source_checkout_finds_the_tools_fetched_into_packaging_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            exe.write_bytes(b"")
            with mock.patch.object(binaries, "SOURCE_BIN", Path(tmp)), mock.patch.dict(os.environ, {"PATH": ""}), \
                    mock.patch.dict(os.environ, {}, clear=False) as env:
                env.pop("IAFLAC_BIN", None)
                self.assertEqual(binaries.find("ffmpeg"), str(exe))

    def test_iaflac_bin_comes_first(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            (Path(a) / name).write_bytes(b"")
            (Path(b) / name).write_bytes(b"")
            with mock.patch.object(binaries, "SOURCE_BIN", Path(b)), mock.patch.dict(os.environ, {"IAFLAC_BIN": a}):
                self.assertEqual(binaries.find("ffmpeg"), str(Path(a) / name))
