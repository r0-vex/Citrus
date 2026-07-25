from utils import clear_screen, print_header, pause, command_exists, process_running


def dashboard() -> None:

    clear_screen()
    print_header("Dashboard")

    checks: dict[str, bool] = {
        "SSH Running": process_running("sshd"),
        "Python Installed": command_exists("python") or command_exists("python3"),
        "Git Installed": command_exists("git"),
        "Zsh Installed": command_exists("zsh"),
    }

    print("\nStatus:")
    for label, is_ok in checks.items():
        symbol = "✅" if is_ok else "❌"
        print(f"  {symbol} {label}")

    pause()
