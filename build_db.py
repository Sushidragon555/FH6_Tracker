import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MASTER_FILE = os.path.join(BASE_DIR, "fh6_master_list.json")
OWNED_FILE = os.path.join(BASE_DIR, "owned_cars.json")


def check_files():
    if not os.path.exists(MASTER_FILE):
        print("Error: Missing 'fh6_master_list.json' in this folder! Run build_db.py first.")
        return False
    if not os.path.exists(OWNED_FILE):
        print("Error: Missing 'owned_cars.json' in this folder!")
        return False
    return True


def format_credits(amount):
    """Formats large credit numbers into clean human-readable M or K strings."""
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M CR"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}K CR"
    return f"{amount} CR"


def run_calculation():
    if not check_files():
        return

    with open(MASTER_FILE, "r", encoding="utf-8") as handle:
        master_db = json.load(handle)
    with open(OWNED_FILE, "r", encoding="utf-8") as handle:
        owned_data = json.load(handle)

    owned_set = set(owned_data.get("owned", []))
    unowned_cars = []
    total_cost = 0

    for car, price in master_db.items():
        if car not in owned_set:
            unowned_cars.append((car, price))
            total_cost += price

    print("\n" + "=" * 75)
    print("                FORZA HORIZON 6 UNOWNED VEHICLES TRACKER")
    print("=" * 75)

    if not unowned_cars:
        print(" [+] Status 100%: Outstanding! You own every vehicle on the master list.")
    else:
        for car, price in sorted(unowned_cars, key=lambda item: item[1], reverse=True):
            price_display = format_credits(price) if price > 0 else "Exclusive / 0 CR"
            print(f"[-] {car:<52} | Price: {price_display}")

        print("=" * 75)
        print(f" [>] Total Unowned Cars Left to Collect:  {len(unowned_cars)}")
        print(f" [>] Remaining Autoshow Bankroll Needed:  {format_credits(total_cost)}")
        print("=" * 75 + "\n")


if __name__ == "__main__":
    run_calculation()