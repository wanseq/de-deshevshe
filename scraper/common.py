# common.py
# Спільні функції парсерів: нормалізація цін і зіставлення трьох каталогів.

import json
import re
import time
import difflib
import math
from datetime import datetime
from zoneinfo import ZoneInfo


def build_date():
    """Дата оновлення в часовому поясі користувачів сайту."""
    return datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()

# ---------------------------------------------------------------------------
# 1. Нормалізація назви товару
# ---------------------------------------------------------------------------
# Мета: "Молоко Яготинське 2.5% 900г" та "молоко яготинське 2,5% 900 г"
# повинні перетворитись на однаковий "ключ", щоб їх можна було зіставити.

UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(кг|мл|шт|kg|ml|г|л|g|l)\b", re.IGNORECASE
)

# Переводимо все одиниці в спільну базу, щоб можна було порівнювати
# "250 мл" з "0,5 л" (а не тільки "мл" з "мл" дослівно).
#   category - "weight" / "volume" / "count"
#   multiplier - у скільки разів множити число, щоб отримати базову одиницю
#                (грами для ваги, мілілітри для об'єму, штуки для count)
UNIT_TO_BASE = {
    "кг": ("weight", 1000.0),
    "г": ("weight", 1.0),
    "л": ("volume", 1000.0),
    "мл": ("volume", 1.0),
    "шт": ("count", 1.0),
    "kg": ("weight", 1000.0),
    "g": ("weight", 1.0),
    "l": ("volume", 1000.0),
    "ml": ("volume", 1.0),
}


def extract_unit(text):
    """Витягує фасування з назви/поля unit. Повертає (значення_в_базових_одиницях,
    категорія) або (None, None). Наприклад "1,5 л" -> (1500.0, "volume"),
    "250 мл" -> (250.0, "volume") - тепер їх можна коректно порівняти між собою."""
    if not text:
        return None, None
    m = UNIT_RE.search(text)
    if not m:
        return None, None
    raw_value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    category, multiplier = UNIT_TO_BASE[unit]
    return raw_value * multiplier, category


def normalize_unit_price(item):
    """Повертає (грн/кг або грн/л, 'кг' або 'л', підпис ціни).

    Явне 'за кг/100 г/л' означає одиницю цінника; інакше вага або
    об'єм з назви/поля unit означає розмір фасованого товару.
    Без надійної кількості не вгадуємо нормалізовану ціну.
    """
    if not item or item.get("price") is None:
        return None, None, ""
    try:
        price = float(item["price"])
    except (ValueError, TypeError):
        return None, None, ""
    if not math.isfinite(price) or price < 0:
        return None, None, ""

    unit_text = item.get("unit") or ""
    name = item.get("name") or ""
    # Позначення одиниці продажу має пріоритет над вагою в назві.
    per = re.search(r"\b(?:за|/|на)\s*(?:(\d+(?:[.,]\d+)?)\s*)?(кг|мл|kg|ml|г|л|g|l)\b", unit_text, re.I)
    if not per:
        per = re.search(r"\b(?:за|/|на)\s*(?:(\d+(?:[.,]\d+)?)\s*)?(кг|мл|kg|ml|г|л|g|l)\b", name, re.I)
    if per:
        quantity = float((per.group(1) or "1").replace(",", "."))
        unit = per.group(2).lower()
        amount = quantity * UNIT_TO_BASE[unit][1]
    else:
        # «шт» у картці магазину — одиниця продажу, а не фасування;
        # вагу/об'єм у такому разі шукаємо в повній назві.
        unit_match = UNIT_RE.search(unit_text)
        source_text = unit_text if unit_match and UNIT_TO_BASE[unit_match.group(2).lower()][0] != "count" else name
        match = UNIT_RE.search(source_text)
        if not match:
            return None, None, ""
        quantity = float(match.group(1).replace(",", "."))
        unit = match.group(2).lower()
        amount = quantity * UNIT_TO_BASE[unit][1]
        # Наприклад, 4х115 г: ціна стосується всіх чотирьох упаковок.
        prefix = source_text[:match.start()]
        pack = re.search(r"(\d+)\s*[xх×]\s*$", prefix, re.I)
        if pack:
            amount *= int(pack.group(1))
            quantity_label = f"{pack.group(1)}×{quantity:g} " + {"kg": "кг", "g": "г", "l": "л", "ml": "мл"}.get(unit, unit)
        else:
            quantity_label = None

    category = UNIT_TO_BASE[unit][0]
    if category == "count" or amount <= 0:
        return None, None, ""
    base_unit = "кг" if category == "weight" else "л"
    display_unit = {"kg": "кг", "g": "г", "l": "л", "ml": "мл"}.get(unit, unit)
    if per or not quantity_label:
        quantity_label = f"{quantity:g} {display_unit}"
    return price * 1000 / amount, base_unit, f"за {quantity_label}"


def is_loose(item):
    """Явна одиниця цінника, на відміну від ваги готової упаковки."""
    return bool(item and re.search(
        r"\bза\s*(?:\d+(?:[.,]\d+)?\s*)?(?:кг|г|л|мл)\b",
        item.get("unit") or "", re.I,
    ))


def product_amount(item):
    """Кількість в грамах/мілілітрах для зіставлення фасування."""
    if not item:
        return None, None
    base_price, base_unit, _ = normalize_unit_price(item)
    if base_price and base_unit:
        return float(item["price"]) * 1000 / base_price, base_unit
    return None, None


def pepsi_variant(name):
    """Смак Pepsi: не плутати звичайну, Black та фруктові версії."""
    n = normalize_name(name)
    if not re.search(r"\bpepsi\b", n) or not re.search(r"\bнапій\b", n):
        return None
    flavor = next((key for key, pattern in (
        ("wild_cherry_cream", r"wild\s+cherry.*cream"),
        ("tropical", r"тропіч|тропік|tropical"),
        ("mango", r"манго|mango"),
        ("lime_mint", r"лайм.*м.ят|lime.*mint"),
        ("cherry", r"вишн|cherry"),
        ("lemon", r"лимон|lemon"),
        ("strawberry", r"полуниц|strawberry"),
        ("cream", r"крем|cream"),
    ) if re.search(pattern, n)), "regular")
    zero = bool(re.search(r"\bblack\b|\bzero\b|нуль\s+цукру|без\s+цукру|безкалорій", n))
    if flavor == "regular" and re.search(r"real\s+sugar", n):
        flavor = "real_sugar"
    return flavor, zero


def monster_variant(name):
    """Виділяє смак/лінійку Monster, включно з новими смаками."""
    n = normalize_name(name)
    if not re.search(r"\bmonster\b", n) or not re.search(r"\bнапій\b", n) or "capri" in n:
        return None
    after = re.search(r"\bmonster\b((?:\s+[a-z0-9]+){0,6})", n)
    words = (after.group(1).strip().split() if after else [])
    words = [w for w in words if w not in {"energy", "juice"}]
    variant = " ".join(words).replace("mangoloco", "mango loco").replace("ultrafiesta", "ultra fiesta")
    return variant or "original"


# Деякі сайти (як з'ясувалось - і АТБ теж) в частині назв товарів
# випадково змішують кириличні й ЛАТИНСЬКІ літери, які виглядають
# однаково: "бeзaлкoгoльний" замість "безалкогольний" (тут e,a,o -
# латинські). Візуально непомітно, але для порівняння рядків це вже
# інше слово - і саме через це "pepsi" програвав такому "рідкісному"
# зіпсованому слову в пошуку ключа для групування.
_LATIN_TO_CYRILLIC_HOMOGLYPHS = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с",
    "x": "х", "y": "у", "i": "і", "k": "к", "h": "н",
    "t": "т", "m": "м", "b": "в",
})


def _fix_mixed_script_word(word):
    """Якщо слово містить І кириличні, І латинські літери - це майже
    напевно не навмисна латиниця (як бренд "pepsi"), а зіпсоване через
    візуально схожі літери кириличне слово. Виправляємо. Чисто
    латинські слова (реальні бренди) не чіпаємо."""
    has_cyrillic = any(("а" <= ch <= "я") or ch in "іїєґ" for ch in word)
    has_latin = any("a" <= ch <= "z" for ch in word)
    if has_cyrillic and has_latin:
        return word.translate(_LATIN_TO_CYRILLIC_HOMOGLYPHS)
    return word


def normalize_name(name):
    """Прибирає зайві символи, лапки, зводить до нижнього регістру - для зіставлення."""
    if not name:
        return ""
    n = name.lower()
    n = n.replace("«", " ").replace("»", " ").replace('"', " ")
    n = re.sub(r"[^a-zа-яіїєґ0-9.,%\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = " ".join(_fix_mixed_script_word(w) for w in n.split())
    return n


def similarity(a, b):
    """Гібридна оцінка схожості: більшість ваги - на спільні значущі
    слова (не залежить від порядку слів у назві), менша частина - на
    посимвольну схожість (щоб трохи враховувати форми слів, помилки
    тощо). Чисто посимвольне порівняння (як було раніше) підводило
    саме на переставлених словах: "Напій Pepsi... 0.5л" проти "Pepsi
    напій... 0,5 л" - те саме, але формально різні рядки символів."""
    words_a = set(significant_words(a))
    words_b = set(significant_words(b))
    char_sim = difflib.SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()

    if not words_a or not words_b:
        return char_sim

    union = words_a | words_b
    jaccard = len(words_a & words_b) / len(union) if union else 0.0
    return 0.7 * jaccard + 0.3 * char_sim


# ---------------------------------------------------------------------------
# 2. Зіставлення товарів АТБ і Сільпо в один список
# ---------------------------------------------------------------------------
# Очікуваний вхідний формат для кожного магазину - список словників:
# {"name": str, "unit": str, "category": str, "price": float, "image": str, "url": str}
#
# threshold - наскільки схожими повинні бути назви (0..1), щоб вважати це одним товаром.
# Підбирайте експериментально: 0.55-0.65 зазвичай непогано працює для назв товарів.

# ---------------------------------------------------------------------------
# Очікуваний вхідний формат для кожного магазину - список словників:
# {"name": str, "unit": str, "category": str, "price": float, "image": str, "url": str}
#
# threshold - наскільки схожими повинні бути назви (0..1), щоб вважати це одним товаром.
# Підбирайте експериментально: 0.55-0.65 зазвичай непогано працює для назв товарів.
#
# only_matched - якщо True (за замовчуванням), у результат потрапляють ТІЛЬКИ
# товари, які знайшлись в ОБОХ магазинах. Це і швидше для сайту (не тисячі
# позицій "тільки в одному магазині"), і саме те, для чого сайт задумувався -
# порівняння цін. Поставте False, якщо хочете бачити й товари без пари.

STOPWORDS = {"з", "та", "і", "в", "на", "без", "для", "по", "від", "як"}


def significant_words(name):
    """Усі значущі слова назви (без коротких службових слів і без
    "слів", що починаються з цифри - типу "250г" чи "2.5%" - їх
    порівнює окрема, точніша перевірка фасування (extract_unit), а
    тут вони тільки заважали б через різне написання коми/крапки)."""
    words = normalize_name(name).split()
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS and not w[0].isdigit()]


def block_keys(name, top_n=2):
    """Кілька найдовших значущих слів - простий варіант без частотної
    таблиці, використовується тільки для швидкого перегляду/діагностики
    (debug_search.py). Основний алгоритм зіставлення (match_products)
    використовує точніший rarest_keys() нижче."""
    words = significant_words(name)
    if not words:
        return [""]
    uniq_sorted = sorted(set(words), key=len, reverse=True)
    return uniq_sorted[:top_n]


def block_key(name):
    """Один головний ключ (найдовше слово) - для діагностики/показу."""
    return block_keys(name, top_n=1)[0]


def build_word_freq(items):
    """Рахує, у скількох товарах зустрічається кожне значуще слово."""
    freq = {}
    for it in items:
        for w in set(significant_words(it.get("name", ""))):
            freq[w] = freq.get(w, 0) + 1
    return freq


def rarest_keys(name, freq, top_n=2):
    """Кілька НАЙРІДШИХ значущих слів назви (за реальною частотою в
    даних, а не просто за довжиною слова). Рідкісне слово - майже
    завжди бренд чи специфічна назва ("pepsi", "яготинське"), а не
    загальний іменник категорії ("напій", "молоко") - тому товари
    групуються за ним значно точніше й компактніше, і це працює
    незалежно від порядку слів у назві.

    Слова з частотою 0 (яких немає в Сільпо взагалі - наприклад,
    через інше написання) навмисно йдуть В ОСТАННЮ чергу: хоча "0"
    формально найрідше число, шукати збіг за таким ключем безглуздо -
    кандидатів там гарантовано не буде."""
    words = significant_words(name)
    if not words:
        return [""]
    uniq = list(set(words))
    uniq.sort(key=lambda w: (freq.get(w, 0) == 0, freq.get(w, 0), -len(w)))
    return uniq[:top_n]


MAX_CANDIDATES_PER_ITEM = 500  # запобіжник: більше кандидатів не розглядаємо


def match_item_pairs(atb_items, silpo_items, threshold=0.6, only_matched=True, verbose=True):
    matched = []
    used_silpo = set()

    # Частота кожного слова серед товарів Сільпо - щоб визначити, які
    # слова рідкісні (специфічні) і годяться для групування, а які
    # занадто загальні (типу "безалкогольний", "напій").
    silpo_word_freq = build_word_freq(silpo_items)

    # Індекс: рідкісне слово -> список індексів товарів Сільпо. Порівнюємо
    # ATB-товар тільки з цими кандидатами, а не з усіма 26000+ товарами
    # Сільпо - і це працює навіть якщо порядок слів у назвах різний.
    silpo_index = {}
    pepsi_ids = []
    monster_ids = []
    for idx, m in enumerate(silpo_items):
        for key in rarest_keys(m.get("name", ""), silpo_word_freq):
            silpo_index.setdefault(key, []).append(idx)
        if pepsi_variant(m.get("name", "")) is not None:
            pepsi_ids.append(idx)
        if monster_variant(m.get("name", "")) is not None:
            monster_ids.append(idx)

    total = len(atb_items)
    start = time.time()

    for i, a in enumerate(atb_items):
        if verbose and i % 200 == 0 and i > 0:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (total - i) / rate if rate > 0 else 0
            print(f"    зіставлено {i}/{total} ({elapsed:.0f} сек, залишилось ~{remaining:.0f} сек)")

        av, ac = product_amount(a)
        a_loose = is_loose(a)
        a_pepsi = pepsi_variant(a.get("name", ""))
        a_monster = monster_variant(a.get("name", ""))

        candidate_ids = set()
        for key in rarest_keys(a.get("name", ""), silpo_word_freq):
            candidate_ids.update(silpo_index.get(key, []))
        if a_pepsi is not None:
            candidate_ids.update(pepsi_ids)
        if a_monster is not None:
            candidate_ids.update(monster_ids)
        if len(candidate_ids) > MAX_CANDIDATES_PER_ITEM:
            candidate_ids = set(list(candidate_ids)[:MAX_CANDIDATES_PER_ITEM])

        best = None
        best_score = 0.0
        for idx in candidate_ids:
            if idx in used_silpo:
                continue
            m = silpo_items[idx]

            mv, mc = product_amount(m)
            if ac and mc and ac != mc:
                continue
            if ac and re.search(r"\bшт\.?\b", m.get("name", ""), re.I):
                continue
            produce_categories = {"Овочі та фрукти", "Фрукти, овочі"}
            if (a.get("category") in produce_categories) != (m.get("category") in produce_categories):
                continue
            # При зіставленні з Фоззі не припускаємо однакову фасовку,
            # якщо в одній із назв бракує відомостей про кількість.
            if ("sku" in a or "sku" in m) and not (av and mv):
                continue

            m_pepsi = pepsi_variant(m.get("name", ""))
            if a_pepsi is not None or m_pepsi is not None:
                if a_pepsi is None or a_pepsi != m_pepsi or not av or not mv:
                    continue
            m_monster = monster_variant(m.get("name", ""))
            if a_monster is not None or m_monster is not None:
                if a_monster is None or a_monster != m_monster or not av or not mv:
                    continue
            # Упаковки зіставляємо лише за однакової кількості. Для
            # розсипних овочів/фруктів 1 кг та 100 г — та сама база.
            if av and mv and ac == mc and not (a_loose or is_loose(m)):
                if min(av, mv) / max(av, mv) < 0.99:
                    continue

            score = similarity(a.get("name", ""), m.get("name", ""))
            if a_pepsi is not None or a_monster is not None:
                score = 1.0 + score * 0.01
            if score > best_score:
                best_score = score
                best = idx

        if best is not None and best_score >= threshold:
            used_silpo.add(best)
            m = silpo_items[best]
            matched.append((a, m))
        elif not only_matched:
            matched.append((a, None))

    if not only_matched:
        for idx, m in enumerate(silpo_items):
            if idx not in used_silpo:
                matched.append((None, m))

    return matched


def match_products(atb_items, silpo_items, threshold=0.6, only_matched=True, verbose=True):
    """Сумісність з колишнім двомагазинним викликом."""
    return [build_entry(a, s) for a, s in match_item_pairs(
        atb_items, silpo_items, threshold, only_matched, verbose)]


def merge_three_products(atb_items, silpo_items, fozzy_items, threshold=0.6):
    """Об'єднує збіги; включає товар у наявності щонайменше у 2 магазинах."""
    groups = []
    atb_group = {}
    silpo_group = {}
    for a, s in match_item_pairs(atb_items, silpo_items, threshold, False):
        index = len(groups)
        groups.append({"atb": a, "silpo": s, "fozzy": None})
        if a is not None:
            atb_group[id(a)] = index
        if s is not None:
            silpo_group[id(s)] = index

    # Новий каталог великий; високий поріг відсікає схожі товари іншого
    # бренду/смаку, залишаючи точні спільні назви та особливі варіанти.
    fozzy_threshold = max(threshold, 0.85)
    f_to_a = {id(f): a for f, a in match_item_pairs(
        fozzy_items, atb_items, fozzy_threshold, True, False)}
    f_to_s = {id(f): s for f, s in match_item_pairs(
        fozzy_items, silpo_items, fozzy_threshold, True, False)}
    for f in fozzy_items:
        ga = atb_group.get(id(f_to_a[id(f)])) if id(f) in f_to_a else None
        gs = silpo_group.get(id(f_to_s[id(f)])) if id(f) in f_to_s else None
        if ga is not None and gs is not None and ga != gs:
            # Два одиночні товари з АТБ і Сільпо можуть утворити
            # повну трійку завдяки спільному товару Фоззі.
            if (groups[ga]["silpo"] is None and
                    groups[gs]["atb"] is None and
                    groups[ga]["fozzy"] is None and
                    groups[gs]["fozzy"] is None):
                groups[ga]["silpo"] = groups[gs]["silpo"]
                groups[gs] = None
                gs = ga
            else:
                # Суперечливі пари не об'єднуємо: інший смак/версія
                # можуть мати дуже схожі назви.
                ga = None
                gs = None
        index = ga if ga is not None else gs
        if index is not None and groups[index] and groups[index]["fozzy"] is None:
            groups[index]["fozzy"] = f

    return [build_entry(g["atb"], g["silpo"], g["fozzy"])
            for g in groups if g and sum(
                bool(item and item.get("price") is not None and not item.get("out_of_stock"))
                for item in g.values()) >= 2]


def build_entry(atb_item, silpo_item, fozzy_item=None):
    items = {"atb": atb_item, "silpo": silpo_item, "fozzy": fozzy_item}
    src = atb_item or silpo_item or fozzy_item
    normalized = {store: normalize_unit_price(item) for store, item in items.items()}
    produce = src.get("category") in {"Овочі та фрукти", "Фрукти, овочі"}
    beverage = src.get("category") in {"Напої безалкогольні", "Напої"}
    show_base_unit = "кг" if produce and any(is_loose(item) for item in items.values()) else ("л" if beverage else None)

    comparison_mode = None
    comparable = {store: None for store in items}
    base_stores = [store for store, item in items.items() if item and
                   normalized[store][0] is not None and normalized[store][1] == show_base_unit]
    if show_base_unit and len(base_stores) >= 2:
        comparison_mode = "base"
        for store in base_stores:
            comparable[store] = normalized[store][0]
    else:
        amounts = {store: product_amount(item) for store, item in items.items() if item}
        valid = [(store, amount, unit) for store, (amount, unit) in amounts.items() if amount and unit]
        if len(valid) >= 2:
            ref_amount, ref_unit = valid[0][1:]
            same = [store for store, amount, unit in valid if unit == ref_unit and
                    min(amount, ref_amount) / max(amount, ref_amount) >= 0.99]
            if len(same) >= 2:
                comparison_mode = "pack"
                for store in same:
                    comparable[store] = items[store]["price"]

    entry = {
        "name": src.get("name"),
        "unit": src.get("unit", ""),
        "category": src.get("category", ""),
        "tags": normalize_name(src.get("name", "")),
        "compare_prices": comparable,
        "comparison_mode": comparison_mode,
        "image": next((item.get("image") for item in items.values() if item and item.get("image")), ""),
        "source": src.get("url", ""),
        "updated": build_date(),
        "demo": False,
    }
    for store, item in items.items():
        per_base, base_unit, label = normalized[store]
        shown_base = per_base if base_unit == show_base_unit else None
        entry[store] = round(shown_base, 2) if shown_base is not None else (item["price"] if item else None)
        entry[f"{store}_raw"] = item["price"] if item else None
        entry[f"{store}_out_of_stock"] = bool(item.get("out_of_stock")) if item else False
        entry[f"{store}_per_base"] = shown_base
        entry[f"{store}_base_unit"] = base_unit if shown_base is not None else None
        entry[f"{store}_price_label"] = label
        entry[f"{store}_url"] = item.get("url", "") if item else ""
    return entry


# ---------------------------------------------------------------------------
# 3. Запис products.js у форматі, який читає app.js на сайті
# ---------------------------------------------------------------------------

def write_products_js(entries, path="../products.js"):
    header = (
        "// products.js\n"
        "// Згенеровано автоматично скриптами з папки /scraper.\n"
        f"// Дата збірки: {build_date()}\n"
        f"// Кількість товарів: {len(entries)}\n\n"
        "const PRODUCTS = "
    )
    # Каталог з десятками тисяч позицій: компактний запис швидше
    # завантажується, без втрати полів чи точності чисел.
    body = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    footer = ";\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + body + footer)
    print(f"Записано {len(entries)} товарів у {path}")
