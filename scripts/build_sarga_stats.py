#!/usr/bin/env python3
"""Populate sarga and kanda sloka counts in the SQLite DB."""

import argparse
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from valmiki.scraper import SargaReader


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS sarga_stats (
            kanda INTEGER NOT NULL,
            sarga INTEGER NOT NULL,
            sloka_count INTEGER NOT NULL,
            PRIMARY KEY (kanda, sarga)
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS kanda_stats (
            kanda INTEGER PRIMARY KEY,
            total_sargas INTEGER NOT NULL,
            total_slokas INTEGER NOT NULL
        )
        '''
    )


def upsert_sarga(conn: sqlite3.Connection, kanda: int, sarga: int, count: int) -> None:
    conn.execute(
        '''
        INSERT INTO sarga_stats (kanda, sarga, sloka_count)
        VALUES (?, ?, ?)
        ON CONFLICT(kanda, sarga) DO UPDATE SET
            sloka_count = excluded.sloka_count
        ''',
        (kanda, sarga, count),
    )
    conn.commit()


def upsert_kanda(conn: sqlite3.Connection, kanda: int, total_sargas: int, total_slokas: int) -> None:
    conn.execute(
        '''
        INSERT INTO kanda_stats (kanda, total_sargas, total_slokas)
        VALUES (?, ?, ?)
        ON CONFLICT(kanda) DO UPDATE SET
            total_sargas = excluded.total_sargas,
            total_slokas = excluded.total_slokas
        ''',
        (kanda, total_sargas, total_slokas),
    )
    conn.commit()


def _fetch_sarga_count(kanda: int, sarga: int) -> tuple[int, int]:
    reader = SargaReader(kanda, sarga, lang='te')
    try:
        first = reader[0]
        sloka_num = first.get('sloka_num') or ''
    except Exception:
        sloka_num = ''
    prefix = f'{kanda}.{sarga}.'
    if not sloka_num.startswith(prefix):
        raise ValueError(f'unexpected sloka prefix for kanda {kanda} sarga {sarga}: {sloka_num}')
    return sarga, len(reader)


def _get_cached_sargas(conn: sqlite3.Connection, kanda: int) -> dict[int, int]:
    rows = conn.execute(
        'SELECT sarga, sloka_count FROM sarga_stats WHERE kanda = ?',
        (kanda,),
    ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def count_kanda(conn: sqlite3.Connection, kanda: int, max_sarga: int, workers: int) -> None:
    results: dict[int, int] = _get_cached_sargas(conn, kanda)
    if results:
        print(f'kanda {kanda}: using {len(results)} cached sargas')
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_sarga_count, kanda, s): s
            for s in range(1, max_sarga + 1)
            if s not in results
        }
        for future in as_completed(futures):
            sarga = futures[future]
            try:
                sarga, count = future.result()
            except Exception:
                continue
            if count > 0:
                results[sarga] = count
                upsert_sarga(conn, kanda, sarga, count)
                print(f'kanda {kanda} sarga {sarga}: {count}')

    total_slokas = 0
    total_sargas = 0
    for sarga in range(1, max_sarga + 1):
        count = results.get(sarga)
        if not count:
            break
        total_sargas = sarga
        total_slokas += count
    if total_sargas > 0:
        upsert_kanda(conn, kanda, total_sargas, total_slokas)
        print(f'kanda {kanda} total: sargas={total_sargas} slokas={total_slokas}')


def main() -> None:
    parser = argparse.ArgumentParser(description='Build sarga/kanda sloka counts.')
    parser.add_argument('--db', default='data/valmiki.db', help='Path to SQLite DB')
    parser.add_argument('--kanda', type=int, nargs='*', default=[1, 2, 3, 4, 5, 6])
    parser.add_argument('--max-sarga', type=int, default=300)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        init_db(conn)
        for kanda in args.kanda:
            count_kanda(conn, kanda, args.max_sarga, args.workers)


if __name__ == '__main__':
    main()
