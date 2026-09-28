# merge.py
#
# Об'єднує каталоги АТБ, Сільпо та Фоззі в products.js для сайту.
#
# Запуск (після того, як усі три скрипти відпрацювали):
#   python merge.py

import json
import os
from catalog_state import verified_metadata, STORES
from common import merge_three_products, write_products_js


def load(path):
    if not os.path.exists(path):
        print(f"Файл {path} не знайдено - спершу запустіть відповідний скрапер.")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    metadata = verified_metadata()
    atb_items = load("atb_products.json")
    silpo_items = load("silpo_products.json")
    fozzy_items = load("fozzy_products.json")

    if not all((atb_items, silpo_items, fozzy_items)):
        print("Потрібні всі три непорожні JSON-файли. Збірку не змінено.")
        return

    print(f"АТБ: {len(atb_items)}, Сільпо: {len(silpo_items)}, Фоззі: {len(fozzy_items)} товарів")
    print("Зіставляю товари (це може зайняти хвилину-дві на великих обсягах)...")
    entries = merge_three_products(atb_items, silpo_items, fozzy_items, threshold=0.6)
    print(f"Зіставлено {len(entries)} товарів, кожен у наявності щонайменше у двох магазинах.")

    for entry in entries:
        dates = {}
        for store in STORES:
            if entry.get(f"{store}_raw") is not None:
                dates[store] = metadata[store]["collected_on"]
        entry["store_updated"] = dates
        entry["updated"] = min(dates.values())
    write_products_js(entries, path="../products.js")
    print("Готово. Відкрийте (або перезавантажте) ../index.html у браузері.")


if __name__ == "__main__":
    main()
