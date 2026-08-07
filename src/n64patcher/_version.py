"""Single source of truth for the version.

Read by __init__ (__version__), n64_core (VERSION, shown in the GUI title
and `--version`) and pyproject.toml (dynamic metadata), so a release only
has to change it here.
"""

__version__ = "3.3.1"
