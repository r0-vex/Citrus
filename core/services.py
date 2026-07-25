from utils import clear_screen, print_header, pause


def services_menu() -> None:
    """Display the service manager screen and wait for Enter."""
    clear_screen()
    print_header("Service Manager")
    print("\nCurrently no services available.\n")
    pause()
