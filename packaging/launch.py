"""Entry point for the packaged app (PyInstaller needs a script, not a module path)."""
import multiprocessing
import sys

from iaflac.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
