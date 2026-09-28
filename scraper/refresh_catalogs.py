"""Collect stores concurrently; retain last verified data on failure."""
import json
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from catalog_state import STORES, inspect_catalog, verified_metadata

ROOT = Path(__file__).resolve().parent


def collect_store(store):
    # Each scraper writes in isolation: failures cannot overwrite good data.
    with tempfile.TemporaryDirectory(prefix=f'{store}-') as tmp:
        output = Path(tmp) / f'{store}_products.json'
        log_path = ROOT / f'{store}_refresh.log'
        command = [sys.executable, '-u', str(ROOT / f'{store}_scraper.py')]
        if store == 'fozzy':
            command += ['--workers', '1']
        with log_path.open('w') as log:
            try:
                subprocess.run(command, cwd=tmp, stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=4200)
            finally:
                diagnostic = Path(tmp) / 'fozzy_diagnostics.json'
                if store == 'fozzy' and diagnostic.exists():
                    shutil.copyfile(diagnostic, ROOT / diagnostic.name)
        rows, digest = inspect_catalog(output, store)
        # Same filesystem is not guaranteed for /tmp and checkout.
        staged = ROOT / f'{store}_products.pending'
        staged.write_bytes(output.read_bytes())
        staged.replace(ROOT / f'{store}_products.json')
        return {'collected_on': datetime.now(ZoneInfo('Europe/Kyiv')).date().isoformat(),
                'sha256': digest, 'count': len(rows)}


def main(stores=STORES):
    os.chdir(ROOT)
    metadata = verified_metadata(ROOT)
    report = {store: 'not requested; kept existing catalogue' for store in STORES if store not in stores}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(collect_store, store): store for store in stores}
        pending = set(futures)
        while pending:
            completed, pending = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
            if not completed:
                for future in pending:
                    store = futures[future]
                    log = ROOT / f'{store}_refresh.log'
                    lines = log.read_text(errors='replace').splitlines() if log.exists() else []
                    print(f'{store}: running — {lines[-1] if lines else "waiting for first page"}', flush=True)
            for future in completed:
                store = futures[future]
                try:
                    metadata[store] = future.result()
                    report[store] = 'updated'
                    print(f'{store}: updated, {metadata[store]["count"]} products', flush=True)
                except Exception as exc:
                    report[store] = f'kept previous catalogue: {exc}'
                    print(f'::warning::{store}: refresh failed; keeping data from {metadata[store]["collected_on"]}. See {store}_refresh.log', flush=True)
                staged = ROOT / 'catalog_meta.pending'
                staged.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
                staged.replace(ROOT / 'catalog_meta.json')
    verified_metadata(ROOT)
    (ROOT / 'refresh_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as file:
            file.write('## Catalogue refresh\n\n| Store | Result | Collection date |\n|---|---|---|\n')
            for store in STORES:
                file.write(f'| {store} | {report[store]} | {metadata[store]["collected_on"]} |\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--store', choices=('all', *STORES), default='all')
    args = parser.parse_args()
    main(STORES if args.store == 'all' else (args.store,))
