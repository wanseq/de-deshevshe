# atb_scraper.py
#
# РОБОЧИЙ парсер каталогу АТБ, версія 2 - підлаштований під реальну
# розмітку сайту (перевірено через debug_check*.py):
#
#   <div class="catalog-item__title ..."><a href="/product/slug">Назва</a></div>
#   ...
#   <div class="catalog-item__bottom">
#     <div class="... product-price ...">              <- видимий блок ціни
#       <data value="19.95" class="product-price__top">
#         ... <span class="product-price__unit">/кг</span> ...
#       </data>
#       <data value="29.95" class="product-price__bottom">...</data>   <- стара ціна (якщо є знижка)
#     </div>
#     <div hidden class="... product-price ...">...</div>              <- альтернативна ціна, ігноруємо
#   </div>
#
# Запуск:
#   pip3 install -r requirements.txt   (в активному venv)
#   python3 atb_scraper.py

import json
import re
import time
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.atbmarket.com"

CATEGORIES = [
    ("Овочі та фрукти", "/catalog/287-ovochi-ta-frukti"),
    ("Бакалія", "/catalog/285-bakaliya"),
    ("Молочні продукти та яйця", "/catalog/molocni-produkti-ta-ajca"),
    ("Напої безалкогольні", "/catalog/294-napoi-bezalkogol-ni"),
    ("М'ясо", "/catalog/maso"),
    ("Сири", "/catalog/siri"),
    ("Кондитерські вироби", "/catalog/299-konditers-ki-virobi"),
    ("Риба і морепродукти", "/catalog/353-riba-i-moreprodukti"),
    ("Хлібобулочні вироби", "/catalog/325-khlibobulochni-virobi"),
    ("Заморожені продукти", "/catalog/322-zamorozheni-produkti"),
    ("Кава, чай", "/catalog/kava-caj"),
    ("Чипси, снеки", "/catalog/cipsi-sneki"),
    ("Ковбаса і м'ясні делікатеси", "/catalog/360-kovbasa-i-m-yasni-delikatesi"),
    ("Дитяче харчування", "/catalog/339-dityache-kharchuvannya"),
    ("Японська кухня", "/catalog/415-yapons-ka-kukhnya"),
    ("Кулінарія", "/catalog/502-kulinariya"),
    # ("Алкоголь", "/catalog/292-alkogol-i-tyutyun"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9",
}

REQUEST_DELAY_SEC = 1.2
MAX_PAGES_PER_CATEGORY = 60


def to_float(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return None


def find_card(title_div):
    """Піднімаємось від блоку з назвою товару вгору, поки не знайдемо
    контейнер картки - ознака: усередині є блок з ціною (catalog-item__bottom)."""
    node = title_div
    for _ in range(6):
        node = node.parent
        if node is None:
            return None
        if node.find(class_="catalog-item__bottom"):
            return node
    return None


def extract_price(card):
    """Повертає (ціна, стара_ціна, одиниця) з першого НЕ прихованого блоку ціни."""
    bottom = card.find(class_="catalog-item__bottom")
    if not bottom:
        return None, None, ""

    price_blocks = bottom.find_all(
        "div", class_=lambda c: c and "product-price" in c.split() and "catalog-item__product-price" in c.split()
    )
    # Якщо клас трохи інший на якихось товарах - беремо будь-який div, що
    # містить всередині <data class="product-price__top">, і не прихований.
    if not price_blocks:
        price_blocks = [
            d for d in bottom.find_all("div")
            if d.find("data", class_="product-price__top")
        ]

    visible_block = None
    for b in price_blocks:
        if not b.has_attr("hidden"):
            visible_block = b
            break
    if visible_block is None and price_blocks:
        visible_block = price_blocks[0]
    if visible_block is None:
        return None, None, ""

    top = visible_block.find("data", class_="product-price__top")
    bottom_data = visible_block.find("data", class_="product-price__bottom")

    price = to_float(top.get("value")) if top else None
    old_price = to_float(bottom_data.get("value")) if bottom_data else None

    unit = ""
    if top:
        unit_span = top.find(class_="product-price__unit")
        if unit_span:
            unit_text = unit_span.get_text(strip=True).lstrip("/")
            if unit_text == "кг":
                unit = "за кг"
            elif unit_text == "л":
                unit = "за л"

    return price, old_price, unit


def parse_category_page(html, category_name):
    soup = BeautifulSoup(html, "lxml")
    products = []
    seen_urls = set()

    for title_div in soup.find_all(class_="catalog-item__title"):
        link = title_div.find("a", href=True)
        if not link:
            continue
        name = link.get_text(strip=True)
        if not name:
            continue
        href = link["href"]
        url = href if href.startswith("http") else BASE_URL + href
        if url in seen_urls:
            continue

        card = find_card(title_div)
        if card is None:
            continue

        price, old_price, unit = extract_price(card)
        if price is None:
            continue

        img = card.find("img")
        image = ""
        if img:
            image = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            if image and image.startswith("/"):
                image = BASE_URL + image

        card_text = card.get_text(separator=" ", strip=True)
        out_of_stock = "Немає в наявності" in card_text

        discount_percent = None
        if old_price and price and old_price > price:
            discount_percent = round((1 - price / old_price) * 100)

        seen_urls.add(url)
        products.append({
            "name": name,
            "unit": unit,
            "category": category_name,
            "price": price,
            "old_price": old_price,
            "discount_percent": discount_percent,
            "out_of_stock": out_of_stock,
            "image": image,
            "url": url,
        })

    return products


def fetch_category(name, path):
    all_products = []
    seen_urls = set()

    for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
        url = f"{BASE_URL}{path}"
        params = {"page": page} if page > 1 else {}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=20)

        if resp.status_code != 200:
            if page == 1 or resp.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"АТБ, {name}, сторінка {page}: HTTP {resp.status_code}")
            print(f"    сторінка {page}: HTTP {resp.status_code}, зупиняюсь")
            break

        products = parse_category_page(resp.text, name)
        new_products = [p for p in products if p["url"] not in seen_urls]

        if not new_products:
            if page == 1:
                raise RuntimeError(f"АТБ, {name}: перша сторінка не містить товарів")
            print(f"    сторінка {page}: нових товарів немає, категорію зібрано")
            break

        for p in new_products:
            seen_urls.add(p["url"])
        all_products.extend(new_products)

        print(f"    сторінка {page}: +{len(new_products)} товарів (всього {len(all_products)})")
        time.sleep(REQUEST_DELAY_SEC)

    return all_products


def main():
    all_items = []
    for name, path in CATEGORIES:
        print(f"Категорія: {name}")
        items = fetch_category(name, path)
        print(f"  разом у категорії: {len(items)}")
        all_items.extend(items)

    with open("atb_products.json", "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"\nГотово: {len(all_items)} товарів збережено в atb_products.json")


if __name__ == "__main__":
    main()
