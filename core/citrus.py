"""
citrus.py
=========
Entry point for the Citrus platform.

Deliberately contains almost no logic - it just starts the main
menu loop defined in menu.py. Keeping this file tiny means the
"front door" of the app never has to change as features are added.
"""

from menu import main_loop


def main() -> None:
    """Start the Citrus application."""
    main_loop()


if __name__ == "__main__":
    main()
