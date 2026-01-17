# Valmiki Ramayana Reader - Architecture

## Overview
A web-based reader for the Valmiki Ramayana that scrapes scriptural data from valmiki.iitk.ac.in, supports multiple languages, and provides an intuitive reading experience with bookmarks and progress tracking.

## System Architecture

### 1. Data Layer

#### 1.1 Data Source
- **Source URL Pattern**: `https://www.valmiki.iitk.ac.in/sloka?field_kanda_tid={kanda}&language={lang}&field_sarga_value={sarga}`
- **Structure**: 6 Kandas, each containing variable number of Sargas
- **Languages Supported**: 
  - `dv` - Devanagari (Sanskrit)
  - `te` - Telugu

#### 1.2 Data Models

**Sloka Structure**:
```python
{
    "sloka_num": str,           # e.g., "1.1.1" (kanda.sarga.sloka)
    "sloka_text": str,          # The actual sloka in native script
    "pratipadaartham": dict,    # Word-by-word meanings {word: meaning}
    "bhaavam_en": str,          # Overall meaning in English
    "bhaavam": str              # Translated meaning (te/tg based on render_language)
}
```

#### 1.3 Scraper Module (`SargaReader`)

**Responsibilities**:
- Fetch HTML from valmiki.iitk.ac.in
- Parse sloka data from `.views-row` divs
- Extract sloka number using regex pattern `৷৷[\d.]+৷৷`
- Parse three content fields:
  - `.views-field-body` → sloka text
  - `.views-field-field-htetrans` → pratipadaartham
  - `.views-field-field-explanation` → bhaavam
- Cache fetched sargas in memory

**Key Methods**:
- `__init__(kanda_num, sarga_num, lang)` - Initialize and fetch
- `extract_sloka(row)` - Extract structured data from HTML row
- `__getitem__(ix)` - Get rendered sloka by index
- `__len__()` - Number of slokas in sarga

### 2. Translation Layer

#### 2.1 AI Translation Service

**Agents**:
- `te_agent` - English → Telugu (formal literary Telugu)
- `tg_agent` - English → Telangana dialect (tribal native style)

**Model**: Google Gemini 2.5 Flash Lite via `pydantic_ai`

**Translation Cache**:
```python
all_te_translations = {}  # {english_text: telugu_translation}
all_tg_translations = {}  # {english_text: telangana_translation}
```

**Strategy**:
- Check cache first before calling AI
- Translate `bhaavam_en` on-demand based on `render_language`
- Store translations for reuse across sessions

### 3. Storage Layer

#### 3.1 In-Memory Storage

**Sarga Cache**:
```python
sarga_readers = {}  # {(kanda, sarga): SargaReader}
```
- Avoids re-fetching same sarga data
- Persists for application lifetime

**Bookmarks**:
```python
bookmarks = set()  # {(kanda, sarga, sloka_num)}
```
- User-marked slokas for later reference
- Manual control (not auto-bookmarked)

**Last Read Position**:
```python
last_read = {}  # {language: (kanda, sarga, sloka_num)}
```
- Tracks reading progress per language
- Auto-updated on page view

#### 3.2 Future: Persistent Storage
- SQLite database for bookmarks & reading history
- JSON files for translation cache
- Optional user accounts for cloud sync

### 4. Web Application Layer

#### 4.1 Framework
- **FastHTML** - Lightweight Python web framework
- **JupyUvi** - Jupyter integration for development

#### 4.2 Routes

**Reader Routes**:
- `GET /{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}`
  - Display single sloka
  - Auto-fetch surrounding sargas for navigation
  - Track last read position

**Bookmark Routes**:
- `POST /{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/bookmark`
  - Toggle bookmark status
  - Returns `{bookmarked: bool}`
- `GET /{language}/bookmarks`
  - List all bookmarked slokas

**Progress Tracking**:
- `POST /{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/mark-read`
  - Update last read position

**Home Route**:
- `GET /`
  - Display continue reading links
  - Show reading history

### 5. Presentation Layer

#### 5.1 UI Components

**Sloka Viewer**:
- Centered layout (max-width: 800px)
- Black background with white text
- Components:
  - Sloka number (H4)
  - Sloka text in native script (H4, pre-line whitespace)
  - Bhaavam/meaning (H3)
  - Optional: Pratipadaartham table

**Navigation**:
- Left arrow (5% width) - Previous sloka
- Center content (90% width) - Current sloka
- Right arrow (5% width) - Next sloka
- Keyboard support: Arrow keys
- Click anywhere on content → Next sloka

**Bookmarks**:
- Fixed bookmark icon (top-right)
- Star icon: filled (#fbbf24) when bookmarked, outlined when not
- Toggle via click (AJAX request)
- Separate bookmark list page

#### 5.2 Language Rendering

**Supported Languages**:
- `en` - English (original bhaavam_en)
- `te` - Telugu (AI-translated)
- `tg` - Telangana dialect (AI-translated)

**Rendering Logic**:
- `SargaReader.render_language` controls display language
- Sloka text always in source script (Devanagari/Telugu)
- Only bhaavam gets translated

### 6. Module Organization

```
valmiki/
├── data/                   # Scraped data (future)
├── docs/
│   └── architecture.md     # This file
├── src/valmiki/
│   ├── __init__.py
│   ├── scraper.py          # SargaReader class
│   ├── models.py           # Sloka data models
│   ├── translator.py       # AI translation agents & cache
│   ├── storage.py          # Bookmark & reading progress
│   ├── renderer.py         # HTML rendering helpers
│   └── app.py              # FastHTML routes & server
├── notebooks/
│   └── exploration.ipynb   # Development notebook
├── requirements.txt
└── README.md
```

### 7. Key Design Decisions

#### 7.1 Lazy Loading
- Fetch sargas only when needed
- Cache in memory to avoid redundant requests
- Pre-fetch adjacent sargas for smooth navigation

#### 7.2 Translation Strategy
- On-demand translation (not pre-translated)
- Cache translations to reduce API calls
- Separate agents for different Telugu styles

#### 7.3 Bookmark vs Last Read
- **Bookmarks**: Manual, for important slokas
- **Last Read**: Automatic, tracks progress
- Kept separate to avoid confusion

#### 7.4 Navigation UX
- Arrow navigation for rapid browsing
- Full-screen focused reading
- Minimal UI distractions

### 8. Performance Considerations

**Optimizations**:
- In-memory sarga cache reduces HTTP requests
- Translation cache minimizes AI API calls
- Async sloka extraction for faster rendering
- Preemptive fetching of adjacent sargas

**Limitations**:
- Memory usage grows with exploration
- No persistent cache (lost on restart)
- Single-user session model

### 9. Future Enhancements

**Phase 1** (Current):
- ✅ Basic scraping & parsing
- ✅ Multi-language support
- ✅ Navigation & bookmarks
- ✅ AI translation

**Phase 2**:
- [ ] Persistent storage (SQLite)
- [ ] Search functionality
- [ ] Chapter/Kanda overview pages
- [ ] Commentary layer (traditional interpretations)

**Phase 3**:
- [ ] User accounts & cloud sync
- [ ] Audio narration
- [ ] Mobile app (PWA)
- [ ] Offline mode
- [ ] Export to PDF/EPUB

**Phase 4**:
- [ ] Community annotations
- [ ] Multiple translation sources
- [ ] Sanskrit word analysis
- [ ] Cross-references to other texts

### 10. Technical Dependencies

**Core**:
- `httpx` - HTTP client for scraping
- `beautifulsoup4` + `lxml` - HTML parsing
- `fasthtml` - Web framework
- `pydantic_ai` - AI agent framework
- `google-generative-ai` - Gemini models

**Development**:
- `jupyter` - Interactive development
- `fasthtml.jupyter` - Jupyter integration

## Deployment Strategy

**Development**:
- Run via JupyUvi in Jupyter notebook
- Hot reload for rapid iteration

**Production** (Future):
- Deploy as standalone FastHTML app
- Use uvicorn/gunicorn ASGI server
- Add Redis for distributed caching
- PostgreSQL for user data
- Docker containerization
