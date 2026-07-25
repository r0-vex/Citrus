import os

def clear():
    os.system("clear")

while True:
    clear()

    print("=" * 50)
    print("              🍋 CITRUS OS")
    print("=" * 50)
    print()
    print("1. Dashboard")
    print("2. Services")
    print("3. Bots")
    print("4. Music")
    print("5. Git")
    print("6. Exit")
    print()

    choice = input("Select > ")

    if choice == "6":
        print("\nGoodbye, Captain! 🍋")
        break

    input("\nFeature coming soon... Press Enter.")
