"""
core.logging
============
Citrus's logging and event-bus package.

Import note:
    This package is deliberately named "logging" to match the
    required project layout. That name would shadow Python's own
    standard library "logging" module if this package's own
    directory (core/logging/) ever ended up directly on sys.path -
    which happens automatically whenever a script is run with that
    directory as its working/script directory.

    core/citrus.py strips core/ from sys.path at startup, before any
    other Citrus module is imported, specifically to prevent this.
    See citrus.py for the full explanation. Do not import anything
    from this package before that bootstrap has run.
"""
