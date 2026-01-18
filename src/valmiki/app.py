"""FastHTML web application for Valmiki Ramayana Reader."""

import sqlite3
import json
import time
from urllib.parse import quote
from pathlib import Path

from fasthtml.common import *
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse

from .scraper import SargaReader

# Initialize FastHTML app
app = FastHTML()
rt = app.route

# In-memory storage
sarga_readers = {}  # Cache for SargaReader instances: {(kanda, sarga): SargaReader}
db_path = (Path(__file__).resolve().parents[2] / 'data' / 'valmiki.db')
DEFAULT_LANGUAGE = 'te'
MAX_KANDA = 6
DEFAULT_USER_ID = 1
stats_cache = {
    'ramayana_total': None,
    'kanda_totals': {},
    'kanda_sargas': {},
    'kanda_prefix': {},
    'ramayana_prefix': {},
}

KANDA_NAMES = {
    1: 'Bāla Kāṇḍa',
    2: 'Ayodhyā Kāṇḍa',
    3: 'Araṇya Kāṇḍa',
    4: 'Kiṣkindhā Kāṇḍa',
    5: 'Sundara Kāṇḍa',
    6: 'Yuddha Kāṇḍa',
}

# Translation caches (for future translator integration)
translation_cache = {
    'te': {},  # Telugu translations: {english_text: telugu_text}
    'tg': {},  # Telangana translations: {english_text: telangana_text}
}


class CachedSarga:
    def __init__(self, slokas: list[dict]) -> None:
        self._slokas = slokas

    def __len__(self) -> int:
        return len(self._slokas)

    def __getitem__(self, index: int) -> dict:
        return self._slokas[index]

    def get_all_slokas(self) -> list[dict]:
        return self._slokas


def _get_conn():
    """Open a SQLite connection with a row factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for _ in range(3):
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('PRAGMA journal_mode = WAL')
            conn.execute('PRAGMA synchronous = NORMAL')
            conn.execute('PRAGMA busy_timeout = 3000')
            return conn
        except sqlite3.OperationalError as exc:
            last_error = exc
            time.sleep(0.05)
    raise last_error


def _load_sarga_from_cache(kanda: int, sarga: int):
    with _get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT sloka_index, sloka_num_text, sloka_text, bhaavam_en
            FROM sarga_cache
            WHERE kanda = ? AND sarga = ?
            ORDER BY sloka_index
            ''',
            (kanda, sarga),
        ).fetchall()
    if not rows:
        return None
    slokas = []
    for row in rows:
        slokas.append(
            {
                'sloka_num': row['sloka_num_text'],
                'sloka_text': row['sloka_text'],
                'bhaavam_en': row['bhaavam_en'],
                'pratipadaartham': {},
            }
        )
    return CachedSarga(slokas)


def _save_sarga_to_cache(kanda: int, sarga: int, sr: SargaReader) -> None:
    slokas = sr.get_all_slokas()
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
    with _get_conn() as conn:
        conn.execute(
            'DELETE FROM sarga_cache WHERE kanda = ? AND sarga = ?',
            (kanda, sarga),
        )
        conn.executemany(
            '''
            INSERT INTO sarga_cache (kanda, sarga, sloka_index, sloka_num_text, sloka_text, bhaavam_en)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            rows,
        )


def _record_sarga_len(kanda: int, sarga: int, sloka_count: int) -> None:
    with _get_conn() as conn:
        conn.execute(
            '''
            INSERT INTO sarga_stats (kanda, sarga, sloka_count)
            VALUES (?, ?, ?)
            ON CONFLICT(kanda, sarga) DO UPDATE SET
                sloka_count = excluded.sloka_count
            ''',
            (kanda, sarga, sloka_count),
        )


def _get_sarga_len(kanda: int, sarga: int) -> int:
    with _get_conn() as conn:
        row = conn.execute(
            'SELECT sloka_count FROM sarga_stats WHERE kanda = ? AND sarga = ?',
            (kanda, sarga),
        ).fetchone()
    if row:
        return int(row['sloka_count'])
    sr = _get_sarga_reader(kanda, sarga)
    count = len(sr)
    _record_sarga_len(kanda, sarga, count)
    return count


def _get_kanda_total_slokas(kanda: int) -> int:
    cached = stats_cache['kanda_totals'].get(kanda)
    if cached is not None:
        return cached
    with _get_conn() as conn:
        row = conn.execute(
            'SELECT total_slokas FROM kanda_stats WHERE kanda = ?',
            (kanda,),
        ).fetchone()
        if row:
            total = int(row['total_slokas'])
            stats_cache['kanda_totals'][kanda] = total
            return total
        row = conn.execute(
            'SELECT COALESCE(SUM(sloka_count), 0) AS total FROM sarga_stats WHERE kanda = ?',
            (kanda,),
        ).fetchone()
        if row and int(row['total']) > 0:
            total_slokas = int(row['total'])
            total_sargas = conn.execute(
                'SELECT COUNT(*) AS count FROM sarga_stats WHERE kanda = ?',
                (kanda,),
            ).fetchone()
            total_sargas = int(total_sargas['count']) if total_sargas else 0
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
            stats_cache['kanda_totals'][kanda] = total_slokas
            if total_sargas:
                stats_cache['kanda_sargas'][kanda] = total_sargas
            return total_slokas

    total_slokas = 0
    total_sargas = 0
    for sarga in range(1, 301):
        try:
            count = _get_sarga_len(kanda, sarga)
        except Exception:
            break
        if count <= 0:
            break
        total_slokas += count
        total_sargas = sarga
    if total_sargas == 0:
        total_sargas = 1
    if total_slokas == 0:
        total_slokas = _get_sarga_len(kanda, 1)
    with _get_conn() as conn:
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
    stats_cache['kanda_totals'][kanda] = total_slokas
    if total_sargas:
        stats_cache['kanda_sargas'][kanda] = total_sargas
    return total_slokas


def _get_kanda_total_sargas(kanda: int) -> int:
    cached = stats_cache['kanda_sargas'].get(kanda)
    if cached is not None:
        return cached
    with _get_conn() as conn:
        total_row = conn.execute(
            'SELECT total_sargas FROM kanda_stats WHERE kanda = ?',
            (kanda,),
        ).fetchone()
        total_from_stats = int(total_row['total_sargas']) if total_row and total_row['total_sargas'] is not None else 0
        row = conn.execute(
            'SELECT MAX(sarga) AS max_sarga FROM sarga_stats WHERE kanda = ?',
            (kanda,),
        ).fetchone()
        max_sarga = int(row['max_sarga']) if row and row['max_sarga'] is not None else 0
        total = max(total_from_stats, max_sarga)
        if total > 0:
            stats_cache['kanda_sargas'][kanda] = total
            return total
    return 0


def _get_kanda_progress_slokas(kanda: int, sarga: int, sloka_num: int) -> int:
    prefix = stats_cache['kanda_prefix'].get((kanda, sarga))
    if prefix is None:
        with _get_conn() as conn:
            row = conn.execute(
                '''
                SELECT COALESCE(SUM(sloka_count), 0) AS total
                FROM sarga_stats
                WHERE kanda = ? AND sarga < ?
                ''',
                (kanda, sarga),
            ).fetchone()
        prefix = int(row['total']) if row else 0
        stats_cache['kanda_prefix'][(kanda, sarga)] = prefix
    return prefix + sloka_num


def _get_ramayana_total_slokas() -> int:
    cached = stats_cache['ramayana_total']
    if cached is not None:
        return cached
    with _get_conn() as conn:
        row = conn.execute(
            'SELECT COALESCE(SUM(sloka_count), 0) AS total FROM sarga_stats'
        ).fetchone()
    total = int(row['total']) if row else 0
    stats_cache['ramayana_total'] = total
    return total


def _get_ramayana_progress_slokas(kanda: int, sarga: int, sloka_num: int) -> int:
    prefix = stats_cache['ramayana_prefix'].get(kanda)
    if prefix is None:
        with _get_conn() as conn:
            row = conn.execute(
                '''
                SELECT COALESCE(SUM(sloka_count), 0) AS total
                FROM sarga_stats
                WHERE kanda < ?
                ''',
                (kanda,),
            ).fetchone()
        prefix = int(row['total']) if row else 0
        stats_cache['ramayana_prefix'][kanda] = prefix
    return prefix + _get_kanda_progress_slokas(kanda, sarga, sloka_num)


def _get_sarga_reader(kanda: int, sarga: int) -> SargaReader:
    sr = sarga_readers.get((kanda, sarga))
    if sr is not None:
        return sr
    sr = _load_sarga_from_cache(kanda, sarga)
    if sr is not None:
        sarga_readers[(kanda, sarga)] = sr
        _record_sarga_len(kanda, sarga, len(sr))
        return sr
    sr = SargaReader(kanda, sarga, lang='te')
    sarga_readers[(kanda, sarga)] = sr
    _save_sarga_to_cache(kanda, sarga, sr)
    _record_sarga_len(kanda, sarga, len(sr))
    return sr


def _init_db():
    """Initialize SQLite schema if needed."""
    with _get_conn() as conn:
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                birth_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(first_name, last_name, birth_date)
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS reading_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                language TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS thread_progress (
                thread_id INTEGER PRIMARY KEY,
                kanda INTEGER NOT NULL,
                sarga INTEGER NOT NULL,
                sloka_num INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (thread_id) REFERENCES reading_threads(id) ON DELETE CASCADE
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS thread_bookmarks (
                thread_id INTEGER NOT NULL,
                kanda INTEGER NOT NULL,
                sarga INTEGER NOT NULL,
                sloka_num INTEGER NOT NULL,
                PRIMARY KEY (thread_id, kanda, sarga, sloka_num),
                FOREIGN KEY (thread_id) REFERENCES reading_threads(id) ON DELETE CASCADE
            )
            '''
        )
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
        columns = conn.execute(
            'PRAGMA table_info(reading_threads)'
        ).fetchall()
        column_names = {row['name'] for row in columns}
        if 'user_id' not in column_names:
            conn.execute(
                'ALTER TABLE reading_threads ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1'
            )
        user = conn.execute(
            'SELECT id FROM users WHERE id = ? LIMIT 1',
            (DEFAULT_USER_ID,),
        ).fetchone()
        if not user:
            conn.execute(
                '''
                INSERT OR IGNORE INTO users (id, first_name, last_name, birth_date)
                VALUES (?, ?, ?, ?)
                ''',
                (DEFAULT_USER_ID, 'Default', 'User', '1900-01-01'),
            )
        conn.execute(
            'UPDATE reading_threads SET user_id = ? WHERE user_id IS NULL',
            (DEFAULT_USER_ID,),
        )


def _next_thread_name(user_id: int) -> str:
    with _get_conn() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS count FROM reading_threads WHERE user_id = ?',
            (user_id,),
        ).fetchone()
    count = int(row['count']) if row else 0
    return f'Thread {count + 1}'


def _create_thread(name: str | None, kanda: int, sarga: int, sloka_num: int) -> int:
    return _create_thread_for_user(DEFAULT_USER_ID, name, kanda, sarga, sloka_num)


def _create_thread_for_user(user_id: int, name: str | None, kanda: int, sarga: int, sloka_num: int) -> int:
    thread_name = name.strip() if name else ''
    if not thread_name:
        thread_name = _next_thread_name(user_id)
    with _get_conn() as conn:
        thread_id = conn.execute(
            'INSERT INTO reading_threads (user_id, name, language) VALUES (?, ?, ?)',
            (user_id, thread_name, DEFAULT_LANGUAGE),
        ).lastrowid
        conn.execute(
            '''
            INSERT INTO thread_progress (thread_id, kanda, sarga, sloka_num)
            VALUES (?, ?, ?, ?)
            ''',
            (thread_id, kanda, sarga, sloka_num),
        )
    return int(thread_id)


def _ensure_default_thread() -> int:
    return _ensure_default_thread_for_user(DEFAULT_USER_ID)


def _ensure_default_thread_for_user(user_id: int) -> int:
    with _get_conn() as conn:
        row = conn.execute(
            'SELECT id FROM reading_threads WHERE user_id = ? ORDER BY id LIMIT 1',
            (user_id,),
        ).fetchone()
    if row:
        return int(row['id'])
    return _create_thread_for_user(user_id, None, 1, 1, 1)


def _get_thread(thread_id: int, user_id: int | None = None):
    with _get_conn() as conn:
        clause = 'WHERE t.id = ?'
        params = [thread_id]
        if user_id is not None:
            clause += ' AND t.user_id = ?'
            params.append(user_id)
        return conn.execute(
            f'''
            SELECT t.id, t.name, t.language, p.kanda, p.sarga, p.sloka_num, p.updated_at
            FROM reading_threads t
            LEFT JOIN thread_progress p ON p.thread_id = t.id
            {clause}
            ''',
            params,
        ).fetchone()


def _rename_thread(thread_id: int, user_id: int, name: str) -> None:
    clean_name = name.strip()
    if not clean_name:
        return
    with _get_conn() as conn:
        conn.execute(
            'UPDATE reading_threads SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?',
            (clean_name, thread_id, user_id),
        )


def _update_progress(thread_id: int, kanda: int, sarga: int, sloka_num: int) -> None:
    with _get_conn() as conn:
        conn.execute(
            '''
            INSERT INTO thread_progress (thread_id, kanda, sarga, sloka_num)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                kanda = excluded.kanda,
                sarga = excluded.sarga,
                sloka_num = excluded.sloka_num,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (thread_id, kanda, sarga, sloka_num),
        )
        conn.execute(
            'UPDATE reading_threads SET updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (thread_id,),
        )


def _get_threads(user_id: int | None = None):
    params: list[object] = []
    clause = ''
    if user_id is not None:
        clause = 'WHERE t.user_id = ?'
        params.append(user_id)
    with _get_conn() as conn:
        rows = conn.execute(
            f'''
            SELECT t.id, t.name, t.language, p.kanda, p.sarga, p.sloka_num, p.updated_at
            FROM reading_threads t
            LEFT JOIN thread_progress p ON p.thread_id = t.id
            {clause}
            ORDER BY p.updated_at DESC, t.id DESC
            ''',
            params,
        ).fetchall()
    return rows


def _ensure_legacy_bookmarks() -> None:
    with _get_conn() as conn:
        legacy_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bookmarks'"
        ).fetchone()
        if not legacy_table:
            return
        legacy_count = conn.execute('SELECT COUNT(*) AS count FROM bookmarks').fetchone()
        if not legacy_count or int(legacy_count['count']) == 0:
            return
        current_count = conn.execute(
            '''
            SELECT COUNT(*) AS count
            FROM thread_bookmarks tb
            JOIN reading_threads t ON t.id = tb.thread_id
            ''',
        ).fetchone()
        if current_count and int(current_count['count']) > 0:
            return
        thread_id = conn.execute(
            'INSERT INTO reading_threads (user_id, name, language) VALUES (?, ?, ?)',
            (DEFAULT_USER_ID, 'Legacy Bookmarks', DEFAULT_LANGUAGE),
        ).lastrowid
        conn.execute(
            'INSERT INTO thread_progress (thread_id, kanda, sarga, sloka_num) VALUES (?, 1, 1, 1)',
            (thread_id,),
        )
        conn.execute(
            '''
            INSERT OR IGNORE INTO thread_bookmarks (thread_id, kanda, sarga, sloka_num)
            SELECT ?, kanda, sarga, sloka_num FROM bookmarks
            ''',
            (thread_id,),
        )


def _is_bookmarked(thread_id: int, kanda: int, sarga: int, sloka_num: int) -> bool:
    with _get_conn() as conn:
        row = conn.execute(
            '''
            SELECT 1 FROM thread_bookmarks
            WHERE thread_id = ? AND kanda = ? AND sarga = ? AND sloka_num = ?
            ''',
            (thread_id, kanda, sarga, sloka_num),
        ).fetchone()
    return row is not None


def _toggle_bookmark(thread_id: int, kanda: int, sarga: int, sloka_num: int) -> bool:
    with _get_conn() as conn:
        row = conn.execute(
            '''
            SELECT 1 FROM thread_bookmarks
            WHERE thread_id = ? AND kanda = ? AND sarga = ? AND sloka_num = ?
            ''',
            (thread_id, kanda, sarga, sloka_num),
        ).fetchone()
        if row:
            conn.execute(
                '''
                DELETE FROM thread_bookmarks
                WHERE thread_id = ? AND kanda = ? AND sarga = ? AND sloka_num = ?
                ''',
                (thread_id, kanda, sarga, sloka_num),
            )
            return False
        conn.execute(
            '''
            INSERT INTO thread_bookmarks (thread_id, kanda, sarga, sloka_num)
            VALUES (?, ?, ?, ?)
            ''',
            (thread_id, kanda, sarga, sloka_num),
        )
        return True


def _get_thread_bookmarks(user_id: int, thread_id: int | None = None):
    _ensure_legacy_bookmarks()
    params: list[object] = []
    clause = 'WHERE t.user_id = ?'
    params.append(user_id)
    if thread_id is not None:
        params.append(thread_id)
        clause += ' AND t.id = ?'
    with _get_conn() as conn:
        rows = conn.execute(
            f'''
            SELECT t.id AS thread_id, t.name AS thread_name,
                   tb.kanda, tb.sarga, tb.sloka_num
            FROM thread_bookmarks tb
            JOIN reading_threads t ON t.id = tb.thread_id
            {clause}
            ORDER BY t.name, tb.kanda, tb.sarga, tb.sloka_num
            ''',
            params,
        ).fetchall()
    return rows


def _parse_thread_id(request: Request) -> int | None:
    raw = request.query_params.get('thread')
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _get_user(user_id: int):
    with _get_conn() as conn:
        return conn.execute(
            'SELECT id, first_name, last_name, birth_date FROM users WHERE id = ?',
            (user_id,),
        ).fetchone()


def _get_or_create_user(first_name: str, last_name: str, birth_date: str) -> int:
    clean_first = first_name.strip()
    clean_last = last_name.strip()
    clean_birth = birth_date.strip()
    if not clean_first or not clean_last or not clean_birth:
        return DEFAULT_USER_ID
    with _get_conn() as conn:
        row = conn.execute(
            '''
            SELECT id FROM users
            WHERE first_name = ? AND last_name = ? AND birth_date = ?
            ''',
            (clean_first, clean_last, clean_birth),
        ).fetchone()
        if row:
            return int(row['id'])
        user_id = conn.execute(
            '''
            INSERT INTO users (first_name, last_name, birth_date)
            VALUES (?, ?, ?)
            ''',
            (clean_first, clean_last, clean_birth),
        ).lastrowid
    return int(user_id)


def _get_user_id(request: Request) -> int | None:
    raw = request.cookies.get('valmiki_user')
    if not raw:
        return None
    try:
        user_id = int(raw)
    except ValueError:
        return None
    if not _get_user(user_id):
        return None
    return user_id


def _set_user_cookie(response: Response, user_id: int) -> None:
    max_age = 60 * 60 * 24 * 365 * 10
    response.set_cookie(
        'valmiki_user',
        str(user_id),
        max_age=max_age,
        httponly=True,
        samesite='lax',
        path='/',
    )


def _login_redirect(request: Request) -> Response:
    next_path = request.url.path
    if request.url.query:
        next_path = f'{next_path}?{request.url.query}'
    next_param = quote(next_path, safe='')
    return RedirectResponse(f'/login?next={next_param}', status_code=303)


def _resolve_thread_id(thread_id: int | None, user_id: int) -> int:
    try:
        if thread_id is not None:
            thread = _get_thread(thread_id, user_id)
            if thread:
                return int(thread['id'])
        return _ensure_default_thread_for_user(user_id)
    except sqlite3.OperationalError:
        return thread_id or 1


def _with_thread(url: str, thread_id: int) -> str:
    return f'{url}?thread={thread_id}'


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'


def _thread_title_fragment(thread_id: int, title: str):
    return Div(
        H3(
            title,
            style='color:white; font-size:1.2em; cursor:pointer',
            **{
                'hx-get': f'/threads/{thread_id}/rename-form',
                'hx-target': f'#thread-title-{thread_id}',
                'hx-swap': 'outerHTML',
            },
        ),
        id=f'thread-title-{thread_id}',
    )


def _thread_rename_form_fragment(thread_id: int, title: str):
    return Form(
        Input(
            type='text',
            name='name',
            value=title,
            autofocus='true',
            style='padding:6px 8px; width:100%; border-radius:6px; border:1px solid #333; background:#0f0f0f; color:white',
        ),
        Div(
            Button('Save', type='submit',
                   style='padding:6px 10px; border:none; border-radius:6px; background:#fbbf24; color:black; cursor:pointer'),
            Button(
                'Cancel',
                type='button',
                style='padding:6px 10px; border:none; border-radius:6px; background:#2d2d2d; color:#fbbf24; cursor:pointer',
                **{
                    'hx-get': f'/threads/{thread_id}/title',
                    'hx-target': f'#thread-title-{thread_id}',
                    'hx-swap': 'outerHTML',
                },
            ),
            style='display:flex; gap:8px; margin-top:8px',
        ),
        id=f'thread-title-{thread_id}',
        action=f'/threads/{thread_id}/rename',
        method='post',
        **{
            'hx-post': f'/threads/{thread_id}/rename',
            'hx-target': f'#thread-title-{thread_id}',
            'hx-swap': 'outerHTML',
        },
    )


def _new_thread_prompt_fragment():
    initial_sargas = _get_kanda_total_sargas(1)
    if initial_sargas <= 0:
        initial_sargas = 100
    return Form(
        Input(
            type='text',
            name='name',
            placeholder='New reading thread name',
            autofocus='true',
            style='padding:8px 10px; width:100%; border-radius:8px; border:1px solid #333; background:#0f0f0f; color:white',
        ),
        Div(
            Div(
                P('Kanda', style='color:#888; font-size:0.9em; margin-bottom:6px'),
                Select(
                    *[Option(str(k), value=str(k)) for k in range(1, MAX_KANDA + 1)],
                    name='kanda',
                    **{
                        'hx-get': '/threads/sarga-options',
                        'hx-target': '#new-thread-sarga',
                        'hx-swap': 'outerHTML',
                        'hx-trigger': 'change',
                    },
                    style='padding:8px 10px; border-radius:8px; border:1px solid #333; background:#0f0f0f; color:white; width:100%'
                ),
                style='flex:1'
            ),
            Div(
                P('Sarga', style='color:#888; font-size:0.9em; margin-bottom:6px'),
                Select(
                    *[Option(str(s), value=str(s)) for s in range(1, initial_sargas + 1)],
                    name='sarga',
                    id='new-thread-sarga',
                    style='padding:8px 10px; border-radius:8px; border:1px solid #333; background:#0f0f0f; color:white; width:100%'
                ),
                style='flex:1'
            ),
            style='display:flex; gap:12px; margin-top:10px'
        ),
        Div(
            Button('Create', type='submit',
                   style='padding:8px 12px; border:none; border-radius:8px; background:#fbbf24; color:black; cursor:pointer'),
            Button(
                'Cancel',
                type='button',
                style='padding:8px 12px; border:none; border-radius:8px; background:#2d2d2d; color:#fbbf24; cursor:pointer',
                **{
                    'hx-get': '/threads/new-button',
                    'hx-target': '#new-thread-cta',
                    'hx-swap': 'outerHTML',
                },
            ),
            style='display:flex; gap:8px; margin-top:10px',
        ),
        id='new-thread-cta',
        action='/threads/new',
        method='get',
        **{
            'hx-get': '/threads/new',
            'hx-target': '#threads-list',
            'hx-swap': 'afterbegin',
        },
    )


def _new_thread_button_fragment():
    return A(
        'New Reading Thread',
        href='#',
        id='new-thread-cta',
        style='display:block; padding:12px 14px; margin:8px 0; background:#2d2d2d; color:#fbbf24; text-decoration:none; border-radius:8px; font-size:1.05em',
        **{
            'hx-get': '/threads/new-form',
            'hx-target': '#new-thread-cta',
            'hx-swap': 'outerHTML',
        },
    )

def _thread_card_fragment(thread):
    k = thread['kanda']
    s = thread['sarga']
    sl = thread['sloka_num']
    if k is None or s is None or sl is None:
        resume_url = _with_thread('/kanda/1/sarga/1/sloka/1', thread['id'])
        progress_text = 'No progress yet'
    else:
        resume_url = _with_thread(f'/kanda/{k}/sarga/{s}/sloka/{sl}', thread['id'])
        progress_text = f'Kanda {k} Sarga {s} Sloka {sl}'

    return Div(
        Div(
            _thread_title_fragment(thread['id'], thread['name']),
            P(progress_text, style='color:#ccc; margin-top:8px'),
            style='flex:1'
        ),
        Div(
            A('Resume', href=resume_url,
              style='display:inline-block; padding:8px 12px; background:#2d2d2d; color:#fbbf24; text-decoration:none; border-radius:6px; font-size:0.95em; margin-right:8px'),
            A('Bookmarks', href=_with_thread('/bookmarks', thread['id']),
              style='display:inline-block; padding:8px 12px; background:#1a1a1a; color:#fbbf24; text-decoration:none; border-radius:6px; font-size:0.95em'),
            Button(
                'Delete',
                type='button',
                style='display:inline-block; padding:8px 12px; background:#3a1a1a; color:#fbbf24; border:none; border-radius:6px; font-size:0.95em; cursor:pointer',
                **{
                    'hx-post': f'/threads/{thread["id"]}/delete',
                    'hx-target': f'#thread-card-{thread["id"]}',
                    'hx-swap': 'outerHTML',
                    'hx-confirm': 'Delete this thread?',
                },
            ),
            style='display:flex; gap:8px; align-items:center'
        ),
        id=f'thread-card-{thread["id"]}',
        style='padding:16px; background:#111; border:1px solid #222; border-radius:10px; margin-bottom:16px'
    )


_init_db()


@rt('/manifest.webmanifest')
def manifest():
    """Web app manifest for PWA."""
    payload = {
        "name": "Valmiki Ramayana Reader",
        "short_name": "Valmiki",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": [
            {
                "src": "/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml"
            }
        ]
    }
    return JSONResponse(payload, media_type='application/manifest+json')


@rt('/static/favicon.png')
def favicon_png():
    """Serve site favicon."""
    path = Path(__file__).resolve().parents[2] / 'static' / 'favicon.png'
    if not path.exists():
        return Response('favicon not found', status_code=404)
    return Response(path.read_bytes(), media_type='image/png')


@rt('/icon.svg')
def icon():
    """Simple SVG icon for PWA."""
    svg = """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
      <defs>
        <linearGradient id="g" x1="0" x2="1" y1="0" y2="1">
          <stop offset="0%" stop-color="#fbbf24"/>
          <stop offset="100%" stop-color="#f59e0b"/>
        </linearGradient>
      </defs>
      <rect width="256" height="256" rx="48" fill="#0b0b0b"/>
      <circle cx="128" cy="128" r="76" fill="url(#g)"/>
      <path d="M128 70c-16 24-24 46-24 66 0 19 10 36 24 50 14-14 24-31 24-50 0-20-8-42-24-66z"
            fill="#0b0b0b"/>
    </svg>
    """.strip()
    return Response(svg, media_type='image/svg+xml')


@rt('/.well-known/assetlinks.json')
def assetlinks():
    """Serve Digital Asset Links for TWA verification."""
    path = Path(__file__).resolve().parents[2] / 'assetlinks.json'
    if not path.exists():
        return Response('assetlinks.json not found', status_code=404)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return Response('Invalid assetlinks.json', status_code=500)
    return JSONResponse(payload)


@rt('/login')
def login(request: Request):
    user_id = _get_user_id(request)
    if user_id:
        next_path = request.query_params.get('next')
        if next_path and next_path.startswith('/'):
            return RedirectResponse(next_path, status_code=303)
        return RedirectResponse('/', status_code=303)
    next_path = request.query_params.get('next', '/')
    if not next_path.startswith('/'):
        next_path = '/'
    return Html(
        Head(
            Title('Valmiki - Login'),
            Link(rel='icon', href='/static/favicon.png', type='image/png'),
            Meta(name='viewport', content='width=device-width, initial-scale=1, viewport-fit=cover'),
            Style('''
                * { margin:0; padding:0; box-sizing:border-box; }
                body { font-family: "Noto Sans", system-ui, -apple-system, sans-serif; font-weight: 500; background:black; color:white; min-height:100vh; display:flex; align-items:flex-start; justify-content:center; padding:48px 24px 24px; }
                .card { width:100%; max-width:520px; padding:32px; background:#0f0f0f; border:1px solid #222; border-radius:14px; box-shadow:0 18px 60px rgba(0,0,0,0.45); }
                label { display:block; font-size:0.95em; color:#bbb; margin-bottom:6px; }
                input { width:100%; padding:12px 14px; border-radius:10px; border:1px solid #333; background:#111; color:white; font-size:1em; }
                .row { margin-bottom:16px; }
                button { padding:12px 16px; border-radius:10px; border:none; background:#fbbf24; color:black; font-weight:600; cursor:pointer; width:100%; }
                .hint { color:#888; font-size:0.9em; margin-top:6px; }
            '''),
        ),
        Body(
            Div(
                H1('Sign In', style='color:#fbbf24; margin-bottom:8px; text-align:center'),
                P('This keeps your reading threads separate on this device.', style='text-align:center; color:#888; margin-bottom:20px'),
                Form(
                    Div(
                        Label('First Name'),
                        Input(type='text', name='first_name', required=True, autocomplete='given-name'),
                        class_='row',
                    ),
                    Div(
                        Label('Last Name'),
                        Input(type='text', name='last_name', required=True, autocomplete='family-name'),
                        class_='row',
                    ),
                    Div(
                        Label('Birth Date'),
                        Input(type='date', name='birth_date', required=True, autocomplete='bday'),
                        class_='row',
                    ),
                    Input(type='hidden', name='next', value=next_path),
                    Button('Continue', type='submit'),
                    class_='card',
                    action='/login/submit',
                    method='post',
                ),
            )
        ),
    )


@rt('/login/submit', methods=['POST'])
async def login_post(request: Request):
    form = await request.form()
    first_name = str(form.get('first_name', '')).strip()
    last_name = str(form.get('last_name', '')).strip()
    birth_date = str(form.get('birth_date', '')).strip()
    next_path = str(form.get('next', '/')).strip()
    if not next_path.startswith('/'):
        next_path = '/'
    user_id = _get_or_create_user(first_name, last_name, birth_date)
    with _get_conn() as conn:
        legacy_flag = conn.execute(
            'SELECT value FROM app_meta WHERE key = ?',
            ('legacy_threads_owner',),
        ).fetchone()
        if legacy_flag is None:
            legacy_threads = conn.execute(
                'SELECT COUNT(*) AS count FROM reading_threads WHERE user_id = ?',
                (DEFAULT_USER_ID,),
            ).fetchone()
            legacy_count = int(legacy_threads['count']) if legacy_threads else 0
            if legacy_count > 0 and user_id != DEFAULT_USER_ID:
                conn.execute(
                    'UPDATE reading_threads SET user_id = ? WHERE user_id = ?',
                    (user_id, DEFAULT_USER_ID),
                )
                conn.execute(
                    'INSERT INTO app_meta (key, value) VALUES (?, ?)',
                    ('legacy_threads_owner', str(user_id)),
                )
    response = RedirectResponse(next_path, status_code=303)
    _set_user_cookie(response, user_id)
    return response


@rt('/logout')
def logout():
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie('valmiki_user', path='/')
    return response


@rt('/threads/new')
def new_thread(request: Request):
    """Create a new reading thread and redirect to its start."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    name = request.query_params.get('name', '')
    kanda = _parse_int(request.query_params.get('kanda'), 1)
    sarga = _parse_int(request.query_params.get('sarga'), 1)
    sloka_num = _parse_int(request.query_params.get('sloka'), 1)
    thread_id = _create_thread_for_user(user_id, name, kanda, sarga, sloka_num)
    if request.headers.get('HX-Request') == 'true':
        thread = _get_thread(thread_id, user_id)
        return _thread_card_fragment(thread)
    return RedirectResponse(
        _with_thread(f'/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}', thread_id)
    )


@rt('/threads/new-form')
def new_thread_form():
    """Inline create form for new thread."""
    return _new_thread_prompt_fragment()


@rt('/threads/new-button')
def new_thread_button():
    """Inline button for new thread."""
    return _new_thread_button_fragment()


@rt('/threads/sarga-options')
def sarga_options(request: Request):
    """Return sarga options for a selected kanda."""
    kanda = _parse_int(request.query_params.get('kanda'), 1)
    total_sargas = _get_kanda_total_sargas(kanda)
    if total_sargas <= 0:
        total_sargas = 100
    return Select(
        *[Option(str(s), value=str(s)) for s in range(1, total_sargas + 1)],
        name='sarga',
        id='new-thread-sarga',
        style='padding:8px 10px; border-radius:8px; border:1px solid #333; background:#0f0f0f; color:white; flex:1'
    )


@rt('/threads/{thread_id}/rename')
async def rename_thread(thread_id: int, request: Request):
    """Rename an existing reading thread."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    form = await request.form()
    name = str(form.get('name', '')).strip()
    _rename_thread(thread_id, user_id, name)
    thread = _get_thread(thread_id, user_id)
    if not thread:
        return Response('', status_code=404)
    return _thread_title_fragment(thread_id, thread['name'])


@rt('/threads/{thread_id}/rename-form')
def rename_thread_form(thread_id: int, request: Request):
    """Return inline rename form."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    thread = _get_thread(thread_id, user_id)
    if not thread:
        return Response('', status_code=404)
    return _thread_rename_form_fragment(thread_id, thread['name'])


@rt('/threads/{thread_id}/title')
def thread_title(thread_id: int, request: Request):
    """Return thread title fragment."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    thread = _get_thread(thread_id, user_id)
    if not thread:
        return Response('', status_code=404)
    return _thread_title_fragment(thread_id, thread['name'])


@rt('/threads/{thread_id}/delete', methods=['POST'])
def delete_thread(thread_id: int, request: Request):
    """Delete a reading thread."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    with _get_conn() as conn:
        conn.execute(
            'DELETE FROM reading_threads WHERE id = ? AND user_id = ?',
            (thread_id, user_id),
        )
    return Response('')


@rt('/')
def home(request: Request):
    """Home page with reading threads."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    user = _get_user(user_id)
    first_name = user['first_name'] if user else 'Your'
    threads = _get_threads(user_id)
    thread_cards = []

    for thread in threads:
        thread_cards.append(_thread_card_fragment(thread))

    new_thread_links = [
        _new_thread_button_fragment(),
    ]

    return Html(
        Head(
            Title('Valmiki Ramayana Reader'),
            Link(rel='icon', href='/static/favicon.png', type='image/png'),
            Meta(name='viewport', content='width=device-width, initial-scale=1, viewport-fit=cover'),
            Meta(name='theme-color', content='#000000'),
            Meta(name='apple-mobile-web-app-capable', content='yes'),
            Meta(name='apple-mobile-web-app-status-bar-style', content='black-translucent'),
            Meta(name='apple-mobile-web-app-title', content='Valmiki'),
            Link(rel='manifest', href='/manifest.webmanifest'),
            Style('''
                * { margin:0; padding:0; box-sizing:border-box; }
                body { font-family: "Noto Sans", system-ui, -apple-system, sans-serif; font-weight: 500; }
            '''),
            Script(src='https://unpkg.com/htmx.org@1.9.12')
        ),
        Body(
            Div(
                Div(
                    A('⎋', href='/logout',
                      style='text-decoration:none; color:#fbbf24; font-size:1.3em; padding:6px 10px; border:1px solid #333; border-radius:8px'),
                    style='position:fixed; top:20px; right:20px; z-index:1000'
                ),
                H1('వాల్మీకి రామాయణం', style='text-align:center; color:#fbbf24; padding:30px; font-size:2.5em'),
                H2('Valmiki Ramayana Reader', style='text-align:center; color:#888; padding:10px; font-size:1.5em'),
                Div(
                    H3(f"{first_name}'s Reading Threads", style='color:#fbbf24; margin:10px 0 20px; text-align:center'),
                    Div(*thread_cards, id='threads-list') if thread_cards else Div(P('No threads yet', style='text-align:center; color:#888; padding:20px'), id='threads-list'),
                    Hr(style='border:none; border-top:1px solid #333; margin:30px 0'),
                    H3('Start a New Thread', style='color:#fbbf24; margin:10px 0 20px; text-align:center'),
                    *new_thread_links,
                    style='max-width:700px; margin:0 auto; padding:20px'
                ),
                Div(
                    P(
                        A('Source: valmiki.iitk.ac.in',
                          href='https://www.valmiki.iitk.ac.in/',
                          style='color:#666; text-decoration:none'),
                        style='text-align:center; font-size:0.9em; padding:20px 0'
                    ),
                    style='border-top:1px solid #222; margin-top:30px'
                ),
                style='min-height:100vh; background:black'
            )
        )
    )


@rt('/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}')
async def sloka(kanda: int, sarga: int, sloka_num: int, request: Request):
    """Display a single sloka with navigation."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    thread_id = _resolve_thread_id(_parse_thread_id(request), user_id)
    
    # Get or create SargaReader for this sarga
    try:
        sr = _get_sarga_reader(kanda, sarga)
    except Exception as e:
        return Response(f'Error loading sarga: {str(e)}', status_code=500)
    
    # Validate sloka number
    if sloka_num < 1 or sloka_num > len(sr):
        return Response('Sloka not found', status_code=404)
    
    # Get sloka data
    try:
        sloka_data = sr[sloka_num - 1]  # Convert to 0-based index
    except Exception as e:
        return Response(f'Error loading sloka: {str(e)}', status_code=500)
    
    # Calculate previous URL
    if sloka_num > 1:
        prev_url = f'/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num-1}'
    else:
        # Need to go to previous sarga or previous kanda
        if sarga > 1:
            try:
                prev_sr = _get_sarga_reader(kanda, sarga-1)
            except Exception:
                prev_sr = None
            prev_url = f'/kanda/{kanda}/sarga/{sarga-1}/sloka/{len(prev_sr)}' if prev_sr else '#'
        elif kanda > 1:
            prev_kanda = kanda - 1
            prev_sarga = _get_kanda_total_sargas(prev_kanda)
            if prev_sarga <= 0:
                prev_url = '#'
            else:
                try:
                    prev_sr = _get_sarga_reader(prev_kanda, prev_sarga)
                except Exception:
                    prev_sr = None
                prev_url = f'/kanda/{prev_kanda}/sarga/{prev_sarga}/sloka/{len(prev_sr)}' if prev_sr else '#'
        else:
            prev_url = '#'
    
    # Calculate next URL
    if sloka_num < len(sr):
        next_url = f'/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num+1}'
    else:
        total_sargas = _get_kanda_total_sargas(kanda)
        if total_sargas and sarga >= total_sargas and kanda >= MAX_KANDA:
            next_url = '#'
        elif total_sargas and sarga >= total_sargas:
            next_url = f'/kanda/{kanda+1}/sarga/1/sloka/1'
        else:
            # Go to next sarga
            next_url = f'/kanda/{kanda}/sarga/{sarga+1}/sloka/1'

    if prev_url != '#':
        prev_url = _with_thread(prev_url, thread_id)
    if next_url != '#':
        next_url = _with_thread(next_url, thread_id)
    
    # Render sloka content
    sloka_text = sloka_data['sloka_text']
    bhaavam_en = sloka_data['bhaavam_en']
    
    # English translation (Telugu sloka shown above)
    bhaavam = bhaavam_en
    if 'ఇత్యార్షే' in bhaavam:
        bhaavam = bhaavam.split('ఇత్యార్షే', 1)[0].strip()
    
    # Check if bookmarked
    is_bookmarked = _is_bookmarked(thread_id, kanda, sarga, sloka_num)
    bookmark_fill = '#fbbf24' if is_bookmarked else 'none'
    
    # Bookmark icon SVG
    bookmark_icon = NotStr(f'''
        <svg id="bookmark-btn" width="24" height="24" viewBox="0 0 24 24" 
             fill="{bookmark_fill}" stroke="#fbbf24" stroke-width="2" style="cursor:pointer">
            <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path>
        </svg>
    ''')
    
    # Sloka content
    end_marker = None
    if sloka_num == len(sr):
        kanda_name = KANDA_NAMES.get(kanda, f'Kāṇḍa {kanda}')
        end_marker = Div(
            Div('✦ ✦ ✦', style='color:#fbbf24; text-align:center; font-size:1.2em; margin:30px 0 10px'),
            P(f'End of {_ordinal(sarga)} Sarga of {kanda_name}', style='text-align:center; color:#888; font-size:1.05em'),
            style='margin-top:30px'
        )

    sloka_content = Div(
        H4(sloka_data['sloka_num'], style='text-align:center; font-size:1.5em; color:#888; margin-bottom:20px'),
        H3(sloka_text, cls='telugu-text', style='text-align:center; white-space:pre-line; font-size:1.8em; line-height:1.8; margin-bottom:40px'),
        H2(bhaavam, style='text-align:center; font-size:1.3em; line-height:1.6; color:#ccc; max-width:700px; margin:0 auto'),
        end_marker if end_marker else None,
        style='max-width:900px; margin:0 auto'
    )
    
    progress_pct = (sloka_num / max(len(sr), 1)) * 100
    kanda_total = _get_kanda_total_slokas(kanda)
    kanda_done = _get_kanda_progress_slokas(kanda, sarga, sloka_num)
    kanda_pct = (kanda_done / max(kanda_total, 1)) * 100
    ramayana_total = _get_ramayana_total_slokas()
    ramayana_done = _get_ramayana_progress_slokas(kanda, sarga, sloka_num)
    ramayana_pct = (ramayana_done / max(ramayana_total, 1)) * 100

    sloka_view = Div(
        Div(
            '',
            id='mark-read',
            **{
                'hx-post': f'/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/mark-read?thread={thread_id}',
                'hx-trigger': 'load',
                'hx-swap': 'none',
            }
        ),
        # Rotation hint overlay
        Div(
            Span('🔄', style='font-size:1.5em'),
            Div(
                Div('Rotate your device for better reading', style='font-weight:600'),
                Div('మెరుగైన పఠన అనుభవం కోసం మీ పరికరాన్ని అడ్డంగా తిప్పండి', style='font-size:0.9em; margin-top:4px; opacity:0.95'),
                style='line-height:1.3'
            ),
            cls='rotation-hint'
        ),

        # Sarga progress bar
        Div(
            Div(
                style=f'width:{progress_pct:.2f}%; height:100%; background:#fbbf24; transition: width 0.2s ease'
            ),
            style='position:fixed; top:0; left:0; right:0; height:6px; background:#1a1a1a; z-index:9999'
        ),

        # Kanda progress bar
        Div(
            Div(
                style=f'width:{kanda_pct:.2f}%; height:100%; background:#f59e0b; transition: width 0.2s ease'
            ),
            style='position:fixed; top:6px; left:0; right:0; height:6px; background:#111; z-index:9998'
        ),

        # Ramayana progress bar
        Div(
            Div(
                style=f'width:{ramayana_pct:.2f}%; height:100%; background:#d97706; transition: width 0.2s ease'
            ),
            style='position:fixed; top:12px; left:0; right:0; height:6px; background:#0d0d0d; z-index:9997'
        ),
        
        # Top-right controls
        Div(
            A(
                bookmark_icon,
                href='#',
                id='bookmark-link',
                style='text-decoration:none',
                **{
                    'data-bookmark-url': f'/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/bookmark?thread={thread_id}'
                }
            ),
            A('📚', href=_with_thread('/bookmarks', thread_id), 
              style='text-decoration:none; font-size:1.5em; margin-left:15px'),
            A('⛶', href='#', id='fullscreen-btn',
              style='text-decoration:none; font-size:1.5em; margin-left:15px'),
            A('🏠', href='/', 
              style='text-decoration:none; font-size:1.5em; margin-left:15px'),
            style='position:fixed; top:20px; right:20px; z-index:1000; display:flex; gap:10px; align-items:center'
        ),
        
        # Fullscreen hint (shown when user opted in)
        Div(
            'Tap to enter fullscreen',
            id='fullscreen-hint',
            style='display:none; position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:#111; color:#fbbf24; padding:10px 14px; border:1px solid #333; border-radius:999px; z-index:1000; font-size:0.95em; cursor:pointer'
        ),
        
        # Main content with navigation
        Div(
            # Left arrow
            Div(
                A('←', href=prev_url, id='prev',
                  style='font-size:3em; text-decoration:none; display:flex; align-items:center; justify-content:center; height:100vh; background:#1a1a1a; color:white; width:100%; transition: background 0.2s',
                  onmouseover='this.style.background="#2d2d2d"',
                  onmouseout='this.style.background="#1a1a1a"',
                  **{
                      'hx-get': prev_url,
                      'hx-target': '#sloka-view',
                      'hx-swap': 'outerHTML',
                      'hx-push-url': 'true',
                  }),
                style='flex:0 0 8%'
            ),
            
            # Center content (clickable to go next)
            Div(
                sloka_content,
                style='flex:1; padding:40px 20px; display:flex; align-items:center; justify-content:center; color:white; cursor:pointer',
                **{
                    'hx-get': next_url,
                    'hx-target': '#sloka-view',
                    'hx-swap': 'outerHTML',
                    'hx-push-url': 'true',
                }
            ),
            
            # Right arrow
            Div(
                A('→', href=next_url, id='next',
                  style='font-size:3em; text-decoration:none; display:flex; align-items:center; justify-content:center; height:100vh; background:black; color:white; width:100%; transition: background 0.2s',
                  onmouseover='this.style.background="#1a1a1a"',
                  onmouseout='this.style.background="black"',
                  **{
                      'hx-get': next_url,
                      'hx-target': '#sloka-view',
                      'hx-swap': 'outerHTML',
                      'hx-push-url': 'true',
                  }),
                style='flex:0 0 8%'
            ),
            
            style='display:flex; min-height:100vh; background:black'
        ),
        
        # JavaScript for keyboard navigation
        Script(f'''
            // Keyboard navigation (reset handler on swap)
            if (window._slokaKeyHandler) {{
                document.removeEventListener('keydown', window._slokaKeyHandler);
            }}
            window._slokaKeyHandler = (e) => {{
                if (e.key === 'ArrowLeft') {{
                    e.preventDefault();
                    document.getElementById('prev').click();
                }}
                if (e.key === 'ArrowRight') {{
                    e.preventDefault();
                    document.getElementById('next').click();
                }}
            }};
            document.addEventListener('keydown', window._slokaKeyHandler);
        '''),
        
        id='sloka-view',
        style='background:black'
    )

    if request.headers.get('HX-Request') == 'true':
        return sloka_view

    return Html(
        Head(
            Title(f'Sloka {sloka_data["sloka_num"]} - Valmiki Ramayana'),
            Link(rel='icon', href='/static/favicon.png', type='image/png'),
            Link(rel='preconnect', href='https://fonts.googleapis.com'),
            Link(rel='preconnect', href='https://fonts.gstatic.com', crossorigin='true'),
            Link(rel='stylesheet', href='https://fonts.googleapis.com/css2?family=Noto+Sans+Telugu:wght@500&family=Noto+Sans:wght@500&display=swap'),
            Meta(name='viewport', content='width=device-width, initial-scale=1, viewport-fit=cover'),
            Meta(name='theme-color', content='#000000'),
            Meta(name='apple-mobile-web-app-capable', content='yes'),
            Meta(name='apple-mobile-web-app-status-bar-style', content='black-translucent'),
            Meta(name='apple-mobile-web-app-title', content='Valmiki'),
            Link(rel='manifest', href='/manifest.webmanifest'),
            Script(src='https://unpkg.com/htmx.org@1.9.12'),
            Style('''
                * { margin:0; padding:0; box-sizing:border-box; font-family: "Noto Sans", system-ui, -apple-system, sans-serif; font-weight: 500; }
                .telugu-text { font-family: "Noto Sans Telugu", "Noto Sans", sans-serif; font-weight: 500; }
                
                /* Rotation hint - hidden by default */
                .rotation-hint {
                    display: none !important;
                }
                
                /* Show rotation hint ONLY on mobile/tablet portrait mode */
                @media screen and (max-width: 1024px) and (orientation: portrait) {
                    .rotation-hint {
                        display: flex !important;
                        align-items: center;
                        justify-content: center;
                        gap: 12px;
                        position: fixed;
                        top: 0;
                        left: 0;
                        right: 0;
                        background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%);
                        color: black;
                        padding: 12px 20px;
                        text-align: center;
                        z-index: 10000;
                        font-weight: 600;
                        font-size: 1.3em;
                        box-shadow: 0 2px 10px rgba(251, 191, 36, 0.3);
                        animation: slideDown 0.3s ease-out;
                    }
                    
                    @keyframes slideDown {
                        from { transform: translateY(-100%); }
                        to { transform: translateY(0); }
                    }
                }
            ''')
        ),
        Body(
            sloka_view,
            Script('''
                const requestFs = () => {
                    const el = document.documentElement;
                    const req = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
                    if (req) req.call(el);
                };

                const refreshFsHint = () => {
                    const hint = document.getElementById('fullscreen-hint');
                    if (!hint) return;
                    if (localStorage.getItem('fsPreferred') === '1' && !document.fullscreenElement) {
                        hint.style.display = 'flex';
                    } else {
                        hint.style.display = 'none';
                    }
                };

                document.addEventListener('click', async (e) => {
                    const bookmarkLink = e.target.closest('#bookmark-link');
                    if (bookmarkLink) {
                        e.preventDefault();
                        const url = bookmarkLink.getAttribute('data-bookmark-url');
                        const res = await fetch(url, { method: 'POST' });
                        const data = await res.json();
                        const svg = document.getElementById('bookmark-btn');
                        if (svg) svg.style.fill = data.bookmarked ? '#fbbf24' : 'none';
                        return;
                    }

                    const fsBtn = e.target.closest('#fullscreen-btn');
                    if (fsBtn) {
                        e.preventDefault();
                        localStorage.setItem('fsPreferred', '1');
                        requestFs();
                        refreshFsHint();
                        return;
                    }

                    const fsHint = e.target.closest('#fullscreen-hint');
                    if (fsHint) {
                        requestFs();
                        refreshFsHint();
                    }
                });

                document.addEventListener('htmx:afterSwap', refreshFsHint);
                refreshFsHint();
            ''')
        )
    )


@rt('/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/bookmark', methods=['POST'])
def toggle_bookmark(kanda: int, sarga: int, sloka_num: int, request: Request):
    """Toggle bookmark status for a sloka."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    thread_id = _resolve_thread_id(_parse_thread_id(request), user_id)
    bookmarked = _toggle_bookmark(thread_id, kanda, sarga, sloka_num)
    
    return {'bookmarked': bookmarked}


@rt('/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/mark-read', methods=['POST'])
def mark_read(kanda: int, sarga: int, sloka_num: int, request: Request):
    """Mark a sloka as last read position."""
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    thread_id = _resolve_thread_id(_parse_thread_id(request), user_id)
    _update_progress(thread_id, kanda, sarga, sloka_num)
    return {'success': True}


@rt('/bookmarks')
def get_bookmarks(request: Request):
    """Display all bookmarked slokas."""
    thread_id = _parse_thread_id(request)
    user_id = _get_user_id(request)
    if not user_id:
        return _login_redirect(request)
    thread = _get_thread(thread_id, user_id) if thread_id is not None else None

    # Create bookmark links
    bookmark_links = []
    rows = _get_thread_bookmarks(user_id, thread_id)
    if thread_id is not None and thread:
        grouped = {thread['name']: rows}
    else:
        grouped = {}
        for row in rows:
            grouped.setdefault(row['thread_name'], []).append(row)

    for thread_name, items in grouped.items():
        bookmark_links.append(
            H2(thread_name, style='color:#fbbf24; padding:10px 0; text-align:center')
        )
        for row in items:
            k = row['kanda']
            s = row['sarga']
            sl = row['sloka_num']
            link_text = f'కాండ {k} సర్గ {s} శ్లోక {sl}'
            bookmark_links.append(
                Div(
                    A(link_text,
                      href=_with_thread(f'/kanda/{k}/sarga/{s}/sloka/{sl}', row['thread_id']),
                      style='color:white; text-decoration:none; font-size:1.3em; padding:15px; display:block; border-bottom:1px solid #333; transition: background 0.2s',
                      onmouseover='this.style.background="#1a1a1a"',
                      onmouseout='this.style.background="transparent"')
                )
            )
    
    # No bookmarks message
    if not bookmark_links:
        no_bookmarks = P('పేజీలు ఏవీ గుర్తించబడలేదు', style='text-align:center; color:#888; padding:40px; font-size:1.2em')
        bookmark_links = [no_bookmarks]
    
    # Title text
    title = 'పేజీలు గుర్తించబడ్డాయి'
    if thread:
        title = f'{title} - {thread["name"]}'
    
    return Html(
        Head(
            Title(f'{title} - Valmiki Ramayana'),
            Link(rel='icon', href='/static/favicon.png', type='image/png'),
            Meta(name='viewport', content='width=device-width, initial-scale=1, viewport-fit=cover'),
            Meta(name='theme-color', content='#000000'),
            Meta(name='apple-mobile-web-app-capable', content='yes'),
            Meta(name='apple-mobile-web-app-status-bar-style', content='black-translucent'),
            Meta(name='apple-mobile-web-app-title', content='Valmiki'),
            Link(rel='manifest', href='/manifest.webmanifest'),
            Style('* { margin:0; padding:0; box-sizing:border-box; font-family: "Noto Sans", system-ui, -apple-system, sans-serif; font-weight: 500; }')
        ),
        Body(
            Div(
                # Header
                Div(
                    A('← Back', href='/', style='color:#fbbf24; text-decoration:none; font-size:1.1em'),
                    style='padding:20px'
                ),
                H1(title, style='text-align:center; color:#fbbf24; padding:30px; font-size:2em'),
                
                # Bookmarks list
                Div(*bookmark_links, style='max-width:600px; margin:0 auto; padding:20px'),
                
                style='background:black; min-height:100vh; color:white'
            )
        )
    )


# For running the app standalone
if __name__ == '__main__':
    import uvicorn
    print("Starting Valmiki Ramayana Reader...")
    print("Visit: http://localhost:8000")

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
