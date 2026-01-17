"""FastHTML web application for Valmiki Ramayana Reader."""

from fasthtml.common import *

from .scraper import SargaReader

# Initialize FastHTML app
app = FastHTML()
rt = app.route

# In-memory storage
sarga_readers = {}  # Cache for SargaReader instances: {(kanda, sarga): SargaReader}
bookmarks = set()  # Set of bookmarked slokas: {(kanda, sarga, sloka_num)}
last_read = {}  # Last read position per language: {language: (kanda, sarga, sloka_num)}

# Translation caches (for future translator integration)
translation_cache = {
    'te': {},  # Telugu translations: {english_text: telugu_text}
    'tg': {},  # Telangana translations: {english_text: telangana_text}
}


@rt('/')
def home():
    """Home page with continue reading links."""
    links = []
    
    # Add continue reading links for each language with history
    for lang, (k, s, sl) in last_read.items():
        if lang == 'te':
            link_text = f'చదవడం కొనసాగించండి - కాండ {k} సర్గ {s} శ్లోక {sl}'
        elif lang == 'tg':
            link_text = f'చదువుకోడం కొనసాగించు - కాండ {k} సర్గ {s} శ్లోక {sl}'
        else:  # en
            link_text = f'Continue Reading - Kanda {k} Sarga {s} Sloka {sl}'
        
        links.append(
            A(link_text, 
              href=f'/{lang}/kanda/{k}/sarga/{s}/sloka/{sl}',
              style='display:block; padding:15px; margin:10px 0; background:#1a1a1a; color:white; text-decoration:none; border-radius:8px; font-size:1.2em')
        )
    
    # Start reading links
    start_links = [
        A('Start Reading (Telugu)', href='/te/kanda/1/sarga/1/sloka/1', 
          style='display:block; padding:15px; margin:10px 0; background:#2d2d2d; color:#fbbf24; text-decoration:none; border-radius:8px; font-size:1.1em'),
        A('Start Reading (English)', href='/en/kanda/1/sarga/1/sloka/1',
          style='display:block; padding:15px; margin:10px 0; background:#2d2d2d; color:#fbbf24; text-decoration:none; border-radius:8px; font-size:1.1em'),
    ]
    
    return Html(
        Head(
            Title('Valmiki Ramayana Reader'),
            Style('* { margin:0; padding:0; box-sizing:border-box; }')
        ),
        Body(
            Div(
                H1('వాల్మీకి రామాయణం', style='text-align:center; color:#fbbf24; padding:30px; font-size:2.5em'),
                H2('Valmiki Ramayana Reader', style='text-align:center; color:#888; padding:10px; font-size:1.5em'),
                Div(
                    *links if links else [P('No reading history yet', style='text-align:center; color:#888; padding:20px')],
                    Hr(style='border:none; border-top:1px solid #333; margin:30px 0'),
                    *start_links,
                    style='max-width:600px; margin:0 auto; padding:20px'
                ),
                style='min-height:100vh; background:black'
            )
        )
    )


@rt('/{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}')
async def sloka(kanda: int, sarga: int, sloka_num: int, language: str):
    """Display a single sloka with navigation."""
    # Validate language
    if language not in ['en', 'te', 'tg']:
        return Response('Invalid language', status_code=404)
    
    # Get or create SargaReader for this sarga
    sr = sarga_readers.get((kanda, sarga))
    if sr is None:
        try:
            sr = SargaReader(kanda, sarga, lang='te')  # Always fetch Telugu script
            sarga_readers[(kanda, sarga)] = sr
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
        prev_url = f'/{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num-1}'
    else:
        # Need to go to previous sarga
        if sarga > 1:
            prev_sr = sarga_readers.get((kanda, sarga-1))
            if prev_sr is None:
                try:
                    prev_sr = SargaReader(kanda, sarga-1, lang='te')
                    sarga_readers[(kanda, sarga-1)] = prev_sr
                except:
                    prev_sr = None
            prev_url = f'/{language}/kanda/{kanda}/sarga/{sarga-1}/sloka/{len(prev_sr)}' if prev_sr else '#'
        else:
            prev_url = '#'
    
    # Calculate next URL
    if sloka_num < len(sr):
        next_url = f'/{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num+1}'
    else:
        # Go to next sarga (assume it exists)
        next_url = f'/{language}/kanda/{kanda}/sarga/{sarga+1}/sloka/1'
    
    # Render sloka content
    sloka_text = sloka_data['sloka_text']
    bhaavam_en = sloka_data['bhaavam_en']
    
    # Get translated bhaavam if not English
    if language == 'te':
        bhaavam = translation_cache['te'].get(bhaavam_en, bhaavam_en)
    elif language == 'tg':
        bhaavam = translation_cache['tg'].get(bhaavam_en, bhaavam_en)
    else:
        bhaavam = bhaavam_en
    
    # Check if bookmarked
    is_bookmarked = (kanda, sarga, sloka_num) in bookmarks
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
    
    warning_font_size = '3.5rem'
    return Html(
        Head(
            Title(f'Sloka {sloka_data["sloka_num"]} - Valmiki Ramayana'),
            Style('''
                * { margin:0; padding:0; box-sizing:border-box; font-family: system-ui, -apple-system, sans-serif; }
                
                /* Rotation hint - hidden by default */
                .rotation-hint {
                    display: none;
                }
                
                /* Show rotation hint on mobile/tablet portrait mode */
                @media screen and (max-width: 1024px) and (orientation: portrait) {
                    .rotation-hint {
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        gap: 10px;
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
                    
                    /* Adjust content padding to account for hint */
                    body > div:last-child {
                        padding-top: 50px;
                    }
                }
            ''')
        ),
        Body(
            # Rotation hint overlay
            Div(
                Span('🔄', style=f'font-size:{warning_font_size}'),
                Div(
                    Div('Rotate your device for better reading experience', style=f'font-weight:600; font-size:{warning_font_size}'),
                    Div('మెరుగైన పఠన అనుభవం కోసం మీ పరికరాన్ని అడ్డంగా తిప్పండి.', style=f'font-size:{warning_font_size}; margin-top:4px; opacity:0.95') if language in ['te', 'tg'] else None,
                    style='line-height:1.3'
                ),
                cls='rotation-hint',
                style='display:flex; align-items:center; gap:12px'
            ),
            
            # Top-right controls
            Div(
                A(bookmark_icon, href='#', style='text-decoration:none'),
                A('📚', href=f'/{language}/bookmarks', 
                  style='text-decoration:none; font-size:1.5em; margin-left:15px'),
                A('🏠', href='/', 
                  style='text-decoration:none; font-size:1.5em; margin-left:15px'),
                style='position:fixed; top:20px; right:20px; z-index:1000; display:flex; gap:10px; align-items:center'
            ),
            
            # Main content with navigation
            Div(
                # Left arrow
                Div(
                    A('←', href=prev_url, id='prev',
                      style='font-size:3em; text-decoration:none; display:flex; align-items:center; justify-content:center; height:100vh; background:#1a1a1a; color:white; width:100%; transition: background 0.2s',
                      onmouseover='this.style.background="#2d2d2d"',
                      onmouseout='this.style.background="#1a1a1a"'),
                    style='flex:0 0 8%'
                ),
                
                # Center content (clickable to go next)
                Div(
                    sloka_content,
                    style='flex:1; padding:40px 20px; display:flex; align-items:center; justify-content:center; color:white; cursor:pointer',
                    onclick=f"window.location.href='{next_url}'"
                ),
                
                # Right arrow
                Div(
                    A('→', href=next_url, id='next',
                      style='font-size:3em; text-decoration:none; display:flex; align-items:center; justify-content:center; height:100vh; background:#1a1a1a; color:white; width:100%; transition: background 0.2s',
                      onmouseover='this.style.background="#2d2d2d"',
                      onmouseout='this.style.background="#1a1a1a"'),
                    style='flex:0 0 8%'
                ),
                
                style='display:flex; min-height:100vh; background:black'
            ),
            
            # JavaScript for keyboard navigation and bookmark toggle
            Script(f'''
                // Mark as read
                fetch('/{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/mark-read', {{method: 'POST'}});
                
                // Keyboard navigation
                document.addEventListener('keydown', (e) => {{
                    if (e.key === 'ArrowLeft') {{
                        e.preventDefault();
                        document.getElementById('prev').click();
                    }}
                    if (e.key === 'ArrowRight') {{
                        e.preventDefault();
                        document.getElementById('next').click();
                    }}
                }});
                
                // Bookmark toggle
                document.getElementById('bookmark-btn').parentElement.addEventListener('click', async (e) => {{
                    e.preventDefault();
                    e.stopPropagation();
                    const res = await fetch('/{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/bookmark', {{
                        method: 'POST'
                    }});
                    const data = await res.json();
                    const svg = document.getElementById('bookmark-btn');
                    svg.style.fill = data.bookmarked ? '#fbbf24' : 'none';
                }});
            '''),
            
            style='background:black'
        )
    )


@rt('/{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/bookmark', methods=['POST'])
def toggle_bookmark(kanda: int, sarga: int, sloka_num: int, language: str):
    """Toggle bookmark status for a sloka."""
    bookmark_id = (kanda, sarga, sloka_num)
    
    if bookmark_id in bookmarks:
        bookmarks.remove(bookmark_id)
        bookmarked = False
    else:
        bookmarks.add(bookmark_id)
        bookmarked = True
    
    return {'bookmarked': bookmarked}


@rt('/{language}/kanda/{kanda}/sarga/{sarga}/sloka/{sloka_num}/mark-read', methods=['POST'])
def mark_read(kanda: int, sarga: int, sloka_num: int, language: str):
    """Mark a sloka as last read position."""
    last_read[language] = (kanda, sarga, sloka_num)
    return {'success': True}


@rt('/{language}/bookmarks')
def get_bookmarks(language: str):
    """Display all bookmarked slokas."""
    if language not in ['en', 'te', 'tg']:
        return Response('Invalid language', status_code=404)
    
    # Create bookmark links
    bookmark_links = []
    for k, s, sl in sorted(bookmarks):
        if language == 'te':
            link_text = f'కాండ {k} సర్గ {s} శ్లోక {sl}'
        elif language == 'tg':
            link_text = f'కాండ {k} సర్గ {s} శ్లోక {sl}'
        else:  # en
            link_text = f'Kanda {k} Sarga {s} Sloka {sl}'
        
        bookmark_links.append(
            Div(
                A(link_text,
                  href=f'/{language}/kanda/{k}/sarga/{s}/sloka/{sl}',
                  style='color:white; text-decoration:none; font-size:1.3em; padding:15px; display:block; border-bottom:1px solid #333; transition: background 0.2s',
                  onmouseover='this.style.background="#1a1a1a"',
                  onmouseout='this.style.background="transparent"')
            )
        )
    
    # No bookmarks message
    if not bookmark_links:
        if language == 'te':
            no_bookmarks = P('పేజీలు ఏవీ గుర్తించబడలేదు', style='text-align:center; color:#888; padding:40px; font-size:1.2em')
        elif language == 'tg':
            no_bookmarks = P('ఏ పేజీలూ గుర్తు పెట్టలేదు', style='text-align:center; color:#888; padding:40px; font-size:1.2em')
        else:
            no_bookmarks = P('No bookmarks yet', style='text-align:center; color:#888; padding:40px; font-size:1.2em')
        bookmark_links = [no_bookmarks]
    
    # Title text
    if language == 'te':
        title = 'పేజీలు గుర్తించబడ్డాయి'
    elif language == 'tg':
        title = 'గుర్తు పెట్టిన పేజీలు'
    else:
        title = 'Bookmarks'
    
    return Html(
        Head(
            Title(f'{title} - Valmiki Ramayana'),
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
