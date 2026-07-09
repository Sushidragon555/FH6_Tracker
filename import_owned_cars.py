import csv
import json
import os
import sys

base_dir = os.path.dirname(os.path.abspath(__file__))
owned_file = os.path.join(base_dir, "owned_cars.json")


def normalize_car_entry(entry):
    cleaned = entry.strip().strip(",; ")
    return cleaned


def parse_owned_cars_text(text):
    if not text:
        return []

    items = []
    for line in text.splitlines():
        if not line.strip():
            continue
        for part in next(csv.reader([line], skipinitialspace=True)):
            cleaned = normalize_car_entry(part)
            if cleaned:
                items.append(cleaned)
    return items


def load_owned_cars_from_file(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        return parse_owned_cars_text(handle.read())


def save_owned_cars(cars, path=None):
    target = path or owned_file
    data = {"owned": list(dict.fromkeys(cars))}
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4)
    return target


def main():
    if len(sys.argv) > 1:
        input_path = sys.argv[1]
        if os.path.isfile(input_path):
            cars = load_owned_cars_from_file(input_path)
            save_owned_cars(cars)
            print(f"Imported {len(cars)} cars from {input_path}.")
            return
        cars = parse_owned_cars_text(input_path)
        save_owned_cars(cars)
        print(f"Imported {len(cars)} cars from pasted text.")
        return

    default_cars = [
        "2000 Nissan Skyline GT-R V-Spec II",
        "2019 Zenvo TSR-S",
        "2023 Acura Integra A-Spec",
    ]
    save_owned_cars(default_cars)
    print("Saved default sample cars.")


if __name__ == "__main__":
    main()
