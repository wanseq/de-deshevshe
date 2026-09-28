"""Збір звичайних роздрібних цін FOZZY у fozzy_products.json.

    pip3 install -r requirements.txt
    python3 fozzy_scraper.py

Ціна «від N од» — оптова; для порівняння беремо data-main-price
картки товару (акційну роздрібну ціну, якщо є акція).
"""

import argparse
import asyncio
import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import aiohttp
from lxml import html

BASE_URL = "https://fozzyshop.ua"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
           "Accept-Language": "uk-UA,uk;q=0.9",
           "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}

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
    for href in tree.xpath('//div[contains(@class,"pagination")]//a/@href'):
        values = parse_qs(urlparse(href).query).get("page", [])
        pages.extend(int(value) for value in values if value.isdigit())
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
        if not name or not urls or not math.isfinite(price) or price <= 0:
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


class AccessDenied(RuntimeError):
    """A denied request must not be retried as a temporary server error."""


def retry_delay(value, attempt):
    """Respect Retry-After; stop rather than retry early for a long cooldown."""
    seconds = 2 ** attempt
    if value:
        try:
            seconds = max(seconds, float(value))
        except ValueError:
            try:
                seconds = max(seconds, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    if seconds > 120:
        raise RuntimeError("FOZZY requests a cooldown longer than 120 seconds; keeping previous data")
    return seconds


def response_info(response, body):
    """Only diagnostic metadata; never log cookies or full response bodies."""
    text = body[:100000].decode("utf-8", errors="replace")
    match = re.search(r'(?:error\s*(?:code)?\s*[:#]?\s*)(10\d\d)\b', text, re.I)
    return {"url": str(response.url), "status": response.status,
            "server": response.headers.get("Server", ""),
            "cf_ray": response.headers.get("CF-Ray", ""),
            "cf_error": match.group(1) if match else None,
            "content_type": response.headers.get("Content-Type", ""),
            "bytes": len(body)}


async def collect(output="fozzy_products.json", workers=1, delay=0.35, limit_pages=None,
                  check=False, report="fozzy_diagnostics.json"):
    if workers < 1 or workers > 3 or (limit_pages is not None and limit_pages < 1):
        raise ValueError("workers must be 1..3; limit-pages must be positive")
    diagnostics = {"checked_at": datetime.now(timezone.utc).isoformat(), "requests": [], "result": "running"}
    def save_report():
        Path(report).write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_report()
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
                            data = await response.read()
                            info = response_info(response, data)
                            # Keep failed responses and first pages, not hundreds of successes.
                            if response.status != 200 or page == 1:
                                diagnostics["requests"].append(info)
                                save_report()
                            if response.status in (401, 403):
                                diagnostics["result"] = "access_denied"
                                save_report()
                                raise AccessDenied(f"FOZZY HTTP {response.status}; CF code={info['cf_error']}; "
                                                   f"ray={info['cf_ray']}; URL={url}. Access denied, no repeated requests.")
                            if response.status in (429, 500, 502, 503, 504):
                                wait = retry_delay(response.headers.get("Retry-After"), attempt)
                                if attempt < 3:
                                    await asyncio.sleep(wait)
                                    continue
                                raise RuntimeError(f"HTTP {response.status}")
                            response.raise_for_status()
                        await asyncio.sleep(delay)
                    return data
                except AccessDenied:
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2 ** attempt)

        # Start a normal site session and retain any cookies set by the homepage.
        await fetch("/")
        if check:
            category, path = CATEGORIES[0]
            products, total = parse_page(await fetch(path), category)
            if not products:
                diagnostics["result"] = "no_products"
                save_report()
                raise RuntimeError("FOZZY returned a page without product cards; see diagnostics")
            diagnostics.update(result="check_ok", products=len(products), pages=total)
            save_report()
            print(f"FOZZY_CHECK_OK: {len(products)} products, {total} pages. No catalogues changed.", flush=True)
            return products

        async def category_pages(category, path):
            first, total = parse_page(await fetch(path), category)
            if not first:
                raise RuntimeError(f"Порожня категорія {category}: {path}")
            last = min(total, limit_pages) if limit_pages else total
            if last > 1000:
                raise RuntimeError(f"Unexpected page count: {last}")
            print(f"{category}: {last} сторінок", flush=True)
            pages = [first]
            page_signatures = {tuple(sorted(item['sku'] or item['url'] for item in first))}
            # Завантажуємо по декілька сторінок; обмеження semaphore спільне
            # для всіх категорій, щоб не створювати надмірного навантаження.
            for start in range(2, last + 1, 12):
                batch = await asyncio.gather(*(fetch(path, page) for page in range(start, min(last + 1, start + 12))))
                for document in batch:
                    items, _ = parse_page(document, category)
                    if not items:
                        raise RuntimeError(f"Порожня сторінка у категорії {category}")
                    signature = tuple(sorted(item['sku'] or item['url'] for item in items))
                    if signature in page_signatures:
                        raise RuntimeError(f"Repeated page in {category}; refusing incomplete catalogue")
                    page_signatures.add(signature)
                    pages.append(items)
                print(f"  {category}: pages through {min(last, start + 11)}/{last}", flush=True)
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

    staged = Path(output).with_suffix(".pending")
    with staged.open("w", encoding="utf-8") as file:
        json.dump(all_items, file, ensure_ascii=False, indent=2)
    staged.replace(output)
    diagnostics.update(result="complete" if not limit_pages else "limited", products=len(all_items))
    save_report()
    print(f"Записано {len(all_items)} товарів у {output}")
    return all_items


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="fozzy_products.json")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--check", action="store_true", help="Check homepage and one category without changing product data")
    parser.add_argument("--report", default="fozzy_diagnostics.json")
    parser.add_argument("--limit-pages", type=int, default=None, help="Лише для перевірки парсера")
    args = parser.parse_args()
    asyncio.run(collect(args.output, args.workers, limit_pages=args.limit_pages, check=args.check, report=args.report))
