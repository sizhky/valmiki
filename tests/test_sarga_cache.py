import sqlite3
from pathlib import Path

import dill

from valmiki.sarga_cache import CachedSarga, SargaCache


def _connection(database: Path):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def test_available_sargas_include_dill_files_and_skip_gaps(tmp_path):
    database = tmp_path / "valmiki.db"
    with _connection(database) as connection:
        connection.execute(
            """
            CREATE TABLE sarga_stats (
                kanda INTEGER NOT NULL,
                sarga INTEGER NOT NULL,
                sloka_count INTEGER NOT NULL,
                PRIMARY KEY (kanda, sarga)
            )
            """
        )
    dill_dir = tmp_path / "sarga_cache"
    dill_dir.mkdir()
    for sarga in (70, 72):
        with (dill_dir / f"kanda_2_sarga_{sarga}.dill").open("wb") as handle:
            dill.dump(CachedSarga([{"sloka_num": f"2.{sarga}.1"}]), handle)

    cache = SargaCache(lambda: _connection(database), dill_dir)

    assert cache.next(2, 70) == (2, 72)
