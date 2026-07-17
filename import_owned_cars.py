import csv
import json
import os


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
    target = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "owned_cars.json")
    data = {"owned": list(dict.fromkeys(cars))}
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4)
        os.replace(tmp, target)
    except (OSError, PermissionError, TypeError, ValueError):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return target
