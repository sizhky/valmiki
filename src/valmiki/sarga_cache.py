"""Sarga loading behind one cache interface."""

import re
import time
from pathlib import Path
from typing import Callable

import dill
from loguru import logger

from .scraper import SargaReader


class CachedSarga:
    def __init__(self, slokas: list[dict]) -> None:
        self._slokas = slokas

    def __len__(self) -> int:
        return len(self._slokas)

    def __getitem__(self, index: int) -> dict:
        return self._slokas[index]

    def get_all_slokas(self) -> list[dict]:
        return self._slokas


class SargaCache:
    """Load a sarga from memory, SQLite, dill, or the upstream site."""

    def __init__(self, get_conn: Callable, dill_dir: Path) -> None:
        self._get_conn = get_conn
        self._dill_dir = dill_dir
        self._memory: dict[tuple[int, int], SargaReader | CachedSarga] = {}
        self._available = self._find_dill_sargas()

    def get(self, kanda: int, sarga: int) -> SargaReader | CachedSarga:
        started = time.perf_counter()
        key = (kanda, sarga)
        reader = self._memory.get(key)
        if reader is not None:
            self._log_loaded("memory", kanda, sarga, reader, started, "DEBUG")
            return reader
        reader = self._load_database(kanda, sarga)
        source = "database"
        if reader is None:
            try:
                reader = self._load_dill(kanda, sarga)
                source = "dill"
            except Exception:
                logger.bind(event="sarga_dill_failed", kanda=kanda, sarga=sarga).exception(
                    "sarga_dill_failed"
                )
        if reader is None:
            source = "upstream"
            logger.bind(event="sarga_fetch_started", kanda=kanda, sarga=sarga).info(
                "sarga_fetch_started"
            )
            try:
                reader = SargaReader(kanda, sarga, lang="te")
            except Exception:
                logger.bind(event="sarga_fetch_failed", kanda=kanda, sarga=sarga).exception(
                    "sarga_fetch_failed"
                )
                raise
        self._memory[key] = reader
        self._available.add(key)
        if source != "database":
            self._save_database(kanda, sarga, reader)
        self._log_loaded(source, kanda, sarga, reader, started, "INFO")
        return reader

    def previous(self, kanda: int, sarga: int) -> tuple[int, int, int] | None:
        candidates = [item for item in self.available() if item < (kanda, sarga)]
        if not candidates:
            return None
        previous_kanda, previous_sarga = candidates[-1]
        return previous_kanda, previous_sarga, len(self.get(previous_kanda, previous_sarga))

    def next(self, kanda: int, sarga: int) -> tuple[int, int] | None:
        candidates = [item for item in self.available() if item > (kanda, sarga)]
        return candidates[0] if candidates else None

    def available(self) -> list[tuple[int, int]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT kanda, sarga FROM sarga_stats").fetchall()
        self._available.update((int(row["kanda"]), int(row["sarga"])) for row in rows)
        return sorted(self._available)

    def _find_dill_sargas(self) -> set[tuple[int, int]]:
        found = set()
        invalid = []
        for path in self._dill_dir.glob("kanda_*_sarga_*.dill"):
            match = re.fullmatch(r"kanda_(\d+)_sarga_(\d+)\.dill", path.name)
            if not match:
                continue
            try:
                with path.open("rb") as handle:
                    dill.load(handle)
            except Exception:
                invalid.append(path.name)
                continue
            found.add((int(match.group(1)), int(match.group(2))))
        if invalid:
            logger.bind(
                event="sarga_dill_scan",
                valid_count=len(found),
                invalid_count=len(invalid),
                invalid=invalid,
            ).warning("sarga_dill_scan")
        return found

    def _load_database(self, kanda: int, sarga: int) -> CachedSarga | None:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT sloka_num_text, sloka_text, bhaavam_en
                FROM sarga_cache
                WHERE kanda = ? AND sarga = ?
                ORDER BY sloka_index
                """,
                (kanda, sarga),
            ).fetchall()
        if not rows:
            return None
        return CachedSarga(
            [
                {
                    "sloka_num": row["sloka_num_text"],
                    "sloka_text": row["sloka_text"],
                    "bhaavam_en": row["bhaavam_en"],
                    "pratipadaartham": {},
                }
                for row in rows
            ]
        )

    def _load_dill(self, kanda: int, sarga: int) -> SargaReader | None:
        path = self._dill_dir / f"kanda_{kanda}_sarga_{sarga}.dill"
        if not path.exists():
            return None
        with path.open("rb") as handle:
            return dill.load(handle)

    def _save_database(self, kanda: int, sarga: int, reader) -> None:
        rows = [
            (
                kanda,
                sarga,
                index,
                sloka.get("sloka_num") or f"{kanda}.{sarga}.{index}",
                sloka.get("sloka_text", ""),
                sloka.get("bhaavam_en", ""),
            )
            for index, sloka in enumerate(reader.get_all_slokas(), start=1)
        ]
        with self._get_conn() as conn:
            conn.execute("DELETE FROM sarga_cache WHERE kanda = ? AND sarga = ?", (kanda, sarga))
            conn.executemany(
                """
                INSERT INTO sarga_cache
                    (kanda, sarga, sloka_index, sloka_num_text, sloka_text, bhaavam_en)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.execute(
                """
                INSERT INTO sarga_stats (kanda, sarga, sloka_count)
                VALUES (?, ?, ?)
                ON CONFLICT(kanda, sarga) DO UPDATE SET sloka_count = excluded.sloka_count
                """,
                (kanda, sarga, len(reader)),
            )

    @staticmethod
    def _log_loaded(source, kanda, sarga, reader, started, log_level) -> None:
        event = logger.bind(
            event="sarga_loaded",
            source=source,
            kanda=kanda,
            sarga=sarga,
            sloka_count=len(reader),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        event.debug("sarga_loaded") if log_level == "DEBUG" else event.info("sarga_loaded")
