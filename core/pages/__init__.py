"""
core.pages
==========
Every selectable page in Citrus's main menu. Each page exposes a
single public show() function; menu.py imports and dispatches to
these, never their internals. Pages implement behaviour - menu.py
only implements navigation.
"""
