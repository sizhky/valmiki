# Valmiki Ramayana Reader

A Python library and web application for reading the Valmiki Ramayana with multi-language support, AI-powered translations, and an intuitive web interface.

## Features

- 📖 **Scrape scriptural data** from valmiki.iitk.ac.in
- 🌐 **Multi-language support**: Telugu, Devanagari (Sanskrit), English
- 🤖 **AI translations**: Telugu (formal) and Telangana dialect
- 🔖 **Bookmarks**: Mark important slokas for later reference
- 📍 **Reading progress**: Track your position across sessions
- ⌨️ **Keyboard navigation**: Arrow keys for rapid browsing
- 📱 **Responsive design**: Full-screen reading experience

## Installation

### Basic Installation

```bash
pip install valmiki
```

### With Web Interface

```bash
pip install valmiki[web]
```

### With AI Translation Support

```bash
pip install valmiki[ai]
```

### Full Installation (all features)

```bash
pip install valmiki[all]
```

### Development Installation

```bash
git clone https://github.com/yourusername/valmiki.git
cd valmiki
pip install -e ".[dev]"
```

## Quick Start

### Using the Scraper

```python
from valmiki.scraper import SargaReader

# Fetch slokas from Bala Kanda, Sarga 1 in Telugu
reader = SargaReader(kanda_num=1, sarga_num=1, lang='te')

# Get first sloka
sloka = reader[0]
print(f"Sloka {sloka['sloka_num']}")
print(sloka['sloka_text'])
print(sloka['bhaavam_en'])

# Iterate through all slokas
for sloka in reader.get_all_slokas():
    print(f"{sloka['sloka_num']}: {sloka['sloka_text']}")
```

### Running the Web Application

After installing with web support:

```bash
# Simple - just run valmiki
valmiki

# Or explicitly
valmiki serve
```

Then visit: `http://localhost:8000`

Or programmatically:

```python
from valmiki.app import app
import uvicorn
uvicorn.run(app, host="0.0.0.0", port=8000)
```

## API Reference

### SargaReader

Main class for scraping and parsing Ramayana content.

```python
SargaReader(kanda_num: int, sarga_num: int, lang: str = 'te')
```

**Parameters:**
- `kanda_num` (1-6): Kanda number
- `sarga_num`: Sarga number within the kanda
- `lang`: Language code ('te' for Telugu, 'dv' for Devanagari)

**Methods:**
- `get_sloka(index)`: Get sloka by index (0-based)
- `get_all_slokas()`: Get all slokas in the sarga
- `__len__()`: Number of slokas
- `__getitem__(index)`: Access slokas via indexing

**Sloka Structure:**
```python
{
    'sloka_num': str,           # e.g., "1.1.1"
    'sloka_text': str,          # Verse in native script
    'pratipadaartham': dict,    # Word-by-word meanings
    'bhaavam_en': str           # Overall meaning in English
}
```

## Data Structure

- **6 Kandas** (books)
- Each Kanda contains multiple **Sargas** (chapters)
- Each Sarga contains multiple **Slokas** (verses)

## Languages Supported

- `te` - Telugu
- `dv` - Devanagari (Sanskrit)
- `en` - English (for meanings)

## Architecture

See [docs/architecture.md](docs/architecture.md) for detailed system architecture.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Data source: [IIT Kanpur Valmiki Ramayana](https://valmiki.iitk.ac.in)
- AI translations powered by Google Gemini

## Disclaimer

This project is for educational and research purposes. All content belongs to the original sources.
