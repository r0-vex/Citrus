"""
citrus.py
=========
Entry point for the Citrus platform.

Deliberately contains almost no logic beyond a small, necessary
sys.path bootstrap - see the note below - before handing off to
core.app.run(). Keeping this file tiny means the "front door" of the
app never has to change as features are added.

Import bootstrap note:
    Citrus's logging package lives at core/logging/, which would
    shadow Python's own standard library "logging" module if core/
    itself ever ended up directly on sys.path - which Python does
    automatically whenever a script is run with that directory as its
    script directory (e.g. `python citrus.py` from inside core/, or
    `python core/citrus.py` from the project root). The block below
    strips core/ from sys.path and ensures the project root is
    present instead, so "core.xxx" imports resolve correctly
    everywhere and "import logging" inside core/logging/logger.py
    always finds the real standard library module. This runs once,
    before any other Citrus module is imported.

Supported ways to launch Citrus, all equivalent:
    python -m core.citrus        (from the project root)
    python core/citrus.py        (from the project root)
    python citrus.py             (from inside core/)
"""

import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _CORE_DIR.parent


def _fix_sys_path(core_dir: Path, project_root: Path) -> None:
    """Remove core/ from sys.path and ensure the project root is on it.

    Args:
        core_dir: Absolute path to this file's own directory (core/).
        project_root: Absolute path to the project root (core/'s parent).
    """
    cleaned = []
    for entry in sys.path:
        try:
            if Path(entry).resolve() == core_dir:
                continue
        except OSError:
            pass
        cleaned.append(entry)
    sys.path[:] = cleaned

    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


_fix_sys_path(_CORE_DIR, _PROJECT_ROOT)

from core.exceptions import ConfigurationError  # noqa: E402  (must follow the sys.path bootstrap)


def main() -> None:
    """Start the Citrus application."""
    try:
        from core.app import run
    except ConfigurationError as exc:
        print(f"Fatal configuration error: {exc}", file=sys.stderr)
        sys.exit(1)
        return

    run()


if __name__ == "__main__":
    main()
