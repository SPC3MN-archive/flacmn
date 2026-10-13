import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from iaflac.gui import app


class NoConsole(unittest.TestCase):
    """A windowed Windows build has no stdout/stderr; the app must still start its server."""

    def test_missing_streams_go_to_a_log_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None), \
                mock.patch("iaflac.paths.data_dir", return_value=Path(tmp)):
            app.give_streams()
            self.assertIsNotNone(sys.stdout)
            self.assertIs(sys.stderr, sys.stdout)
            self.assertFalse(sys.stdout.isatty())                   # what uvicorn's formatter asks
            sys.stdout.close()
            self.assertTrue((Path(tmp) / "flacmn.log").exists())

    def test_server_starts_without_a_console(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None), \
                mock.patch("iaflac.paths.data_dir", return_value=Path(tmp)):
            app.give_streams()
            try:
                server = app.start_server(app.free_port(), "t")
                self.assertTrue(server.started)
                server.should_exit = True
            finally:
                sys.stdout.close()

    def test_streams_left_alone_with_a_console(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            app.give_streams()
            self.assertIs(sys.stdout, out)
            self.assertIs(sys.stderr, err)


if __name__ == "__main__":
    unittest.main()
