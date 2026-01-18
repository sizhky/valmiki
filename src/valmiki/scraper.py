"""Scraper module for fetching and parsing Valmiki Ramayana slokas from valmiki.iitk.ac.in."""

import re
from functools import lru_cache

import httpx
from bs4 import BeautifulSoup

sarga_cache = {}

class SargaReader:
    """
    Fetches and parses slokas from a specific Sarga (chapter) of the Valmiki Ramayana.
    
    The reader fetches HTML from valmiki.iitk.ac.in and extracts structured sloka data
    including the verse text, word-by-word meanings (pratipadaartham), and overall
    meaning (bhaavam).
    
    Attributes:
        kanda_num (int): Kanda number (1-6)
        sarga_num (int): Sarga number within the kanda
        lang (str): Language code ('te' for Telugu, 'dv' for Devanagari)
        url (str): Constructed URL for fetching data
        soup (BeautifulSoup): Parsed HTML content
        rows (list): List of parsed sloka rows
    """
    
    BASE_URL = "https://www.valmiki.iitk.ac.in/sloka"
    
    def __init__(self, kanda_num: int, sarga_num: int, lang: str = 'te'):
        """
        Initialize the SargaReader and fetch data from the website.
        
        Args:
            kanda_num: Kanda number (1-6)
            sarga_num: Sarga number within the kanda
            lang: Language code ('te' for Telugu, 'dv' for Devanagari)
        
        Raises:
            httpx.HTTPError: If the request fails
        """
        self.kanda_num = kanda_num
        self.sarga_num = sarga_num
        self.lang = lang
        self.url = f"{self.BASE_URL}?field_kanda_tid={kanda_num}&language={lang}&field_sarga_value={sarga_num}"
        
        # Fetch and parse the HTML
        response = httpx.get(self.url)
        response.raise_for_status()
        self.soup = BeautifulSoup(response.text, 'lxml')
        self.rows = self.soup.select('.views-row')
        
        # Cache for parsed slokas to avoid re-parsing
        self._sloka_cache = {}
        self._slokas = None
    
    def extract_sloka(self, row) -> dict:
        """
        Extract structured sloka data from an HTML row element.
        
        Args:
            row: BeautifulSoup element containing a single sloka
        
        Returns:
            Dictionary containing:
                - sloka_num (str): Sloka number in format "kanda.sarga.sloka"
                - sloka_text (str): The verse text in native script
                - pratipadaartham (dict): Word-by-word meanings
                - bhaavam_en (str): Overall meaning in English
        """
        # Extract the three main content sections
        body = row.select_one('.views-field-body .field-content')
        htetrans = row.select_one('.views-field-field-htetrans .field-content')
        explanation = row.select_one('.views-field-field-explanation .field-content')
        
        # Parse body text to extract sloka number and verse text
        body_text = body.get_text('\n', strip=True)
        lines = [l.strip() for l in body_text.split('\n') if l.strip()]
        
        # Extract sloka number (format: ৷৷1.1.1৷৷)
        sloka_num = next(
            (re.search(r'৷৷([\d.]+)৷৷', l).group(1) for l in lines if re.search(r'৷৷[\d.]+৷৷', l)),
            None
        )
        
        # Extract sloka text (excluding metadata and sloka number)
        sloka_lines = [
            re.sub(r'\s*৷৷[\d.]+৷৷\s*', '', l)  # Remove sloka number markers
            for l in lines
            if not l.startswith('[')  # Exclude metadata in square brackets
            and any(c not in ' .,।৷' for c in re.sub(r'৷৷[\d.]+৷৷', '', l))  # Has actual content
        ]
        sloka_text = '\n'.join(sloka_lines).strip()
        
        # Parse pratipadaartham (word-by-word meanings)
        hte_text = htetrans.get_text(' ', strip=True) if htetrans else ''
        # Extract word-meaning pairs (non-English word followed by English meaning)
        pairs = re.findall(r'(\S+)\s+([^,]+?)(?=\s+\S+\s+[^,]+|$)', hte_text)
        pratipadaartham = {
            k.strip(): v.strip().rstrip(',')
            for k, v in pairs
            if not re.match(r'^[a-zA-Z]+$', k)  # Filter out standalone English words
        }
        
        # Extract bhaavam (overall meaning)
        bhaavam_en = explanation.get_text(' ', strip=True) if explanation else ''
        
        return {
            'sloka_num': sloka_num,
            'sloka_text': sloka_text,
            'pratipadaartham': pratipadaartham,
            'bhaavam_en': bhaavam_en,
        }
    
    def get_sloka(self, index: int) -> dict:
        """
        Get a specific sloka by index (0-based).
        
        Args:
            index: Zero-based index of the sloka
        
        Returns:
            Dictionary containing structured sloka data
        
        Raises:
            IndexError: If index is out of range
        """
        total = len(self)
        if index < 0 or index >= total:
            raise IndexError(f"Sloka index {index} out of range (0-{total-1})")

        if self._slokas is not None:
            return self._slokas[index]
        
        # Check cache first to avoid re-parsing
        if index not in self._sloka_cache:
            self._sloka_cache[index] = self.extract_sloka(self.rows[index])
        
        return self._sloka_cache[index]
    
    def get_all_slokas(self) -> list[dict]:
        """
        Get all slokas in the sarga.
        
        Returns:
            List of dictionaries containing structured sloka data
        """
        if self._slokas is None:
            self._slokas = [self.extract_sloka(row) for row in self.rows]
        return self._slokas
    
    def __len__(self) -> int:
        """Return the number of slokas in this sarga."""
        if self._slokas is not None:
            return len(self._slokas)
        return len(self.rows)
    
    def __getitem__(self, index: int) -> dict:
        """Allow indexing to get slokas (e.g., reader[0])."""
        return self.get_sloka(index)
    
    def __repr__(self) -> str:
        """String representation of the reader."""
        return f"SargaReader(kanda={self.kanda_num}, sarga={self.sarga_num}, lang='{self.lang}', slokas={len(self)})"

    def __getstate__(self) -> dict:
        slokas = self.get_all_slokas()
        return {
            'kanda_num': self.kanda_num,
            'sarga_num': self.sarga_num,
            'lang': self.lang,
            'slokas': slokas,
        }

    def __setstate__(self, state: dict) -> None:
        self.kanda_num = state['kanda_num']
        self.sarga_num = state['sarga_num']
        self.lang = state.get('lang', 'te')
        self.url = f"{self.BASE_URL}?field_kanda_tid={self.kanda_num}&language={self.lang}&field_sarga_value={self.sarga_num}"
        self.soup = None
        self.rows = []
        self._sloka_cache = {}
        self._slokas = state.get('slokas', [])


@lru_cache(maxsize=128)
def get_sarga_metadata(kanda_num: int, sarga_num: int, lang: str = 'te') -> dict:
    """
    Get metadata about a sarga without parsing all slokas.
    
    Args:
        kanda_num: Kanda number (1-6)
        sarga_num: Sarga number within the kanda
        lang: Language code
    
    Returns:
        Dictionary containing sarga metadata (kanda, sarga, sloka_count)
    
    Note:
        This function is cached using LRU cache to avoid repeated HTTP requests
        for the same sarga metadata.
    """
    if (kanda_num, sarga_num, lang) in sarga_cache:
        reader = sarga_cache[(kanda_num, sarga_num, lang)]
    else:
        reader = SargaReader(kanda_num, sarga_num, lang)
        sarga_cache[(kanda_num, sarga_num, lang)] = reader
    return {
        'kanda': kanda_num,
        'sarga': sarga_num,
        'language': lang,
        'sloka_count': len(reader),
    }
