"""Збір звичайних роздрібних цін FOZZY у fozzy_products.json.

    pip3 install -r requirements.txt
    python3 fozzy_scraper.py

Ціна «від N од» — оптова; для порівняння беремо data-main-price
картки товару (акційну роздрібну ціну, якщо є акція).
"""

import argparse
import asyncio
import json
from urllib.parse import urljoin

import aiohttp
from lxml import html

BASE_URL = "https://fozzyshop.ua"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
           "Accept-Language": "uk-UA,uk;q=0.9"}

# Великі продуктові розділи, які перетинаються з каталогами АТБ і Сільпо.
CATEGORIES = [
    ("Напої", "/3651-bezalkogolni-napoyi"),
    ("Кава, чай", "/3660-chay-kava"),
    ("Бакалія", "/3667-bakaliya-konservatsiya"),
    ("Соуси і спеції", "/3646-sousy-spetsiyi-roslynna-oliya"),
    ("Солодощі і снеки", "/3644-solodoshchi-sneky"),
    ("М'ясо", "/3647-m-yaso-ptytsya"),
    ("Риба", "/3666-ryba-moreprodukty"),
    ("Овочі та фрукти", "/3668-ovochi-frukty-gryby"),
    ("Дитяче харчування", "/3645-tovary-dlya-ditey"),
    ("Молочні продукти та яйця", "/3650-molochna-produktsiya-syr-yaytsya"),
    ("Ковбаси", "/3665-kovbasa-m-yasni-vyroby"),
    ("Заморожені продукти", "/3661-zamorozheni-produkty"),
    ("Хліб та випічка", "/3664-khlibobulochni-vyroby"),
    ("Готові страви", "/3643-kulinariya"),
    ("Японська кухня", "/3708-aziatska-kukhnya"),
]

CARD_XPATH = '//div[contains(concat(" ", normalize-space(@class), " "), " product-mini-card ")]'


def parse_page(document, category):
    """Повертає товари та номер останньої сторінки категорії."""
    tree = html.fromstring(document)
    pages = [int(text.strip()) for text in tree.xpath('//div[contains(@class,"pagination")]//a/text()')
             if text.strip().isdigit()]
    products = []
    for card in tree.xpath(CARD_XPATH):
        price_block = card.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," product_mini_prices_block ")]')
        raw_price = (price_block[0].get("data-main-price") if price_block else None) or card.get("data-price")
        try:
            price = float(raw_price.replace(",", "."))
        except (AttributeError, ValueError):
            continue
        name = (card.get("data-product-name") or "").strip()
        urls = card.xpath('.//div[contains(@class,"product_mini_name")]//a/@href')
        if not name or not urls or price <= 0:
            continue
        unit_type = card.get("data-unit-type", "").lower()
        unit_text = " ".join(card.xpath('.//div[contains(@class,"product_mini_unit")]//span/text()')).strip()
        unit = "за кг" if unit_type == "кг" else "за л" if unit_type == "л" else unit_text
        images = card.xpath('.//div[contains(@class,"product_mini_image")]//img/@src')
        products.append({
            "name": name,
            "unit": unit,
            "category": category,
            "price": price,
            "out_of_stock": "unavailable" in card.get("class", "").split(),
            "image": urljoin(BASE_URL, images[0]) if images else "",
            "url": urljoin(BASE_URL, urls[0]),
            "sku": card.get("data-product-id", ""),
        })
    return products, max(pages or [1])


async def collect(output="fozzy_products.json", workers=3, delay=0.25, limit_pages=None):
    semaphore = asyncio.Semaphore(workers)
    timeout = aiohttp.ClientTimeout(total=40)
    connector = aiohttp.TCPConnector(limit=workers)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector,
                                     timeout=timeout, trust_env=True) as session:
        async def fetch(path, page=1):
            url = urljoin(BASE_URL, path)
            if page > 1:
                url += f"?page={page}"
            for attempt in range(4):
                try:
                    async with semaphore:
                        async with session.get(url) as response:
                            if response.status in (429, 500, 502, 503, 504):
                                raise RuntimeError(f"HTTP {response.status}")
                            response.raise_for_status()
                            data = await response.read()
                        await asyncio.sleep(delay)
                    return data
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2 ** attempt)

        async def category_pages(category, path):
            first, total = parse_page(await fetch(path), category)
            if not first:
                raise RuntimeError(f"Порожня категорія {category}: {path}")
            last = min(total, limit_pages) if limit_pages else total
            print(f"{category}: {last} сторінок", flush=True)
            pages = [first]
            # Завантажуємо по декілька сторінок; обмеження semaphore спільне
            # для всіх категорій, щоб не створювати надмірного навантаження.
            for start in range(2, last + 1, 12):
                batch = await asyncio.gather(*(fetch(path, page) for page in range(start, min(last + 1, start + 12))))
                for document in batch:
                    items, _ = parse_page(document, category)
                    if not items:
                        raise RuntimeError(f"Порожня сторінка у категорії {category}")
                    pages.append(items)
            return [item for page in pages for item in page]

        # По одній категорії, але сторінки всередині паралельні; простіше
        # контролювати прогрес і зупинятися при неповному завантаженні.
        all_items = []
        seen = set()
        for category, path in CATEGORIES:
            items = await category_pages(category, path)
            for item in items:
                key = item["sku"] or item["url"]
                if key not in seen:
                    seen.add(key)
                    all_items.append(item)
            print(f"  +{len(items)}; всього унікальних {len(all_items)}", flush=True)

    with open(output, "w", encoding="utf-8") as file:
        json.dump(all_items, file, ensure_ascii=False, indent=2)
    print(f"Записано {len(all_items)} товарів у {output}")
    return all_items


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="fozzy_products.json")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit-pages", type=int, default=None, help="Лише для перевірки парсера")
    args = parser.parse_args()
    asyncio.run(collect(args.output, args.workers, limit_pages=args.limit_pages))
