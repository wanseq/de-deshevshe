"""Зупиняє нічну публікацію при неповному зборі або некоректній збірці."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MIN_CATALOG = {
    "atb_products.json": 1500,
    "silpo_products.json": 15000,
    "fozzy_products.json": 10000,
}
STORES = ("atb", "silpo", "fozzy")


def load_products_js(path):
    content = path.read_text(encoding="utf-8")
    marker = "const PRODUCTS = "
    if marker not in content:
        raise ValueError(f"{path}: немає списку PRODUCTS")
    return json.loads(content.split(marker, 1)[1].rsplit(";", 1)[0])


def validate_raw():
    for name, minimum in MIN_CATALOG.items():
        rows = json.loads(Path(name).read_text(encoding="utf-8"))
        print(f"{name}: {len(rows)} товарів (мінімум {minimum})")
        if len(rows) < minimum:
            raise ValueError(f"Неповний каталог {name}; старий сайт залишається онлайн")


def validate_merged():
    new = load_products_js(Path("../products.js"))
    old = load_products_js(Path("../previous_products.js")) if Path("../previous_products.js").exists() else None
    minimum = max(2000, int(len(old) * 0.6)) if old else 2000
    print(f"products.js: {len(new)} карток (мінімум {minimum})")
    if len(new) < minimum:
        raise ValueError("Замало порівнянних товарів; публікацію зупинено")
    today = datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()
    for row in new:
        available = sum(row.get(f"{store}_raw") is not None and
                        not row.get(f"{store}_out_of_stock") for store in STORES)
        if available < 2:
            raise ValueError(f"Менше двох магазинів у наявності: {row.get('name')}")
        if row.get("updated") != today:
            raise ValueError("Дата даних не відповідає сьогоднішній даті")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--raw", action="store_true")
    mode.add_argument("--merged", action="store_true")
    args = cli.parse_args()
    validate_raw() if args.raw else validate_merged()
