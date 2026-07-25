"""
Citrus core package.

Groups the application's control logic (entry point, menu, and
feature modules) separately from data folders like bots/, services/,
media/, logs/, etc. This package is intended to be run directly
(e.g. ``python citrus.py`` from inside core/) during early
development; a proper ``python -m`` package launcher can be added
later without touching any module internals.
"""
