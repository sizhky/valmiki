#!/usr/bin/env python3
"""Populate SQLite sarga_cache table for all sargas listed in the DB."""

import argparse
import os
import sqlite3
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

from valmiki.scraper import SargaReader


def get_sargas(db_path: Path) -> list[tuple[int, int, int]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            '''
            SELECT s.kanda, s.sarga, s.sloka_count,
                   COALESCE(c.cached, 0) AS cached
            FROM sarga_stats s
            LEFT JOIN (
                SELECT kanda, sarga, COUNT(*) AS cached
                FROM sarga_cache
                GROUP BY kanda, sarga
            ) c ON c.kanda = s.kanda AND c.sarga = s.sarga
            ORDER BY s.kanda, s.sarga
            '''
        ).fetchall()
    return [(int(r[0]), int(r[1]), int(r[2])) for r in rows if int(r[2]) > int(r[3])]


def fetch_sarga(args: tuple[int, int, str]) -> tuple[int, int, list[tuple]]:
    kanda, sarga, dill_dir = args
    reader = None
    if dill_dir:
        dill_path = Path(dill_dir) / f'kanda_{kanda}_sarga_{sarga}.dill'
        if dill_path.exists():
            try:
                import dill  # local import so workers don't require it unless needed
                with dill_path.open('rb') as handle:
                    reader = dill.load(handle)
            except Exception:
                reader = None
    if reader is None:
        reader = SargaReader(kanda, sarga, lang='te')
    slokas = reader.get_all_slokas()
    rows = []
    for idx, sloka in enumerate(slokas, start=1):
        sloka_num_text = sloka.get('sloka_num') or f'{kanda}.{sarga}.{idx}'
        rows.append(
            (
                kanda,
                sarga,
                idx,
                sloka_num_text,
                sloka.get('sloka_text', ''),
                sloka.get('bhaavam_en', ''),
            )
        )
    return kanda, sarga, rows


def main() -> None:
    parser = argparse.ArgumentParser(description='Build SQLite cache for all sargas in DB.')
    parser.add_argument('--db', default='data/valmiki.db', help='Path to SQLite DB')
    parser.add_argument('--dill-dir', default='data/sarga_cache', help='Directory of dill caches to prefer')
    parser.add_argument('--workers', type=int, default=max(os.cpu_count() or 2, 2), help='Parallel workers')
    args = parser.parse_args()

    db_path = Path(args.db)
    sargas = get_sargas(db_path)
    if not sargas:
        print('No sargas found in sarga_stats. Run build_sarga_stats.py first.')
        return

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS sarga_cache (
                kanda INTEGER NOT NULL,
                sarga INTEGER NOT NULL,
                sloka_index INTEGER NOT NULL,
                sloka_num_text TEXT NOT NULL,
                sloka_text TEXT NOT NULL,
                bhaavam_en TEXT NOT NULL,
                PRIMARY KEY (kanda, sarga, sloka_index)
            )
            '''
        )
        pending = [(k, s, args.dill_dir) for k, s, _ in sargas]
        if not pending:
            print('All sargas are already cached.')
            return

        workers = max(int(args.workers), 1)
        print(f'Caching {len(pending)} sargas with {workers} workers')

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_sarga, item): item for item in pending}
            for future in as_completed(futures):
                kanda, sarga, _ = futures[future]
                try:
                    k, s, rows = future.result()
                except Exception as exc:
                    print(f'Failed {kanda}.{sarga}: {exc}')
                    continue
                conn.execute('DELETE FROM sarga_cache WHERE kanda = ? AND sarga = ?', (k, s))
                conn.executemany(
                    '''
                    INSERT INTO sarga_cache (kanda, sarga, sloka_index, sloka_num_text, sloka_text, bhaavam_en)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    rows,
                )
                conn.commit()
                print(f'Cached {k}.{s} ({len(rows)} slokas)')


if __name__ == '__main__':
    main()
