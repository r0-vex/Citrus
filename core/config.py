"""
config.py
=========
Global configuration constants for the Citrus platform.

Any value that other modules need to agree on (app name, version,
display widths, branding, etc.) lives here so it only has to change
in one place as the project grows.
"""

APP_NAME: str = "Citrus"
VERSION: str = "0.3.1"
AUTHOR: str = "You"

# Width used for headers/menus across the whole app, so every screen
# lines up the same way.
MENU_WIDTH: int = 40

ASCII_LOGO: str = r"""
   _____ _ _
  / ____(_) |
 | |    _| |_ _ __ _   _ ___
 | |   | | __| '__| | | / __|
 | |___| | |_| |  | |_| \__ \
  \_____|_|\__|_|   \__,_|___/

        🍋 Citrus Platform 🍋
"""
