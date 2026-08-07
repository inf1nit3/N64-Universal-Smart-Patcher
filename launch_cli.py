"""PyInstaller entry stub for the CLI executable.

PyInstaller needs a script path, not a console-script entry point, so the
frozen build starts here and immediately hands over to the package.
"""
import sys

from n64patcher.cli import main

if __name__ == "__main__":
    sys.exit(main())
