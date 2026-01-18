#!/usr/bin/env python3
"""Populate dill cache files for all sargas listed in the DB."""

import argparse
import sqlite3
from pathlib import Path

import dill

from valmiki.scraper import SargaReader


def get_sargas(db_path: Path) -> list[tuple[int, int]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            'SELECT kanda, sarga FROM sarga_stats ORDER BY kanda, sarga'
        ).fetchall()
    return [(int(r[0]), int(r[1])) for r in rows]


def cache_path(base: Path, kanda: int, sarga: int) -> Path:
    return base / f'kanda_{kanda}_sarga_{sarga}.dill'


def main() -> None:
    parser = argparse.ArgumentParser(description='Build dill cache for all sargas in DB.')
    parser.add_argument('--db', default='data/valmiki.db', help='Path to SQLite DB')
    parser.add_argument('--out', default='data/sarga_cache', help='Output cache directory')
    args = parser.parse_args()

    dill.settings['recurse'] = False

    db_path = Path(args.db)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    sargas = get_sargas(db_path)
    if not sargas:
        print('No sargas found in sarga_stats. Run build_sarga_stats.py first.')
        return

    for kanda, sarga in sargas:
        path = cache_path(out_dir, kanda, sarga)
        if path.exists():
            continue
        try:
            reader = SargaReader(kanda, sarga, lang='te')
        except Exception as exc:
            print(f'Failed {kanda}.{sarga}: {exc}')
            continue
        try:
            with path.open('wb') as handle:
                dill.dump(reader, handle)
            print(f'Cached {kanda}.{sarga} -> {path}')
        except RecursionError as exc:
            print(f'Skip {kanda}.{sarga}: dill recursion error ({exc})')
        except Exception as exc:
            print(f'Skip {kanda}.{sarga}: dill error ({exc})')


if __name__ == '__main__':
    main()
