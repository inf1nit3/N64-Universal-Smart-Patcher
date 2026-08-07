"""PyInstaller entry stub for the GUI executable.

PyInstaller needs a script path, not a console-script entry point, so the
frozen build starts here and immediately hands over to the package.
"""
from n64patcher.gui import main

if __name__ == "__main__":
    main()
