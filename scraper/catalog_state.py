"""Validated catalogues with content-bound collection dates."""
import hashlib
import json
import math
from datetime import date
from pathlib import Path

STORES = ('atb', 'silpo', 'fozzy')
MINIMUM = {'atb': 1500, 'silpo': 15000, 'fozzy': 10000}


def inspect_catalog(path, store):
    data = Path(path).read_bytes()
    rows = json.loads(data)
    if not isinstance(rows, list) or len(rows) < MINIMUM[store]:
        raise ValueError(f'{store}: incomplete catalogue')
    for row in rows:
        price = row.get('price') if isinstance(row, dict) else None
        if not isinstance(price, (float, int)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0 or not row.get('name'):
            raise ValueError(f'{store}: invalid product')
    return rows, hashlib.sha256(data).hexdigest()


def verified_metadata(root=Path('.')):
    meta = json.loads((root / 'catalog_meta.json').read_text())
    for store in STORES:
        _, digest = inspect_catalog(root / f'{store}_products.json', store)
        entry = meta[store]
        date.fromisoformat(entry['collected_on'])
        if entry['sha256'] != digest:
            raise ValueError(f'{store}: catalogue changed without verified collection date')
    return meta
