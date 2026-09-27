# silpo_scraper.py
#
# РОБОЧИЙ парсер каталогу Сільпо (не шаблон) - за тим самим принципом,
# що і atb_scraper.py: сторінки категорій silpo.ua віддають готовий HTML
# з цінами прямо в тексті картки товару, гортання сторінок працює через
# ?page=2, ?page=3 і т.д.
#
# ВАЖЛИВО про ціни й місто: так само, як з АТБ - ці сторінки не
# прив'язані до конкретного міста/магазину напряму із запиту. Сільпо
# показує загальні (мережеві) ціни; локальні акції окремого магазину
# можуть відрізнятись. Порівняйте кілька цін із тим, що бачите в
# застосунку після вибору "свого" магазину - якщо схоже, все гаразд.
#
# Запуск:
#   pip3 install -r requirements.txt
#   python3 silpo_scraper.py

import json
import re
import time
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://silpo.ua"

CATEGORIES = [
    ("Фрукти, овочі", "/category/frukty-ovochi-4788"),
    ("Бакалія і консерви", "/category/bakaliia-i-konservy-4870"),
    ("Молочні продукти та яйця", "/category/molochni-produkty-ta-iaitsia-234"),
    ("Напої", "/category/napoi-52"),
    ("М'ясо", "/category/m-iaso-4411"),
    ("Сири", "/category/syry-1468"),
    ("Солодощі", "/category/solodoshchi-498"),
    ("Риба", "/category/ryba-4430"),
    ("Хліб та випічка", "/category/khlib-ta-vypichka-5121"),
    ("Заморожена продукція", "/category/zamorozhena-produktsiia-264"),
    ("Кава, чай", "/category/kava-chai-359"),
    ("Снеки та чипси", "/category/sneky-ta-chypsy-5016"),
    ("Ковбаси і м'ясні делікатеси", "/category/kovbasni-vyroby-i-m-iasni-delikatesy-4731"),
    ("Дитяче харчування", "/category/dytiache-kharchuvannia-4676"),
    ("Готові страви і кулінарія", "/category/gotovi-stravy-i-kulinariia-4761"),
    ("Соуси і спеції", "/category/sousy-i-spetsii-4938"),
    # ("Алкоголь", "/category/alkogol-22"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://silpo.ua/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

REQUEST_DELAY_SEC = 1.2
MAX_PAGES_PER_CATEGORY = 60

# "69.90 грн 109.00 грн - 36%"  -> ціна, стара ціна, знижка
DISCOUNT_PRICE_RE = re.compile(
    r"(\d+[.,]\d+)\s*грн\s+(\d+[.,]\d+)\s*грн\s*-\s*(\d+)\s*%"
)
# просто "69.90 грн" (без знижки, або з "Роздріб ..." після - ігноруємо решту)
SIMPLE_PRICE_RE = re.compile(r"(\d+[.,]\d+)\s*грн")

OUT_OF_STOCK_MARKERS = [
    "Немає в наявності",
    "Товар відсутній",
    "тимчасово відсутній",
    "Тимчасово відсутній",
]


def to_float(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return None


def parse_category_page(html, category_name):
    soup = BeautifulSoup(html, "lxml")
    products = []
    seen_urls = set()

    # Кожна картка товару - посилання на /product/...
    links = soup.find_all("a", href=re.compile(r"/product/"))

    for a in links:
        href = a.get("href", "")
        url = href if href.startswith("http") else BASE_URL + href
        if url in seen_urls:
            continue

        card_text = a.get_text(separator=" ", strip=True)
        if "грн" not in card_text:
            # Це посилання не є карткою товару з ціною (може бути банер
            # чи щось інше) - пропускаємо.
            continue

        img = a.find("img")
        name = None
        if img and img.get("alt"):
            name = img["alt"].strip()
        if not name:
            # Резервний варіант: перше "речення" тексту картки, без
            # ціни і рейтингу - не ідеально, але краще ніж нічого.
            cleaned = SIMPLE_PRICE_RE.sub("", card_text)
            name = cleaned.strip()[:120]
        if not name:
            continue

        image = ""
        if img:
            image = img.get("src") or img.get("data-src") or ""
            if image and image.startswith("/"):
                image = BASE_URL + image

        m = DISCOUNT_PRICE_RE.search(card_text)
        if m:
            price = to_float(m.group(1))
            old_price = to_float(m.group(2))
            discount = int(m.group(3))
        else:
            m2 = SIMPLE_PRICE_RE.search(card_text)
            if not m2:
                continue
            price = to_float(m2.group(1))
            old_price = None
            discount = None

        out_of_stock = any(marker in card_text for marker in OUT_OF_STOCK_MARKERS)

        seen_urls.add(url)
        products.append({
            "name": name,
            "unit": "",
            "category": category_name,
            "price": price,
            "old_price": old_price,
            "discount_percent": discount,
            "out_of_stock": out_of_stock,
            "image": image,
            "url": url,
        })

    return products


def fetch_category(session, name, path):
    all_products = []
    seen_urls = set()

    for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
        url = f"{BASE_URL}{path}"
        params = {"page": page} if page > 1 else {}
        resp = session.get(url, params=params, timeout=20)

        if resp.status_code != 200:
            if page == 1 or resp.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"Сільпо, {name}, сторінка {page}: HTTP {resp.status_code}")
            print(f"    сторінка {page}: HTTP {resp.status_code}, зупиняюсь")
            break

        products = parse_category_page(resp.text, name)
        new_products = [p for p in products if p["url"] not in seen_urls]

        if not new_products:
            if page == 1:
                raise RuntimeError(f"Сільпо, {name}: перша сторінка не містить товарів")
            print(f"    сторінка {page}: нових товарів немає, категорію зібрано")
            break

        for p in new_products:
            seen_urls.add(p["url"])
        all_products.extend(new_products)

        print(f"    сторінка {page}: +{len(new_products)} товарів (всього {len(all_products)})")
        time.sleep(REQUEST_DELAY_SEC)

    return all_products


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    # "Прогрів": спершу заходимо на головну сторінку як звичайний браузер,
    # щоб отримати куки сесії - деякі сайти (в т.ч. схоже і Сільпо)
    # повертають 403 на прямий запит категорії без цього кроку.
    warm = session.get(BASE_URL, timeout=20)
    warm.raise_for_status()
    print(f"Прогрів головної сторінки: HTTP {warm.status_code}, отримано {len(session.cookies)} кук")
    time.sleep(REQUEST_DELAY_SEC)
    return session


def main():
    session = make_session()

    all_items = []
    for name, path in CATEGORIES:
        print(f"Категорія: {name}")
        items = fetch_category(session, name, path)
        print(f"  разом у категорії: {len(items)}")
        all_items.extend(items)

    with open("silpo_products.json", "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"\nГотово: {len(all_items)} товарів збережено в silpo_products.json")


if __name__ == "__main__":
    main()
