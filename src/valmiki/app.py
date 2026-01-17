"""FastHTML web application for Valmiki Ramayana Reader."""

import sqlite3
import dill
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
sarga_cache_dir = (Path(__file__).resolve().parents[2] / 'data' / 'sarga_cache')

# Translation caches (for future translator integration)
translation_cache = {
    'te': {},  # Telugu translations: {english_text: telugu_text}
    'tg': {},  # Telangana translations: {english_text: telangana_text}
}


def _get_conn():
    """Open a SQLite connection with a row factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _sarga_cache_path(kanda: int, sarga: int) -> Path:
    return sarga_cache_dir / f'kanda_{kanda}_sarga_{sarga}.dill'


def _load_sarga_from_cache(kanda: int, sarga: int):
    path = _sarga_cache_path(kanda, sarga)
    if not path.exists():
        return None
    try:
        with path.open('rb') as handle:
            return dill.load(handle)
    except Exception:
        return None


def _save_sarga_to_cache(kanda: int, sarga: int, sr: SargaReader) -> None:
    sarga_cache_dir.mkdir(parents=True, exist_ok=True)
    path = _sarga_cache_path(kanda, sarga)
    with path.open('wb') as handle:
        dill.dump(sr, handle)


def _get_sarga_reader(kanda: int, sarga: int) -> SargaReader:
    sr = sarga_readers.get((kanda, sarga))
    if sr is not None:
        return sr
    sr = _load_sarga_from_cache(kanda, sarga)
    if sr is not None:
        sarga_readers[(kanda, sarga)] = sr
        return sr
    sr = SargaReader(kanda, sarga, lang='te')
    sarga_readers[(kanda, sarga)] = sr
    _save_sarga_to_cache(kanda, sarga, sr)
    return sr


def _init_db():
    """Initialize SQLite schema if needed."""
    with _get_conn() as conn:
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS reading_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                language TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def _next_thread_name() -> str:
    with _get_conn() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS count FROM reading_threads',
        ).fetchone()
    count = int(row['count']) if row else 0
    return f'Thread {count + 1}'


def _create_thread(name: str | None, kanda: int, sarga: int, sloka_num: int) -> int:
    thread_name = name.strip() if name else ''
    if not thread_name:
        thread_name = _next_thread_name()
    with _get_conn() as conn:
        thread_id = conn.execute(
            'INSERT INTO reading_threads (name, language) VALUES (?, ?)',
            (thread_name, DEFAULT_LANGUAGE),
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
    with _get_conn() as conn:
        row = conn.execute(
            'SELECT id FROM reading_threads ORDER BY id LIMIT 1',
        ).fetchone()
    if row:
        return int(row['id'])
    return _create_thread(None, 1, 1, 1)


def _get_thread(thread_id: int):
    with _get_conn() as conn:
        return conn.execute(
            'SELECT id, name, language FROM reading_threads WHERE id = ?',
            (thread_id,),
        ).fetchone()


def _rename_thread(thread_id: int, name: str) -> None:
    clean_name = name.strip()
    if not clean_name:
        return
    with _get_conn() as conn:
        conn.execute(
            'UPDATE reading_threads SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (clean_name, thread_id),
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


def _get_threads():
    with _get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT t.id, t.name, t.language, p.kanda, p.sarga, p.sloka_num, p.updated_at
            FROM reading_threads t
            LEFT JOIN thread_progress p ON p.thread_id = t.id
            ORDER BY p.updated_at DESC, t.id DESC
            '''
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
            'INSERT INTO reading_threads (name, language) VALUES (?, ?)',
            ('Legacy Bookmarks', DEFAULT_LANGUAGE),
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


def _get_thread_bookmarks(thread_id: int | None = None):
    _ensure_legacy_bookmarks()
    params: list[object] = []
    clause = ''
    if thread_id is not None:
        clause = 'WHERE t.id = ?'
        params.append(thread_id)
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


def _resolve_thread_id(thread_id: int | None) -> int:
    if thread_id is not None:
        thread = _get_thread(thread_id)
        if thread:
            return int(thread['id'])
    return _ensure_default_thread()


def _with_thread(url: str, thread_id: int) -> str:
    return f'{url}?thread={thread_id}'


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


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


@rt('/threads/new')
def new_thread(request: Request):
    """Create a new reading thread and redirect to its start."""
    name = request.query_params.get('name', '')
    kanda = _parse_int(request.query_params.get('kanda'), 1)
    sarga = _parse_int(request.query_params.get('sarga'), 1)
    sloka_num = _parse_int(request.query_params.get('sloka'), 1)
    thread_id = _create_thread(name, kanda, sarga, sloka_num)
    return RedirectResponse(
        _with_thread(f'/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}', thread_id)
    )


@rt('/threads/{thread_id}/rename')
async def rename_thread(thread_id: int, request: Request):
    """Rename an existing reading thread."""
    form = await request.form()
    name = str(form.get('name', '')).strip()
    _rename_thread(thread_id, name)
    thread = _get_thread(thread_id)
    if not thread:
        return Response('', status_code=404)
    return _thread_title_fragment(thread_id, thread['name'])


@rt('/threads/{thread_id}/rename-form')
def rename_thread_form(thread_id: int):
    """Return inline rename form."""
    thread = _get_thread(thread_id)
    if not thread:
        return Response('', status_code=404)
    return _thread_rename_form_fragment(thread_id, thread['name'])


@rt('/threads/{thread_id}/title')
def thread_title(thread_id: int):
    """Return thread title fragment."""
    thread = _get_thread(thread_id)
    if not thread:
        return Response('', status_code=404)
    return _thread_title_fragment(thread_id, thread['name'])


@rt('/threads/{thread_id}/delete', methods=['POST'])
def delete_thread(thread_id: int):
    """Delete a reading thread."""
    with _get_conn() as conn:
        conn.execute('DELETE FROM reading_threads WHERE id = ?', (thread_id,))
    return Response('')


@rt('/')
def home():
    """Home page with reading threads."""
    threads = _get_threads()
    thread_cards = []

    for thread in threads:
        thread_cards.append(_thread_card_fragment(thread))

    new_thread_links = [
        A('New Reading Thread', href='/threads/new',
          style='display:block; padding:12px 14px; margin:8px 0; background:#2d2d2d; color:#fbbf24; text-decoration:none; border-radius:8px; font-size:1.05em'),
    ]

    return Html(
        Head(
            Title('Valmiki Ramayana Reader'),
            Meta(name='viewport', content='width=device-width, initial-scale=1, viewport-fit=cover'),
            Meta(name='theme-color', content='#000000'),
            Meta(name='apple-mobile-web-app-capable', content='yes'),
            Meta(name='apple-mobile-web-app-status-bar-style', content='black-translucent'),
            Meta(name='apple-mobile-web-app-title', content='Valmiki'),
            Link(rel='manifest', href='/manifest.webmanifest'),
            Style('* { margin:0; padding:0; box-sizing:border-box; }'),
            Script(src='https://unpkg.com/htmx.org@1.9.12')
        ),
        Body(
            Div(
                H1('వాల్మీకి రామాయణం', style='text-align:center; color:#fbbf24; padding:30px; font-size:2.5em'),
                H2('Valmiki Ramayana Reader', style='text-align:center; color:#888; padding:10px; font-size:1.5em'),
                Div(
                    H3('Your Reading Threads', style='color:#fbbf24; margin:10px 0 20px; text-align:center'),
                    *thread_cards if thread_cards else [P('No threads yet', style='text-align:center; color:#888; padding:20px')],
                    Hr(style='border:none; border-top:1px solid #333; margin:30px 0'),
                    H3('Start a New Thread', style='color:#fbbf24; margin:10px 0 20px; text-align:center'),
                    *new_thread_links,
                    style='max-width:700px; margin:0 auto; padding:20px'
                ),
                style='min-height:100vh; background:black'
            )
        )
    )


@rt('/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}')
async def sloka(kanda: int, sarga: int, sloka_num: int, request: Request):
    """Display a single sloka with navigation."""
    thread_id = _resolve_thread_id(_parse_thread_id(request))
    
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
        # Need to go to previous sarga
        if sarga > 1:
            try:
                prev_sr = _get_sarga_reader(kanda, sarga-1)
            except Exception:
                prev_sr = None
            prev_url = f'/kanda/{kanda}/sarga/{sarga-1}/sloka/{len(prev_sr)}' if prev_sr else '#'
        else:
            prev_url = '#'
    
    # Calculate next URL
    if sloka_num < len(sr):
        next_url = f'/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num+1}'
    else:
        # Go to next sarga (assume it exists)
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
    sloka_content = Div(
        H4(sloka_data['sloka_num'], style='text-align:center; font-size:1.5em; color:#888; margin-bottom:20px'),
        H3(sloka_text, style='text-align:center; white-space:pre-line; font-size:1.8em; line-height:1.8; margin-bottom:40px'),
        H2(bhaavam, style='text-align:center; font-size:1.3em; line-height:1.6; color:#ccc; max-width:700px; margin:0 auto'),
        style='max-width:900px; margin:0 auto'
    )
    
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
        
        # Top-right controls
        Div(
            A(bookmark_icon, href='#', style='text-decoration:none'),
            A('📚', href=_with_thread('/bookmarks', thread_id), 
              style='text-decoration:none; font-size:1.5em; margin-left:15px'),
            A('🧵', href=f'/threads/new?kanda={kanda}&sarga={sarga}&sloka={sloka_num}',
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
                  style='font-size:3em; text-decoration:none; display:flex; align-items:center; justify-content:center; height:100vh; background:#1a1a1a; color:white; width:100%; transition: background 0.2s',
                  onmouseover='this.style.background="#2d2d2d"',
                  onmouseout='this.style.background="#1a1a1a"',
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
        
        # JavaScript for keyboard navigation and bookmark toggle
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
            
            // Bookmark toggle
            document.getElementById('bookmark-btn').parentElement.addEventListener('click', async (e) => {{
                e.preventDefault();
                e.stopPropagation();
                const res = await fetch('/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/bookmark?thread={thread_id}', {{
                    method: 'POST'
                }});
                const data = await res.json();
                const svg = document.getElementById('bookmark-btn');
                svg.style.fill = data.bookmarked ? '#fbbf24' : 'none';
            }});

            // Fullscreen toggle (user gesture required; may not work on all mobile browsers)
            const fsBtn = document.getElementById('fullscreen-btn');
            const fsHint = document.getElementById('fullscreen-hint');
            const requestFs = () => {{
                const el = document.documentElement;
                const req = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
                if (req) req.call(el);
            }};

            fsBtn.addEventListener('click', (e) => {{
                e.preventDefault();
                localStorage.setItem('fsPreferred', '1');
                requestFs();
            }});

            if (localStorage.getItem('fsPreferred') === '1' && !document.fullscreenElement) {{
                fsHint.style.display = 'flex';
            }}

            fsHint.addEventListener('click', () => {{
                requestFs();
            }});
        '''),
        
        id='sloka-view',
        style='background:black'
    )

    if request.headers.get('HX-Request') == 'true':
        return sloka_view

    return Html(
        Head(
            Title(f'Sloka {sloka_data["sloka_num"]} - Valmiki Ramayana'),
            Meta(name='viewport', content='width=device-width, initial-scale=1, viewport-fit=cover'),
            Meta(name='theme-color', content='#000000'),
            Meta(name='apple-mobile-web-app-capable', content='yes'),
            Meta(name='apple-mobile-web-app-status-bar-style', content='black-translucent'),
            Meta(name='apple-mobile-web-app-title', content='Valmiki'),
            Link(rel='manifest', href='/manifest.webmanifest'),
            Script(src='https://unpkg.com/htmx.org@1.9.12'),
            Style('''
                * { margin:0; padding:0; box-sizing:border-box; font-family: system-ui, -apple-system, sans-serif; }
                
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
        Body(sloka_view)
    )


@rt('/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/bookmark', methods=['POST'])
def toggle_bookmark(kanda: int, sarga: int, sloka_num: int, request: Request):
    """Toggle bookmark status for a sloka."""
    thread_id = _resolve_thread_id(_parse_thread_id(request))
    bookmarked = _toggle_bookmark(thread_id, kanda, sarga, sloka_num)
    
    return {'bookmarked': bookmarked}


@rt('/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/mark-read', methods=['POST'])
def mark_read(kanda: int, sarga: int, sloka_num: int, request: Request):
    """Mark a sloka as last read position."""
    thread_id = _resolve_thread_id(_parse_thread_id(request))
    _update_progress(thread_id, kanda, sarga, sloka_num)
    return {'success': True}


@rt('/bookmarks')
def get_bookmarks(request: Request):
    """Display all bookmarked slokas."""
    thread_id = _parse_thread_id(request)
    thread = _get_thread(thread_id) if thread_id is not None else None

    # Create bookmark links
    bookmark_links = []
    rows = _get_thread_bookmarks(thread_id)
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
            Meta(name='viewport', content='width=device-width, initial-scale=1, viewport-fit=cover'),
            Meta(name='theme-color', content='#000000'),
            Meta(name='apple-mobile-web-app-capable', content='yes'),
            Meta(name='apple-mobile-web-app-status-bar-style', content='black-translucent'),
            Meta(name='apple-mobile-web-app-title', content='Valmiki'),
            Link(rel='manifest', href='/manifest.webmanifest'),
            Style('* { margin:0; padding:0; box-sizing:border-box; font-family: system-ui, -apple-system, sans-serif; }')
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
