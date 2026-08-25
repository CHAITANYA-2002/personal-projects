"""
F1 Analytics — Streamlit dashboard over the f1_analytics MySQL database.

Run from the sql/ directory:
    pip install -r requirements.txt
    python -m streamlit run app.py

Connection reads F1_DB_* environment variables, falling back to root/root/localhost
for a local demo. No password is committed; change the fallback or set env vars for
any non-local use.
"""
import os
import math
import base64
import html
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import create_engine, event, text

HERE = Path(__file__).parent
RED = "#e10600"
INK = "#0e0e12"
CARD = "#16161d"
LINE = "#2a2a36"
PAPER = "#e9e7e2"

st.set_page_config(page_title="F1 Analytics — What Makes a Champion?",
                   page_icon="🏎️", layout="wide", initial_sidebar_state="expanded")

# team colours (constructor name -> hex)
TEAM = {
    "Mercedes": "#00D2BE", "Ferrari": "#DC0000", "Red Bull": "#0600EF",
    "McLaren": "#FF8700", "Alpine F1 Team": "#0090FF", "Aston Martin": "#006F62",
    "Williams": "#005AFF", "AlphaTauri": "#2B4562", "RB F1 Team": "#6692FF",
    "Alfa Romeo": "#900000", "Haas F1 Team": "#B6BABD", "Renault": "#FFF500",
    "Racing Point": "#F596C8", "Sauber": "#52E252", "Toro Rosso": "#469BFF",
    "Force India": "#F596C8", "Jordan": "#FFA100", "Benetton": "#00A650",
    "Brawn": "#B8FD6E", "Tyrrell": "#1E5BA8", "Lotus F1": "#FFB800",
    "Team Lotus": "#FFB800", "Brabham": "#1E5B3E", "Jaguar": "#0A5C36",
    "BMW Sauber": "#005AFF", "Honda": "#FFFFFF", "Toyota": "#CC0000",
    # historic entrants — classic livery / national racing colours
    "Lotus-Climax": "#0E7C3A", "Lotus-Ford": "#C9A227", "Cooper-Climax": "#1B7F4C",
    "Cooper-Maserati": "#2E8B57", "BRM": "#1F7A3D", "Vanwall": "#0B6E4F",
    "Maserati": "#C8102E", "Matra-Ford": "#2E6FD9", "Brabham-Repco": "#2C7A55",
    "McLaren-Ford": "#FF8700", "March": "#E03C31", "Ligier": "#1E63C8",
    "Wolf": "#1B2A4A", "Kurtis Kraft": "#B6BABD", "Watson": "#B07A3C",
    "Eagle-Weslake": "#1B5FA8", "Shadow": "#5A5A62", "Hesketh": "#D63C63",
    "Penske": "#D8B12A", "Surtees": "#C81E2B", "Porsche": "#B7B7BD",
    "Cooper": "#1B7F4C", "Epperly": "#C7902F", "Kuzma": "#8C6239", "Lesovsky": "#8C6239",
}
FLAG = {
    "British": "🇬🇧", "English": "🇬🇧", "German": "🇩🇪", "Dutch": "🇳🇱",
    "Spanish": "🇪🇸", "French": "🇫🇷", "Italian": "🇮🇹", "Brazilian": "🇧🇷",
    "Finnish": "🇫🇮", "Austrian": "🇦🇹", "Australian": "🇦🇺", "Mexican": "🇲🇽",
    "Canadian": "🇨🇦", "American": "🇺🇸", "American-Italian": "🇺🇸",
    "Monegasque": "🇲🇨", "Japanese": "🇯🇵", "Belgian": "🇧🇪", "Swiss": "🇨🇭",
    "Argentine": "🇦🇷", "Argentinian": "🇦🇷", "Danish": "🇩🇰", "Swedish": "🇸🇪",
    "Russian": "🇷🇺", "Thai": "🇹🇭", "Chinese": "🇨🇳", "New Zealander": "🇳🇿",
    "Polish": "🇵🇱", "Portuguese": "🇵🇹", "Indian": "🇮🇳", "Colombian": "🇨🇴",
    "Venezuelan": "🇻🇪", "Indonesian": "🇮🇩", "South African": "🇿🇦",
    "Irish": "🇮🇪", "Hungarian": "🇭🇺", "Czech": "🇨🇿", "Malaysian": "🇲🇾",
    "Liechtensteiner": "🇱🇮", "Chilean": "🇨🇱", "Uruguayan": "🇺🇾",
    "East German": "🇩🇪", "Rhodesian": "🇿🇼",
}

FLAG_CODE = {
    "British": "gb", "English": "gb", "German": "de", "Dutch": "nl", "Spanish": "es",
    "French": "fr", "Italian": "it", "Brazilian": "br", "Finnish": "fi", "Austrian": "at",
    "Australian": "au", "Mexican": "mx", "Canadian": "ca", "American": "us",
    "American-Italian": "us", "Monegasque": "mc", "Japanese": "jp", "Belgian": "be",
    "Swiss": "ch", "Argentine": "ar", "Argentinian": "ar", "Danish": "dk", "Swedish": "se",
    "Russian": "ru", "Thai": "th", "Chinese": "cn", "New Zealander": "nz", "Polish": "pl",
    "Portuguese": "pt", "Indian": "in", "Colombian": "co", "Venezuelan": "ve", "Indonesian": "id",
    "South African": "za", "Irish": "ie", "Hungarian": "hu", "Czech": "cz", "Malaysian": "my",
    "Liechtensteiner": "li", "Chilean": "cl", "Uruguayan": "uy", "East German": "de", "Rhodesian": "zw",
    "Argentine-Italian": "ar", "Argentinian ": "ar", "Argentine ": "ar", "New Zealand": "nz",
}


TEAM_ABBR = {
    "Ferrari": "SF", "McLaren": "MCL", "Mercedes": "MER", "Red Bull": "RBR",
    "Williams": "WIL", "Team Lotus": "LOT", "Lotus F1": "LOT", "Lotus-Climax": "LOT",
    "Renault": "REN", "Benetton": "BEN", "Tyrrell": "TYR", "Brabham": "BRM",
    "BRM": "BRM", "Alpine F1 Team": "ALP", "Aston Martin": "AMR", "AlphaTauri": "AT",
    "RB F1 Team": "RB", "Alfa Romeo": "ALF", "Haas F1 Team": "HAA", "Sauber": "SAU",
    "Toro Rosso": "STR", "Racing Point": "RP", "Force India": "FI", "Jordan": "JOR",
    "Brawn": "BGP", "Jaguar": "JAG", "BMW Sauber": "BMW", "Honda": "HON",
    "Toyota": "TOY", "Cooper-Climax": "COO", "Brabham-Repco": "BRA", "Matra": "MAT",
    "Vanwall": "VAN", "Maserati": "MAS", "Alfa Romeo Racing": "ALF",
    "Lotus-Climax": "LOT", "Lotus-Ford": "LOT", "Cooper-Climax": "COO",
    "Cooper-Maserati": "COO", "McLaren-Ford": "MCL", "Matra-Ford": "MAT",
    "Kurtis Kraft": "KK", "March": "MAR", "Ligier": "LIG", "Wolf": "WOL",
    "Watson": "WAT", "Eagle-Weslake": "EAG", "Shadow": "SHA", "Hesketh": "HES",
    "Penske": "PEN", "Surtees": "SUR", "Porsche": "POR",
    "Cooper": "COO", "Epperly": "EPP", "Kuzma": "KUZ", "Lesovsky": "LES",
}


def team_color(name: str) -> str:
    return TEAM.get(name, "#9aa0aa")


def team_text_color(name: str) -> str:
    """Lift very dark team colours so their text stays readable on the dark UI."""
    hexcol = team_color(name)
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    if lum >= 0.34:
        return hexcol
    boost = (0.34 - lum) + 0.30
    r, g, b = (min(255, int(c + (255 - c) * boost)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def on_team_text(name: str) -> str:
    """Ink colour that stays legible on top of a solid team-colour chip."""
    hexcol = team_color(name)
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    return "#0d0d10" if (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 >= 0.45 else "#ffffff"


def team_abbr(name: str) -> str:
    n = str(name).strip()
    if n in TEAM_ABBR:
        return TEAM_ABBR[n]
    words = [w for w in n.replace("-", " ").split() if w]
    if len(words) >= 2:
        return (words[0][:1] + words[1][:1] + (words[1][1:2] or "")).upper()
    return n[:3].upper()


@st.cache_data(show_spinner=False)
def _team_logos():
    """name -> (data-uri, needs_light_chip) for every constructor logo we hold."""
    out = {}
    try:
        cons = q("SELECT constructorId, name FROM constructors")
    except Exception:
        return out
    for r in cons.itertuples():
        p = HERE / "assets" / "teams" / f"{int(r.constructorId)}.png"
        if not p.exists():
            continue
        light = (HERE / "assets" / "teams" / f"{int(r.constructorId)}.dark").exists()
        out[r.name] = (f"data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}", light)
    return out


def team_badge(name: str, size: int = 22) -> str:
    """Official team mark where we have it, else a colour disc + monogram."""
    tc = team_color(name)
    logo = _team_logos().get(str(name).strip())
    if logo:
        uri, needs_chip = logo
        chip = ("background:rgba(255,255,255,.94);" if needs_chip
                else f"background:linear-gradient(180deg,{tc}22,#0b0b1000);")
        w = int(size * 1.75)
        return (f'<span class="team-badge logo" style="width:{w}px;height:{size}px;{chip}'
                f'border:1px solid {tc}55" title="{esc(name)}">'
                f'<img src="{uri}" alt="{esc(name)} logo"></span>')
    ab = esc(team_abbr(name))
    fs = size * (0.40 if len(ab) > 2 else 0.46)
    return (f'<span class="team-badge" style="width:{size}px;height:{size}px;'
            f'background:radial-gradient(circle at 32% 26%,{tc},#0b0b10 130%);'
            f'border:1px solid {tc};font-size:{fs:.1f}px;color:#fff" title="{esc(name)}">{ab}</span>')


def with_alpha(hex_colour: str, alpha: float) -> str:
    """Convert a six-character CSS hex colour to an rgba colour."""
    return f"rgba({int(hex_colour[1:3], 16)},{int(hex_colour[3:5], 16)},{int(hex_colour[5:7], 16)},{alpha})"


def flag(nat: str) -> str:
    return FLAG_CODE.get(str(nat).strip(), "—").upper()


def flag_url(nat: str) -> str:
    code = FLAG_CODE.get(str(nat).strip())
    return f"https://flagcdn.com/w40/{code}.png" if code else ""


def flag_html(nat: str) -> str:
    code = FLAG_CODE.get(str(nat).strip())
    return (f'<img class="flag-image" src="https://flagcdn.com/w40/{code}.png" '
            f'alt="{code.upper()} flag">' if code else '<span class="flag-code">—</span>')


CFLAG = {
    "UK": "🇬🇧", "USA": "🇺🇸", "Italy": "🇮🇹", "Germany": "🇩🇪", "Spain": "🇪🇸",
    "France": "🇫🇷", "Monaco": "🇲🇨", "Belgium": "🇧🇪", "Netherlands": "🇳🇱",
    "Brazil": "🇧🇷", "Japan": "🇯🇵", "Austria": "🇦🇹", "Australia": "🇦🇺",
    "Canada": "🇨🇦", "Mexico": "🇲🇽", "Bahrain": "🇧🇭", "UAE": "🇦🇪",
    "Saudi Arabia": "🇸🇦", "Qatar": "🇶🇦", "Singapore": "🇸🇬", "Hungary": "🇭🇺",
    "Portugal": "🇵🇹", "Turkey": "🇹🇷", "Russia": "🇷🇺", "China": "🇨🇳",
    "Azerbaijan": "🇦🇿", "India": "🇮🇳", "Malaysia": "🇲🇾", "South Korea": "🇰🇷",
    "South Africa": "🇿🇦", "Argentina": "🇦🇷", "Switzerland": "🇨🇭",
    "Sweden": "🇸🇪", "Morocco": "🇲🇦", "United States": "🇺🇸",
}

CFLAG_CODE = {
    "UK": "gb", "USA": "us", "Italy": "it", "Germany": "de", "Spain": "es", "France": "fr",
    "Monaco": "mc", "Belgium": "be", "Netherlands": "nl", "Brazil": "br", "Japan": "jp",
    "Austria": "at", "Australia": "au", "Canada": "ca", "Mexico": "mx", "Bahrain": "bh",
    "UAE": "ae", "Saudi Arabia": "sa", "Qatar": "qa", "Singapore": "sg", "Hungary": "hu",
    "Portugal": "pt", "Turkey": "tr", "Russia": "ru", "China": "cn", "Azerbaijan": "az",
    "India": "in", "Malaysia": "my", "South Korea": "kr", "South Africa": "za", "Argentina": "ar",
    "Switzerland": "ch", "Sweden": "se", "Morocco": "ma", "United States": "us", "Korea": "kr",
}


def flag_country(country: str) -> str:
    return CFLAG_CODE.get(country, "—").upper()


def flag_country_html(country: str) -> str:
    code = CFLAG_CODE.get(country)
    return (f'<img class="flag-image" src="https://flagcdn.com/w40/{code}.png" '
            f'alt="{code.upper()} flag">' if code else '<span class="flag-code">—</span>')


def esc(value) -> str:
    """Escape database text before inserting it into a small HTML component."""
    return html.escape(str(value))


def fmt_date(value) -> str:
    """SQLite hands dates back as strings; MySQL as date objects."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    try:
        return pd.to_datetime(value).strftime("%d %b %Y")
    except Exception:
        return str(value)[:10]


def display_grid(grid) -> str:
    """Ergast uses grid=0 for a pit-lane start."""
    return "PIT LANE" if grid in (None, 0) or pd.isna(grid) else f"P{int(grid)}"


def enabled_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def inject_css():
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,500;0,600;0,700;0,800;0,900;1,700;1,800;1,900&family=Rajdhani:wght@500;600;700&display=swap');
    :root {{ --signal:{RED}; --ink:#09090b; --panel:#121217; --line:#2b2b32; --muted:#92929d; }}
    .stApp {{ background: radial-gradient(900px 520px at 84% -8%, #3b0b12 0%, transparent 58%), linear-gradient(135deg, #09090b 0%, #0e0e12 52%, #09090b 100%); color:#f4f2ed; }}
    .stApp:before {{ content:""; pointer-events:none; position:fixed; inset:0; opacity:.25; background-image:linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px); background-size:48px 48px; mask-image:linear-gradient(to bottom, black, transparent 55%); }}
    h1,h2,h3 {{ font-family:'Barlow Condensed',sans-serif!important; font-style:italic; font-weight:900!important; letter-spacing:.035em; }}
    h2 {{ font-size:1.7rem!important; margin-top:.55rem!important; }}
    .stApp,p,span,label,div {{ font-family:'Rajdhani',sans-serif; }}
    .block-container {{ max-width:1480px; padding:1.7rem 2.4rem 3rem!important; }}
    section[data-testid="stSidebar"] {{ background:linear-gradient(180deg,#15151b,#0b0b0e 78%); border-right:1px solid #34343b; }}
    section[data-testid="stSidebar"] > div {{ padding-top:1.3rem; }}
    section[data-testid="stSidebar"] [data-testid="stMarkdown"] h3 {{ color:#fff; font-size:1.6rem!important; letter-spacing:.08em; }}
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{ color:#9999a5; }}
    .sidebar-track {{ position:relative; margin:30px 4px 2px; padding:12px 9px 0; border-top:1px solid #2a2a30; }}
    .sidebar-track .st-label {{ color:#9796a0; font-family:'Barlow Condensed'; font-size:.67rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }}
    .sidebar-track .st-name {{ color:#ece9e4; font-family:'Barlow Condensed'; font-style:italic; font-size:.97rem; font-weight:900; line-height:1; }}
    .sidebar-track .st-meta {{ color:#e10600; font-family:'Barlow Condensed'; font-size:.72rem; font-weight:800; letter-spacing:.08em; }}
    [data-testid="stSidebarNav"] {{ display:none; }}
    [data-testid="stSidebar"] .stRadio > label {{ color:#686873; font-size:.67rem; text-transform:uppercase; letter-spacing:.15em; font-weight:700; }}
    [data-testid="stSidebar"] .stRadio [role="radiogroup"] {{ gap:3px; }}
    [data-testid="stSidebar"] .stRadio label {{ border:1px solid transparent; border-radius:4px; padding:9px 8px; transition:.16s ease; }}
    [data-testid="stSidebar"] .stRadio label:hover {{ background:#1d1d23; border-color:#303038; }}
    [data-testid="stSidebar"] .stRadio label:has(input:checked) {{ background:linear-gradient(90deg,rgba(225,6,0,.22),#1b1b21); border-color:#4c2025; box-shadow:inset 3px 0 0 {RED}; }}
    [data-testid="stSidebar"] .stRadio label p {{ font-family:'Barlow Condensed'; font-weight:800; letter-spacing:.055em; font-size:1.05rem; color:#d8d7dc; }}
    .hero {{ position:relative; isolation:isolate; padding:38px 38px 32px; min-height:210px; overflow:hidden; margin:0 0 15px; border:1px solid #3a2227; border-radius:5px; background-image:linear-gradient(90deg,#08080b 0%,rgba(8,8,11,.92) 26%,rgba(10,6,8,.55) 46%,rgba(10,6,8,.12) 62%,transparent 78%),var(--hero-art),linear-gradient(105deg,#1b0509,#101014); background-position:left center,right center,center; background-size:cover,auto 132%,cover; background-repeat:no-repeat; clip-path:polygon(0 0,100% 0,100% 86%,98.5% 100%,0 100%); }}
    .hero:before {{ content:""; position:absolute; z-index:0; inset:0; background:linear-gradient(180deg,transparent 55%,rgba(8,8,11,.55) 100%); pointer-events:none; }}
    .hero:after {{ content:""; position:absolute; z-index:1; left:0; right:0; bottom:0; height:3px; background:linear-gradient(90deg,{RED} 0 22%,rgba(225,6,0,.15) 22% 42%,transparent 42%); }}
    .hero > * {{ position:relative; z-index:2; }}
    .hero h1 {{ position:relative; margin:6px 0 0; color:#fff; font-size:clamp(2.5rem,5vw,4.65rem)!important; line-height:.88; text-shadow:5px 5px 0 rgba(0,0,0,.22); }}
    .hero p {{ max-width:650px; color:#d6c6c9; font-size:1.12rem; font-weight:600; letter-spacing:.015em; margin:.75rem 0 0; }}
    .hero .tag {{ display:inline-block; background:{RED}; color:#fff; font-size:.72rem; font-family:'Barlow Condensed'; font-style:italic; font-weight:800; letter-spacing:.13em; padding:4px 10px 3px; transform:skewX(-11deg); }}
    .flag-image {{ width:18px; height:13px; object-fit:cover; vertical-align:-2px; margin-right:5px; border:1px solid rgba(255,255,255,.25); box-shadow:0 1px 4px rgba(0,0,0,.5); }}
    .flag-code {{ display:inline-block; min-width:21px; color:#d7d5da; font-family:'Barlow Condensed'; font-style:italic; font-weight:800; font-size:.74rem; letter-spacing:.06em; }}
    .checker {{ height:5px; margin:0 0 21px; border-radius:0; opacity:.7; background-image:linear-gradient(45deg,#f1eee7 25%,transparent 25%,transparent 75%,#f1eee7 75%),linear-gradient(45deg,#f1eee7 25%,#17171b 25%,#17171b 75%,#f1eee7 75%); background-size:10px 10px; background-position:0 0,5px 5px; }}
    .section-title {{ display:flex; align-items:center; gap:9px; margin:17px 0 9px; color:#f3f1eb; font-family:'Barlow Condensed'; font-style:italic; font-size:1.2rem; font-weight:900; letter-spacing:.09em; text-transform:uppercase; }}
    .section-title:before {{ content:""; width:24px; height:3px; background:{RED}; box-shadow:8px 0 0 rgba(225,6,0,.28); }}
    .section-caption {{ margin:-5px 0 12px 33px; color:#8f8e99; font-size:.85rem; font-weight:600; }}
    .kpi {{ position:relative; min-height:94px; background:linear-gradient(145deg,#19191f,#111116); border:1px solid #303038; border-top:2px solid {RED}; border-radius:3px; padding:14px 16px 12px; overflow:hidden; }}
    .kpi:after {{ content:""; position:absolute; right:-10px; bottom:-15px; width:75px; height:75px; border:1px solid rgba(255,255,255,.09); border-radius:50%; }}
    .kpi .l {{ color:#9696a1; font-family:'Barlow Condensed'; font-size:.78rem; font-weight:700; text-transform:uppercase; letter-spacing:.13em; }}
    .kpi .v {{ color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-size:2.35rem; font-weight:900; line-height:1.05; }}
    .kpi .i {{ float:right; font-size:1.3rem; opacity:.55; }}
    [data-testid="stMetricValue"] {{ font-family:'Barlow Condensed'; font-style:italic; font-weight:900; }}
    [data-testid="stDataFrame"] {{ border:1px solid #303038; border-radius:3px; overflow:hidden; }}
    [data-testid="stDataFrame"] [role="columnheader"] {{ background:#1c1c22!important; color:#d9d8dc!important; font-family:'Barlow Condensed'!important; font-weight:800!important; text-transform:uppercase; letter-spacing:.06em; }}
    .race-table-wrap {{ max-height:350px; overflow:auto; border:1px solid #3a3a43; border-top:2px solid {RED}; border-radius:4px; background:#111116; }}
    .race-table {{ width:100%; min-width:1200px; border-collapse:collapse; font-size:.88rem; }}
    .race-table th {{ position:sticky; top:0; z-index:2; padding:9px 10px; text-align:left; white-space:nowrap; background:linear-gradient(180deg,#24242c,#1a1a20); color:#c9c8cf; border-bottom:1px solid #555560; border-right:1px solid #393941; font-family:'Barlow Condensed'; font-size:.72rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }}
    .race-table th:first-child {{ color:#ff4a45; }}
    .race-table td {{ padding:7px 10px; color:#e4e2e4; border-bottom:1px solid #292931; border-right:1px solid #24242b; white-space:nowrap; font-weight:600; }}
    .race-table tbody tr {{ transition:background .14s ease; }}
    .race-table tbody tr:hover {{ background:linear-gradient(90deg,rgba(225,6,0,.22),rgba(225,6,0,.03)); }}
    .race-table tbody tr.leader {{ background:linear-gradient(90deg,rgba(225,6,0,.58),rgba(117,10,15,.24) 42%,transparent); }}
    .race-table tbody tr.leader td:first-child {{ box-shadow:inset 3px 0 0 #ff463f; color:#fff; }}
    .race-table .rank {{ color:#f7f5ef; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; text-align:center; }}
    .race-table .driver-name {{ color:#fff; font-family:'Barlow Condensed'; font-size:1rem; font-style:italic; font-weight:800; }}
    .race-table .nationality {{ color:#d5d4d9; }}
    .stButton > button {{ min-height:38px; border-radius:3px; border:1px solid #a90500; background:{RED}; color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-weight:800; letter-spacing:.07em; text-transform:uppercase; box-shadow:3px 3px 0 #510300; }}
    .stButton > button:hover {{ background:#ff1a14; border-color:#ff5b55; transform:translate(-1px,-1px); box-shadow:4px 4px 0 #510300; }}
    [data-baseweb="select"] > div, .stTextArea textarea {{ background:#16161b!important; border-color:#3a3a43!important; border-radius:3px!important; color:#f4f2ed!important; }}
    .stSlider [data-baseweb="slider"] div[role="slider"] {{ background:{RED}; }}
    .stTabs [data-baseweb="tab-list"] {{ gap:24px; border-bottom:1px solid #303038; }}
    .stTabs [data-baseweb="tab"] {{ font-family:'Barlow Condensed'; font-style:italic; font-weight:800; color:#9b9ba6; letter-spacing:.055em; padding:10px 2px; }}
    .stTabs [aria-selected="true"] {{ color:#fff!important; border-bottom-color:{RED}!important; }}

    /* --- race explorer: map card + podium rows --- */
    .mapcard {{ position:relative; border-radius:4px; padding:18px 20px;
        background: radial-gradient(700px 300px at 20% -20%, rgba(225,6,0,.18) 0%, rgba(0,0,0,0) 60%),
                    linear-gradient(180deg,#12121a 0%, #0a0a0e 100%);
        border:1px solid #303038; border-top:3px solid {RED}; box-shadow: inset 0 0 90px rgba(0,0,0,.55); }}
    .mapcard .cn {{ font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:1.65rem; letter-spacing:.035em; color:#fff;
        text-shadow:0 2px 18px rgba(225,6,0,.4); margin:0 0 2px; }}
    .mapcard .cm {{ color:#b9bcc6; font-size:.92rem; margin-bottom:8px; }}
    .mapcard .cm .dot {{ color:{RED}; margin:0 7px; }}
    .mapcard .stat {{ display:inline-block; margin:8px 10px 0 0; color:#8c909b; font-size:.82rem;
        text-transform:uppercase; letter-spacing:1px; }}
    .mapcard .stat b {{ color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-size:1.2rem; letter-spacing:0; }}

    .podwrap {{ display:flex; flex-direction:column; gap:12px; }}
    .drow {{ display:flex; align-items:center; gap:14px; background:{CARD};
        border:1px solid #262631; border-left:5px solid #444; border-radius:14px;
        padding:12px 14px; position:relative; overflow:hidden; }}
    .drow:before {{ content:""; position:absolute; right:-30px; top:-30px; width:120px; height:120px;
        border-radius:50%; opacity:.12; }}
    .drow .pos {{ font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:1.7rem; width:40px; text-align:center; }}
    .avatar {{ width:60px; height:60px; min-width:60px; border-radius:50%; display:flex;
        align-items:center; justify-content:center; font-family:'Barlow Condensed'; font-style:italic; font-weight:800;
        font-size:1.15rem; color:#fff; box-shadow: inset 0 0 18px rgba(0,0,0,.55); }}
    .dmeta {{ flex:1; min-width:0; }}
    .dmeta .nm {{ font-family:'Barlow Condensed'; font-style:italic; font-weight:800; font-size:1.05rem; color:#fff; }}
    .dmeta .tm {{ font-weight:700; font-size:.9rem; margin:1px 0 6px; }}
    .chip {{ display:inline-block; background:#1c1c25; border:1px solid #33333f; border-radius:20px;
        padding:2px 10px; margin:2px 6px 0 0; font-size:.78rem; color:#d7d9df; }}
    .chip b {{ color:#fff; }}

    /* podium stand */
    .pstand {{ display:flex; align-items:flex-end; justify-content:center; gap:10px; margin-top:4px; }}
    .pcol {{ width:33%; max-width:158px; text-align:center; }}
    .ptop {{ background:{CARD}; border:1px solid #303038; border-radius:3px 3px 0 0; padding:10px 6px 12px;
        margin-bottom:8px; }}
    .ptop .medal {{ font-size:1.25rem; line-height:1; }}
    .phead {{ border-radius:50%; object-fit:cover; object-position:center 15%; margin:4px 0 2px;
        border:2px solid #0c0c11; }}
    .avatar {{ border-radius:50%; display:inline-flex; align-items:center; justify-content:center;
        font-family:'Barlow Condensed'; font-style:italic; font-weight:800; color:#fff; margin:4px 0 2px; }}
    .pn {{ font-family:'Barlow Condensed'; font-style:italic; font-weight:800; font-size:1rem; color:#fff; margin-top:5px; line-height:1.15; }}
    .pt {{ font-size:.76rem; font-weight:700; }}
    .pchips {{ font-size:.73rem; color:#aeb1ba; margin-top:4px; }}
    .pblock {{ border-radius:3px 3px 0 0; display:flex; align-items:flex-start; justify-content:center;
        padding-top:10px; color:#0a0a0a; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:2.4rem;
        box-shadow:0 -6px 26px rgba(0,0,0,.5); }}
    /* --- driver dossier: the career deep-dive identity screen --- */
    .dossier-title {{ display:flex; align-items:center; gap:9px; color:#f5f3ee; font-family:'Barlow Condensed'; font-weight:900; font-style:italic; font-size:1.32rem; letter-spacing:.075em; text-transform:uppercase; margin:0 0 8px; }}
    .dossier-title:before {{ content:""; width:8px; height:8px; background:{RED}; border-radius:50%; box-shadow:0 0 12px {RED}; }}
    .driver-card {{ position:relative; isolation:isolate; height:282px; overflow:hidden; border:1px solid #5d1b22; border-bottom:3px solid {RED}; border-radius:4px; padding:16px; background:linear-gradient(145deg,#201015,#120f13 60%,#09090b); }}
    @supports (color: color-mix(in srgb, red 50%, transparent)) {{ .driver-card {{ background:radial-gradient(220px 190px at 85% 38%, color-mix(in srgb, var(--team) 34%, transparent), transparent 68%), linear-gradient(145deg,#201015,#120f13 60%,#09090b); }} }}
    .driver-card:before {{ content:""; position:absolute; z-index:-1; top:-64px; left:-10px; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:13rem; line-height:1; letter-spacing:-.12em; color:rgba(255,255,255,.055); }}
    .driver-card:after {{ content:""; position:absolute; z-index:-1; inset:0; opacity:.28; background:repeating-linear-gradient(135deg,transparent 0 17px,rgba(255,255,255,.035) 17px 18px); }}
    .driver-card .licence {{ position:relative; z-index:2; width:55%; }}
    .driver-card .eyebrow {{ color:#a9a8b0; font-family:'Barlow Condensed'; font-weight:800; letter-spacing:.13em; font-size:.72rem; text-transform:uppercase; }}
    .driver-card .code {{ font-family:'Barlow Condensed'; font-style:italic; font-size:3.15rem; font-weight:900; line-height:.85; letter-spacing:-.04em; color:#fff; text-shadow:3px 3px 0 rgba(0,0,0,.3); margin-top:4px; }}
    .driver-card .country {{ color:#e2e0e2; font-weight:700; font-size:.9rem; margin-top:7px; }}
    .driver-card .team-name {{ display:inline-block; margin-top:12px; color:#0d0d10; background:var(--team); padding:3px 8px; font-family:'Barlow Condensed'; font-style:italic; font-size:.85rem; font-weight:900; letter-spacing:.05em; text-transform:uppercase; }}
    .driver-card .career {{ margin-top:34px; color:#bbb9c0; font-size:.78rem; text-transform:uppercase; letter-spacing:.1em; }}
    .driver-card .career b {{ display:block; color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-size:1.22rem; letter-spacing:.035em; }}
    .driver-number {{ position:absolute; z-index:1; top:6px; right:13px; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:8.6rem; line-height:1; color:rgba(255,255,255,.12); -webkit-text-stroke:1px rgba(255,255,255,.17); }}
    .driver-photo {{ position:absolute; z-index:2; right:-11px; bottom:-3px; width:61%; height:235px; object-fit:cover; object-position:center 13%; filter:drop-shadow(-12px 4px 14px rgba(0,0,0,.72)) saturate(1.08); -webkit-mask-image:linear-gradient(to right,transparent 0%,#000 24%,#000 100%),linear-gradient(to top,#000 78%,transparent 100%); -webkit-mask-composite:source-in; }}
    .driver-avatar-large {{ position:absolute; z-index:2; right:13px; bottom:16px; width:144px; height:144px; border-radius:50%; display:flex; align-items:center; justify-content:center; color:#fff; border:2px solid var(--team); font-family:'Barlow Condensed'; font-size:3.2rem; font-style:italic; font-weight:900; background:radial-gradient(circle at 32% 25%,var(--team),#101014 65%); box-shadow:0 0 0 8px rgba(0,0,0,.18); }}
    .telemetry-stack {{ height:282px; border:1px solid #303038; border-radius:4px; overflow:hidden; background:linear-gradient(180deg,#17171d,#0e0e12); }}
    .telemetry-head {{ padding:10px 13px 8px; border-bottom:1px solid #303038; color:#a6a5ae; font-family:'Barlow Condensed'; font-weight:800; font-size:.73rem; letter-spacing:.14em; text-transform:uppercase; }}
    .telemetry {{ display:flex; align-items:center; gap:10px; padding:9px 12px; min-height:48px; border-bottom:1px solid rgba(255,255,255,.075); }}
    .telemetry:last-child {{ border-bottom:0; }}
    .telemetry .ti {{ color:var(--team); font-size:1.12rem; width:18px; text-align:center; }}
    .telemetry .tl {{ color:#9998a2; font-family:'Barlow Condensed'; font-weight:700; font-size:.7rem; letter-spacing:.11em; text-transform:uppercase; line-height:1; }}
    .telemetry .tv {{ color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:1.35rem; line-height:.95; }}
    .telemetry .tl,.telemetry .tv {{ display:block; }}
    .chart-panel-label {{ position:relative; border:1px solid #303038; border-top:3px solid var(--team); border-bottom:0; border-radius:4px 4px 0 0; padding:11px 14px 8px; overflow:hidden; background:linear-gradient(90deg,#17171d,#101014); color:#d7d5db; font-family:'Barlow Condensed'; font-weight:800; font-size:.78rem; letter-spacing:.12em; text-transform:uppercase; }}
    .chart-panel-label:after {{ content:""; position:absolute; width:90px; height:90px; right:-17px; top:-55px; opacity:.1; background:repeating-conic-gradient(#fff 0 10deg,transparent 10deg 20deg); }}
    .driver-circuit {{ margin-top:9px; min-height:136px; overflow:hidden; border:1px solid #303038; border-left:3px solid var(--team); border-radius:4px; padding:9px 11px 4px; background:linear-gradient(135deg,#15151b,#0d0d11); }}
    .driver-circuit .dc-label {{ color:#a7a6b0; font-family:'Barlow Condensed'; font-size:.68rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }}
    .driver-circuit .dc-name {{ color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-size:1.1rem; font-weight:900; line-height:1; }}
    .driver-circuit .dc-wins {{ color:var(--team); float:right; font-family:'Barlow Condensed'; font-style:italic; font-size:1.15rem; font-weight:900; }}
    @media (max-width: 1100px) {{ .driver-card .licence {{ width:76%; }} .driver-card .code {{ font-size:2.65rem; }} .driver-photo {{ opacity:.52; }} .driver-avatar-large {{ opacity:.72; }} }}
    @media (max-width: 760px) {{ .driver-card,.telemetry-stack {{ height:auto; min-height:260px; }} .driver-photo {{ width:62%; opacity:.7; }} }}
    @media (max-width: 760px) {{ .block-container {{ padding:1rem .85rem 2rem!important; }} .hero {{ padding:28px 22px 26px; min-height:165px; }} .hero h1 {{ font-size:2.55rem!important; }} .kpi {{ min-height:82px; padding:10px; }} .kpi .v {{ font-size:1.75rem; }} .pstand {{ gap:4px; }} .pcol {{ width:34%; }} }}

    /* ===== richer red accent + shared panels ===== */
    .kpi {{ background:linear-gradient(150deg,#1d1216 0%,#141017 46%,#0f0f14 100%); border:1px solid #3a2328; border-top:2px solid {RED}; box-shadow:0 10px 30px -18px rgba(225,6,0,.6), inset 0 1px 0 rgba(255,255,255,.04); }}
    .kpi:hover {{ border-color:#5a2830; box-shadow:0 14px 34px -16px rgba(225,6,0,.75); }}
    .kpi .i {{ color:{RED}; opacity:.85; }}
    .panel {{ position:relative; border:1px solid #2c1c20; border-radius:6px; padding:15px 17px 12px; overflow:hidden;
        background:radial-gradient(600px 220px at 100% -30%, rgba(225,6,0,.13), transparent 60%), linear-gradient(180deg,#16121629 0%,#101015 60%), #0f0f13; }}
    .panel:before {{ content:""; position:absolute; left:0; top:0; width:3px; height:100%; background:linear-gradient(180deg,{RED},transparent 70%); }}
    .panel-head {{ display:flex; align-items:center; justify-content:space-between; margin:0 0 10px; }}
    .panel-head .t {{ display:flex; align-items:center; gap:9px; color:#f3f1eb; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:1.12rem; letter-spacing:.07em; text-transform:uppercase; }}
    .panel-head .t:before {{ content:""; width:16px; height:3px; background:{RED}; }}
    .panel-head .pill {{ color:#c9c7cf; font-family:'Barlow Condensed'; font-weight:800; font-size:.68rem; letter-spacing:.12em; text-transform:uppercase; background:#1c1c22; border:1px solid #34343d; padding:3px 9px; border-radius:3px; }}

    /* champion leader cards */
    .lead-scroll {{ display:flex; gap:12px; overflow-x:auto; padding:4px 2px 10px; }}
    .lead-card {{ position:relative; flex:0 0 168px; height:230px; border-radius:6px; overflow:hidden; border:1px solid #33232a; background:#141014; isolation:isolate; }}
    .lead-card img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; object-position:center 12%; z-index:-2; }}
    .lead-card .avatar {{ position:absolute; inset:0; width:100%; height:100%; border-radius:0; font-size:3rem; z-index:-2; }}
    .lead-card:after {{ content:""; position:absolute; inset:0; z-index:-1; background:linear-gradient(180deg,rgba(10,8,10,.15) 0%,rgba(10,8,10,.35) 45%,rgba(9,8,10,.96) 88%); }}
    .lead-card .big {{ position:absolute; top:8px; left:12px; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:3.4rem; line-height:.85; color:#fff; text-shadow:0 2px 14px rgba(225,6,0,.75), 0 0 2px #000; }}
    .lead-card .big:after {{ content:""; display:block; width:30px; height:3px; background:{RED}; margin-top:2px; }}
    .lead-card .info {{ position:absolute; left:12px; right:12px; bottom:11px; }}
    .lead-card .nm {{ color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-weight:800; font-size:1.02rem; line-height:1; }}
    .lead-card .yr {{ color:#b9b7bf; font-size:.72rem; font-weight:600; margin-top:3px; line-height:1.15; }}

    /* latest winner cards */
    .win-scroll {{ display:flex; gap:11px; overflow-x:auto; padding:4px 2px 8px; }}
    .win-card {{ flex:0 0 210px; display:flex; gap:11px; align-items:center; padding:10px 12px; border-radius:5px; background:linear-gradient(120deg,#181318,#101014); border:1px solid #2c2129; border-left:3px solid var(--team,{RED}); }}
    .win-card img.face {{ width:46px; height:46px; border-radius:50%; object-fit:cover; object-position:center 15%; border:2px solid var(--team,{RED}); }}
    .win-card .avatar {{ width:46px; height:46px; font-size:1rem; }}
    .win-card .yr {{ color:{RED}; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:1.2rem; line-height:1; }}
    .win-card .gp {{ color:#9a99a3; font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; }}
    .win-card .nm {{ color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-weight:800; font-size:.92rem; line-height:1.05; }}
    .win-card .tm {{ font-size:.72rem; font-weight:700; color:var(--team,#c9c7cf); }}

    /* schema browser */
    .schema-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:14px; }}
    .schema-card {{ border:1px solid #2c1c20; border-radius:6px; overflow:hidden; background:linear-gradient(180deg,#15151b,#0f0f13); }}
    .schema-card .sc-head {{ display:flex; align-items:center; justify-content:space-between; padding:10px 13px; background:linear-gradient(90deg,rgba(225,6,0,.22),#181820); border-bottom:1px solid #33333c; }}
    .schema-card .sc-name {{ color:#fff; font-family:'Barlow Condensed'; font-style:italic; font-weight:900; font-size:1.05rem; letter-spacing:.05em; }}
    .schema-card .sc-count {{ color:#a7a6b0; font-family:'Barlow Condensed'; font-weight:700; font-size:.72rem; letter-spacing:.08em; }}
    .schema-row {{ display:flex; align-items:center; gap:8px; padding:6px 13px; border-bottom:1px solid #20202700; }}
    .schema-row:nth-child(odd) {{ background:rgba(255,255,255,.014); }}
    .schema-col {{ flex:1; color:#e2e0e6; font-weight:600; font-size:.86rem; }}
    .schema-type {{ color:#8f8e99; font-family:'Barlow Condensed'; font-weight:700; font-size:.74rem; letter-spacing:.04em; text-transform:uppercase; }}
    .badge {{ font-family:'Barlow Condensed'; font-weight:800; font-size:.62rem; letter-spacing:.05em; padding:1px 6px; border-radius:3px; }}
    .badge.pk {{ background:{RED}; color:#fff; }}
    .badge.fk {{ background:#20303f; color:#7db4ff; border:1px solid #2f5d86; }}

    /* constructor register table extras */
    .race-table .team-dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:8px; vertical-align:0; box-shadow:0 0 8px currentColor; }}
    .team-badge.logo {{ overflow:hidden; padding:2px 3px; border-radius:4px; }}
    .team-badge.logo img {{ width:100%; height:100%; object-fit:contain; display:block; }}
    .team-badge {{ display:inline-flex; align-items:center; justify-content:center; border-radius:50%;
        font-family:'Barlow Condensed'; font-style:italic; font-weight:900; letter-spacing:.02em;
        margin-right:8px; vertical-align:-5px; box-shadow:0 0 10px -2px currentColor; flex:none; }}

    /* results table colouring */
    .race-table td.pos-1 {{ color:#FFCF40; font-weight:900; }}
    .race-table td.pos-2 {{ color:#D6DAE4; font-weight:900; }}
    .race-table td.pos-3 {{ color:#E08B45; font-weight:900; }}
    .race-table td.pts {{ color:#fff; font-weight:800; }}
    .race-table td.pts.zero {{ color:#6d6d78; font-weight:600; }}
    .race-table tr.p1 {{ background:linear-gradient(90deg,rgba(255,207,64,.16),transparent 55%); }}
    .race-table tr.p2 {{ background:linear-gradient(90deg,rgba(214,218,228,.11),transparent 55%); }}
    .race-table tr.p3 {{ background:linear-gradient(90deg,rgba(224,139,69,.12),transparent 55%); }}
    .race-table tr.dnf td {{ color:#8d8d97; }}
    .status-chip {{ display:inline-block; padding:1px 8px; border-radius:3px; font-family:'Barlow Condensed';
        font-weight:800; font-size:.72rem; letter-spacing:.05em; text-transform:uppercase; }}
    .status-chip.fin {{ background:rgba(46,160,67,.16); color:#5ddb7d; border:1px solid rgba(46,160,67,.4); }}
    .status-chip.lap {{ background:rgba(180,150,40,.14); color:#e0c060; border:1px solid rgba(180,150,40,.36); }}
    .status-chip.out {{ background:rgba(225,6,0,.16); color:#ff6b64; border:1px solid rgba(225,6,0,.4); }}

    /* schema ERD */
    .erd-wrap {{ overflow-x:auto; border:1px solid #2c1c20; border-radius:6px; padding:6px 8px;
        background:radial-gradient(700px 300px at 30% -20%, rgba(225,6,0,.10), transparent 60%), #0d0d11; }}
    </style>
    """, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def hero_art_data() -> str:
    """Compact JPEG keeps the inline CSS small enough for Streamlit to deliver."""
    for name, mime in (("f1-hero-car.jpg", "jpeg"), ("f1-hero-car.png", "png")):
        art = HERE / "assets" / name
        if art.exists():
            return f"url(data:image/{mime};base64,{base64.b64encode(art.read_bytes()).decode()})"
    return "linear-gradient(105deg,#1b0509,#101014)"


def hero(title, subtitle, tag="FORMULA 1 · 1950–2024"):
    hero_art = hero_art_data()
    st.markdown(f"""<div class="hero" style="--hero-art:{hero_art}"><span class="tag">/// {tag}</span>
        <h1>{title}</h1><p>{subtitle}</p></div>
        <div class="checker"></div>""", unsafe_allow_html=True)


def kpi_row(items):
    for col, (label, value, icon) in zip(st.columns(len(items)), items):
        col.markdown(f"""<div class="kpi"><span class="i">{icon}</span>
            <div class="l">{label}</div><div class="v">{value}</div></div>""",
                     unsafe_allow_html=True)


def section_header(title, caption=""):
    st.markdown(f'<div class="section-title">{esc(title)}</div>', unsafe_allow_html=True)
    if caption:
        st.markdown(f'<div class="section-caption">{esc(caption)}</div>', unsafe_allow_html=True)


def driver_table(df: pd.DataFrame, start_rank: int = 1) -> str:
    """Compact scrollable standings table with the flag embedded in nationality."""
    columns = [
        ("driver", "Driver"), ("nationality", "Nationality"), ("starts", "Starts"),
        ("wins", "Wins"), ("podiums", "Podiums"), ("poles", "Poles"), ("points", "Points"),
        ("points_per_start", "Pts / start"), ("podium_pct", "Podium %"), ("win_pct", "Win %"),
        ("dnf_rate", "DNF rate"), ("avg_grid", "Avg grid"), ("teams", "Teams"),
        ("first_season", "First"), ("last_season", "Last"),
    ]
    percent = {"podium_pct", "win_pct", "dnf_rate"}
    one_decimal = {"points", "points_per_start", "avg_grid"}
    head = "".join(f"<th>{label}</th>" for _, label in columns)
    rows = []
    for rank, (_, record) in enumerate(df.iterrows(), start_rank):
        cells = [f'<td class="rank">{rank:02d}</td>']
        for field, _ in columns:
            value = record[field]
            if field == "driver":
                content = f'<span class="driver-name">{esc(value)}</span>'
            elif field == "nationality":
                content = f'<span class="nationality">{flag_html(value)}{esc(value)}</span>'
            elif pd.isna(value):
                content = "—"
            elif field in percent:
                content = f"{float(value):.2f}%"
            elif field in one_decimal:
                content = f"{float(value):.2f}" if field == "points_per_start" else f"{float(value):.1f}"
            else:
                content = f"{int(value):,}" if isinstance(value, (int, float, np.integer, np.floating)) else esc(value)
            cells.append(f"<td>{content}</td>")
        rows.append(f'<tr class="leader"' if rank == 1 else "<tr>")
        rows[-1] += "".join(cells) + "</tr>"
    return f'<div class="race-table-wrap"><table class="race-table"><thead><tr><th>Rank</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def plot(fig, h=380):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=h,
                      title=dict(text=""),
                      margin=dict(l=10, r=55, t=20, b=10),
                      font=dict(family="Rajdhani, sans-serif", size=14))
    fig.update_xaxes(gridcolor=LINE); fig.update_yaxes(gridcolor=LINE)
    st.plotly_chart(fig, width='stretch')


import json as _json
import unicodedata as _ud
import difflib as _dl


def _norm(s):
    s = _ud.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in s if ch.isalnum() or ch == " ").strip()


@st.cache_data(show_spinner=False)
def circuit_coords():
    """Map circuitId -> real track [[lng,lat],...] from the bundled f1-circuits geojson."""
    path = HERE / "assets" / "f1-circuits.geojson"
    if not path.exists():
        return {}
    g = _json.loads(path.read_text(encoding="utf-8"))
    cir = q("SELECT circuitId, name, location FROM circuits")
    cir["nn"], cir["nl"] = cir["name"].map(_norm), cir["location"].map(_norm)
    out = {}
    for feat in g["features"]:
        p = feat["properties"]
        gn, gl = _norm(p.get("Name", "")), _norm(p.get("Location", ""))
        candidates = []
        for _, r in cir.iterrows():
            nm = _dl.SequenceMatcher(None, gn, r.nn).ratio()
            lo = _dl.SequenceMatcher(None, gl, r.nl).ratio()
            sc = 0.45 * nm + 0.55 * lo
            candidates.append((sc, lo, nm, r))
        candidates.sort(key=lambda item: item[0], reverse=True)
        best_sc, best_loc, best_nm, best = candidates[0]
        runner_up = candidates[1][0] if len(candidates) > 1 else 0
        # A wrong circuit is worse than no circuit: reject fuzzy ties rather than guessing.
        if best_sc >= 0.55 and best_sc - runner_up >= .035 and (best_loc >= 0.6 or best_nm >= 0.72):
            out[int(best.circuitId)] = feat["geometry"]["coordinates"]
    return out


def circuit_map_svg(coords, w=520, h=340, pad=26):
    """Sleek SVG of a real track from [lng,lat] coordinates (equirectangular)."""
    lat0 = sum(c[1] for c in coords) / len(coords)
    k = math.cos(math.radians(lat0))
    xs = [c[0] * k for c in coords]
    ys = [c[1] for c in coords]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    spanx, spany = (maxx - minx) or 1e-6, (maxy - miny) or 1e-6
    scale = min((w - 2 * pad) / spanx, (h - 2 * pad) / spany)
    offx = (w - spanx * scale) / 2
    offy = (h - spany * scale) / 2
    pt = lambda x, y: (offx + (x - minx) * scale, h - (offy + (y - miny) * scale))
    d = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in (pt(x, y) for x, y in zip(xs, ys))) + " Z"
    sx, sy = pt(xs[0], ys[0])
    return f'''<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">
      <defs><filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="4" result="b"/><feMerge>
        <feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
      <path d="{d}" fill="none" stroke="#26262f" stroke-width="11" stroke-linejoin="round" stroke-linecap="round"/>
      <path d="{d}" fill="none" stroke="#e10600" stroke-width="3.4" stroke-linejoin="round"
            stroke-linecap="round" filter="url(#glow)"/>
      <circle cx="{sx:.1f}" cy="{sy:.1f}" r="6" fill="#fff"/>
      <circle cx="{sx:.1f}" cy="{sy:.1f}" r="10" fill="none" stroke="#e10600" stroke-width="2.5"/>
    </svg>'''


@st.cache_data(ttl=600, show_spinner=False)
def _portrait_b64(driver_id):
    p = HERE / "assets" / "drivers" / f"{int(driver_id)}.jpg"
    if p.exists():
        return base64.b64encode(p.read_bytes()).decode()
    return None


def portrait(driver_id, name, tc, size=74):
    """Real photo when we have one; otherwise a monogram — never a synthetic face."""
    b64 = _portrait_b64(driver_id)
    if b64:
        return (f'<img class="phead" alt="Portrait of {esc(name)}" src="data:image/jpeg;base64,{b64}" '
                f'style="width:{size}px;height:{size}px;box-shadow:0 0 0 3px {tc}">')
    ini = "".join(w[0] for w in name.split()[:2]).upper()
    return (f'<div class="avatar" style="width:{size}px;height:{size}px;font-size:{size*0.34:.0f}px;'
            f'background:radial-gradient(circle at 35% 30%,{tc},#0c0c11);'
            f'box-shadow:0 0 0 3px {tc}">{esc(ini)}</div>')


def driver_dossier(driver_id, driver, info, career):
    """A game-style driver licence for the career deep-dive screen."""
    team = esc(info["constructor"])
    tc = team_color(info["constructor"])
    raw_code = info["code"]
    code = esc("".join(part[0] for part in driver.split()[:3]).upper()
               if pd.isna(raw_code) or not str(raw_code).strip() else raw_code)
    number = "—" if pd.isna(info["number"]) else esc(int(info["number"]))
    first = "—" if pd.isna(career["first_season"]) else int(career["first_season"])
    last = "—" if pd.isna(career["last_season"]) else int(career["last_season"])
    photo = _portrait_b64(driver_id)
    if photo:
        face = (f'<img class="driver-photo" alt="Portrait of {esc(driver)}" '
                f'src="data:image/jpeg;base64,{photo}">')
    else:
        initials = esc("".join(part[0] for part in driver.split()[:2]).upper())
        face = f'<div class="driver-avatar-large">{initials}</div>'
    return f'''<div class="driver-card" style="--team:{tc}">
        <div class="driver-number">{number}</div>
        <div class="licence">
          <div class="eyebrow">Driver dossier · #{number}</div>
          <div class="code">{code}</div>
          <div class="country">{flag_html(info['nationality'])} {esc(info['nationality'])}</div>
          <div class="team-name" style="color:{on_team_text(info['constructor'])}">{team}</div>
          <div class="career">Active record <b>{first} — {last}</b></div>
        </div>{face}</div>'''


def telemetry_stack(career, team_colour):
    count = lambda value: "—" if pd.isna(value) else f"{int(value):,}"
    metrics = [
        ("🏆", "Race wins", count(career["wins"])),
        ("◆", "Podiums", count(career["podiums"])),
        ("⚡", "Pole positions", count(career["poles"])),
        ("◴", "Win conversion", "—" if pd.isna(career["win_pct"]) else f"{career['win_pct']}%"),
        ("⚠", "DNF rate", "—" if pd.isna(career["dnf_rate"]) else f"{career['dnf_rate']}%"),
    ]
    entries = "".join(f'''<div class="telemetry"><div class="ti">{icon}</div><div>
        <span class="tl">{label}</span><span class="tv">{value}</span></div></div>'''
                      for icon, label, value in metrics)
    return f'''<div class="telemetry-stack" style="--team:{team_colour}">
        <div class="telemetry-head">Career telemetry</div>{entries}</div>'''


def favourite_circuit_card(circuit, coords, team_colour):
    if circuit is None:
        return f'''<div class="driver-circuit" style="--team:{team_colour}">
            <div class="dc-label">Most victorious circuit</div>
            <div class="dc-name">No race win recorded</div></div>'''
    layout = (circuit_map_svg(coords, w=400, h=89, pad=12) if coords else
              '<div style="height:72px;display:flex;align-items:center;justify-content:center;color:#8f8e98;font-family:Barlow Condensed;font-style:italic;font-weight:700">◇ layout unavailable for this circuit</div>')
    return f'''<div class="driver-circuit" style="--team:{team_colour}">
        <div class="dc-label">Most victorious circuit <span class="dc-wins">{int(circuit['wins'])} WINS</span></div>
        <div class="dc-name">{flag_country_html(circuit['country'])}{esc(circuit['circuit'])}</div>
        {layout}</div>'''


def podium(rows, ids):
    """Real podium stand: P2 left, P1 centre (tallest), P3 right."""
    spec = {1: (152, "#FFCF40", "🥇"), 2: (114, "#C4C9D4", "🥈"), 3: (92, "#C97A3A", "🥉")}
    html = '<div class="pstand">'
    for rank in (2, 1, 3):
        r = rows[rank - 1]
        h, col, medal = spec[rank]
        tc = team_color(r["constructor"])
        pts = "0" if r["points"] in (None, 0) else f"{int(r['points'])}"
        html += f'''<div class="pcol">
            <div class="ptop" style="border-top:3px solid {tc}">
              <div class="medal">{medal}</div>
              {portrait(ids[rank - 1], r['driver'], tc, 70 if rank == 1 else 60)}
              <div class="pn">{flag_html(r['nationality'])} {esc(r['driver'])}</div>
              <div class="pt" style="color:{team_text_color(r['constructor'])}">{team_badge(r['constructor'], 15)}{esc(r['constructor'])}</div>
              <div class="pchips">Grid {display_grid(r['grid'])} · {pts} pts</div>
            </div>
            <div class="pblock" style="height:{h}px;
              background:linear-gradient(180deg,{col} 0%, rgba(0,0,0,.4) 130%)">{rank}</div>
          </div>'''
    return html + "</div>"


NAT_COUNTRY = {
    "British": "United Kingdom", "German": "Germany", "Brazilian": "Brazil",
    "Finnish": "Finland", "French": "France", "Austrian": "Austria",
    "Australian": "Australia", "Spanish": "Spain", "Italian": "Italy",
    "American": "United States", "Argentine": "Argentina", "Argentine-Italian": "Argentina",
    "New Zealander": "New Zealand", "South African": "South Africa", "Dutch": "Netherlands",
    "Canadian": "Canada", "Mexican": "Mexico", "Swedish": "Sweden", "Belgian": "Belgium",
    "Swiss": "Switzerland", "Monegasque": "Monaco", "Japanese": "Japan", "Colombian": "Colombia",
}


def _face_src(driver_id):
    """Only ever returns a real photograph; callers fall back to a monogram."""
    b = _portrait_b64(driver_id)
    return f"data:image/jpeg;base64,{b}" if b else None


def _initials(name):
    return esc("".join(w[0] for w in str(name).split()[:2]).upper())


def lead_card(count, driver_id, name, years, nat, tc):
    src = _face_src(driver_id)
    face = (f'<img src="{src}" alt="{esc(name)}">' if src else
            f'<div class="avatar" style="background:radial-gradient(circle at 40% 30%,{tc},#0c0c11)">{_initials(name)}</div>')
    return (f'<div class="lead-card">{face}<div class="big">{esc(count)}</div>'
            f'<div class="info"><div class="nm">{flag_html(nat)}{esc(name)}</div>'
            f'<div class="yr">{esc(years)}</div></div></div>')


def win_card(year, gp, driver_id, name, team, nat):
    tc = team_color(team)
    src = _face_src(driver_id)
    face = (f'<img class="face" src="{src}" alt="{esc(name)}" style="border-color:{tc}">' if src else
            f'<div class="avatar" style="background:radial-gradient(circle at 40% 30%,{tc},#0c0c11)">{_initials(name)}</div>')
    return (f'<div class="win-card" style="--team:{tc}">{face}<div style="min-width:0">'
            f'<div class="yr">{esc(year)}</div><div class="gp">{esc(gp)}</div>'
            f'<div class="nm">{flag_html(nat)}{esc(name)}</div>'
            f'<div class="tm" style="color:{team_text_color(team)}">{team_badge(team, 16)}{esc(team)}</div></div></div>')


def panel_open(title, pill=""):
    p = f'<span class="pill">{esc(pill)}</span>' if pill else ""
    return f'<div class="panel-head" style="margin:16px 0 2px"><span class="t">{esc(title)}</span>{p}</div>'


# ----------------------------------------------------------------------------
SQLITE_PATH = HERE / "data" / "f1_analytics.sqlite"


def _register_sqlite_functions(dbapi_con, _):
    """Teach SQLite the handful of MySQL functions the queries rely on."""
    import math
    from datetime import date, datetime

    def _concat(*parts):
        return "".join("" if p is None else str(p) for p in parts)

    def _timestampdiff(unit, start, end):
        if start is None or end is None:
            return None
        def _d(v):
            if isinstance(v, (date, datetime)):
                return v
            return datetime.fromisoformat(str(v)[:10])
        a, b = _d(start), _d(end)
        years = b.year - a.year - ((b.month, b.day) < (a.month, a.day))
        return years if str(unit).upper() == "YEAR" else (b - a).days

    dbapi_con.create_function("CONCAT", -1, _concat)
    dbapi_con.create_function("TIMESTAMPDIFF", 3, _timestampdiff)
    dbapi_con.create_function("FLOOR", 1, lambda x: None if x is None else math.floor(x))


@st.cache_resource
def get_engine():
    """MySQL when it is reachable, otherwise the bundled read-only SQLite build."""
    url = os.getenv("F1_DB_URL")
    if url:
        eng = create_engine(url)
        if eng.dialect.name == "sqlite":
            event.listen(eng, "connect", _register_sqlite_functions)
        return eng

    if enabled_env("F1_DEPLOYED"):
        missing = [k for k in ("F1_DB_USER", "F1_DB_PASSWORD", "F1_DB_HOST", "F1_DB_NAME")
                   if not os.getenv(k)]
        if missing and not SQLITE_PATH.exists():
            raise RuntimeError("Missing deployment configuration: " + ", ".join(missing))

    if not (enabled_env("F1_DEPLOYED") and not os.getenv("F1_DB_HOST")):
        u = os.getenv("F1_DB_USER", "root"); p = os.getenv("F1_DB_PASSWORD", "root")
        h = os.getenv("F1_DB_HOST", "127.0.0.1"); port = os.getenv("F1_DB_PORT", "3306")
        name = os.getenv("F1_DB_NAME", "f1_analytics")
        try:
            eng = create_engine(f"mysql+pymysql://{u}:{p}@{h}:{port}/{name}?charset=utf8mb4",
                                pool_pre_ping=True, connect_args={"connect_timeout": 4})
            with eng.connect() as c:
                c.execute(text("SELECT 1"))
            return eng
        except Exception:
            pass

    if not SQLITE_PATH.exists():
        raise RuntimeError("No MySQL server reachable and no bundled SQLite database found.")
    eng = create_engine(f"sqlite:///{SQLITE_PATH.as_posix()}")
    event.listen(eng, "connect", _register_sqlite_functions)
    return eng


def is_sqlite() -> bool:
    return get_engine().dialect.name == "sqlite"


@st.cache_data(ttl=300, show_spinner=False)
def q(sql: str) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn)


def has_features() -> bool:
    try:
        if is_sqlite():
            return not q("SELECT name FROM sqlite_master WHERE type='table' "
                         "AND name='f1_result_features'").empty
        return int(q("""SELECT COUNT(*) n FROM information_schema.tables
            WHERE table_schema=DATABASE() AND table_name='f1_result_features'""").iloc[0, 0]) > 0
    except Exception:
        return False


inject_css()

st.sidebar.markdown("### F1 / ANALYTICS")
st.sidebar.caption("THE CHAMPIONSHIP INTELLIGENCE ROOM")
st.sidebar.markdown('<div style="height:1px;background:linear-gradient(90deg,#e10600 0 30%,#32323a 30%);margin:18px 0 12px"></div>', unsafe_allow_html=True)
_OPTS = ["Overview", "Drivers", "Constructors", "Race Explorer",
         "The Champion Thesis", "SQL Query Runner"]
_ICON = ["🏁", "👤", "🏎️", "🗓️", "🏆", "🖥️"]
_default = st.query_params.get("page")
PAGE = st.sidebar.radio("Navigate", _OPTS,
                        index=_OPTS.index(_default) if _default in _OPTS else 0,
                        format_func=lambda o: f"{_ICON[_OPTS.index(o)]}  {o}")

try:
    q("SELECT 1")
except Exception as e:
    st.error(f"Cannot reach MySQL. Is the server running and `f1_analytics` loaded?\n\n`{e}`")
    st.stop()

FEATURES = has_features()
if not FEATURES:
    st.sidebar.warning("Run Notebook 1 to build feature tables (Drivers / Thesis pages).")


# ----------------------------------------------------------------------------
def _champ_rows():
    return q("""SELECT r.year, ds.driverId FROM driver_standings ds JOIN races r ON r.raceId=ds.raceId
                WHERE ds.position=1 AND r.round=(SELECT MAX(round) FROM races r2 WHERE r2.year=r.year)""")


def page_overview():
    hero("OVERVIEW",
         "A complete analytics hub for Formula 1 — 75 years of racing history, performance and records.",
         "F1 SEASON 1950–2024")
    ch = _champ_rows()
    k = q("""SELECT (SELECT COUNT(*) FROM races) races,
                    (SELECT COUNT(DISTINCT year) FROM races) seasons,
                    (SELECT COUNT(*) FROM drivers) drivers,
                    (SELECT COUNT(*) FROM constructors) constructors,
                    (SELECT COUNT(DISTINCT nationality) FROM drivers) countries""").iloc[0]
    kpi_row([("Races", f"{k.races:,}", "🏁"), ("Drivers", f"{k.drivers:,}", "🪖"),
             ("Constructors", f"{k.constructors:,}", "👥"),
             ("Champions", f"{ch['driverId'].nunique()}", "🏆"),
             ("Countries", f"{k.countries}", "🌍"), ("Seasons", f"{k.seasons}", "📅")])

    left, right = st.columns([1, 1.05], gap="large")
    with left:
        st.markdown(panel_open("Races per decade"), unsafe_allow_html=True)
        dec = q("SELECT FLOOR(year/10)*10 decade, COUNT(*) races FROM races GROUP BY decade ORDER BY decade")
        dec["label"] = dec["decade"].astype(int).astype(str) + "s"
        fig = px.bar(dec, x="label", y="races", text="races")
        fig.update_traces(marker=dict(color=dec["races"], colorscale=[[0, "#7a0d0a"], [1, RED]],
                          line=dict(width=0)), textposition="outside")
        fig.update_layout(coloraxis_showscale=False, xaxis_title="", yaxis_title="RACES",
                          yaxis=dict(range=[0, dec["races"].max() * 1.16]))
        plot(fig, h=330)
        st.markdown("</div>", unsafe_allow_html=True)
    with right:
        st.markdown(panel_open("Top circuits by race count", "View all"), unsafe_allow_html=True)
        cir = q("""SELECT ci.name circuit, COUNT(*) races FROM races ra
                   JOIN circuits ci ON ci.circuitId=ra.circuitId
                   GROUP BY ci.circuitId ORDER BY races DESC LIMIT 10""")
        fig = px.bar(cir.iloc[::-1], x="races", y="circuit", orientation="h", text="races")
        fig.update_traces(marker_color=RED, textposition="outside")
        fig.update_layout(xaxis_title="RACES", yaxis_title="",
                          xaxis=dict(range=[0, cir["races"].max() * 1.12]))
        plot(fig, h=330)
        st.markdown("</div>", unsafe_allow_html=True)

    mleft, mright = st.columns([1, 1.15], gap="large")
    with mleft:
        st.markdown(panel_open("Champions by country"), unsafe_allow_html=True)
        cc = ch.merge(q("SELECT driverId, nationality FROM drivers"), on="driverId")
        cc["country"] = cc["nationality"].map(NAT_COUNTRY)
        geo = cc.dropna(subset=["country"]).groupby("country").size().reset_index(name="titles")
        fig = px.choropleth(geo, locations="country", locationmode="country names", color="titles",
                            color_continuous_scale=[[0, "#4a0f10"], [1, RED]])
        fig.update_geos(bgcolor="rgba(0,0,0,0)", showframe=False, showcoastlines=False,
                        showland=True, landcolor="#24242c", showocean=False, projection_type="natural earth")
        fig.update_layout(margin=dict(l=0, r=0, t=6, b=0), height=330,
                          paper_bgcolor="rgba(0,0,0,0)", coloraxis_showscale=True,
                          coloraxis_colorbar=dict(title="", thickness=8, len=.6))
        st.plotly_chart(fig, width='stretch')
        st.markdown("</div>", unsafe_allow_html=True)
    with mright:
        st.markdown(panel_open("Championship leaders"), unsafe_allow_html=True)
        t1, t2, t3 = st.tabs(["Most drivers' titles", "Most constructors' titles", "Most race wins"])
        with t1:
            dt = q("""SELECT driverId, driver, nationality, COUNT(*) titles,
                        GROUP_CONCAT(year) yrs FROM (
                        SELECT d.driverId, CONCAT(d.forename,' ',d.surname) driver,
                               d.nationality, r.year
                        FROM driver_standings ds JOIN races r ON r.raceId=ds.raceId
                        JOIN drivers d ON d.driverId=ds.driverId
                        WHERE ds.position=1
                          AND r.round=(SELECT MAX(round) FROM races r2 WHERE r2.year=r.year)
                        ORDER BY r.year) x
                        GROUP BY driverId, driver, nationality
                        ORDER BY titles DESC, driver LIMIT 10""")
            cards = "".join(lead_card(r.titles, int(r.driverId), r.driver, str(r.yrs).replace(",", ", "),
                            r.nationality, RED) for r in dt.itertuples())
            st.markdown(f'<div class="lead-scroll">{cards}</div>', unsafe_allow_html=True)
        with t2:
            ct = q("""SELECT name, COUNT(*) titles, GROUP_CONCAT(year) yrs FROM (
                        SELECT c.name, r.year
                        FROM constructor_standings cs JOIN races r ON r.raceId=cs.raceId
                        JOIN constructors c ON c.constructorId=cs.constructorId
                        WHERE cs.position=1
                          AND r.round=(SELECT MAX(round) FROM races r2 WHERE r2.year=r.year)
                        ORDER BY r.year) x
                        GROUP BY name ORDER BY titles DESC LIMIT 10""")
            cards = ""
            for r in ct.itertuples():
                tc = team_color(r.name)
                cards += (f'<div class="lead-card" style="background:linear-gradient(165deg,{tc} -10%,#0c0c11 78%)">'
                          f'<div class="big">{r.titles}</div><div class="info">'
                          f'<div class="nm">{team_badge(r.name, 18)}{esc(r.name)}</div>'
                          f'<div class="yr">{esc(str(r.yrs).replace(",", ", "))}</div></div></div>')
            st.markdown(f'<div class="lead-scroll">{cards}</div>', unsafe_allow_html=True)
        with t3:
            wt = q("""SELECT d.driverId, CONCAT(d.forename,' ',d.surname) driver, d.nationality,
                        SUM(re.positionOrder=1) wins FROM results re JOIN drivers d ON d.driverId=re.driverId
                        GROUP BY d.driverId ORDER BY wins DESC LIMIT 10""")
            cards = "".join(lead_card(int(r.wins), int(r.driverId), r.driver, "Career race wins",
                            r.nationality, RED) for r in wt.itertuples())
            st.markdown(f'<div class="lead-scroll">{cards}</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(panel_open("Latest race winners"), unsafe_allow_html=True)
    lw = q("""WITH fr AS (SELECT year, MAX(round) mr FROM races GROUP BY year)
              SELECT r.year, r.name gp, d.driverId, CONCAT(d.forename,' ',d.surname) driver,
                     d.nationality, c.name team
              FROM fr JOIN races r ON r.year=fr.year AND r.round=fr.mr
              JOIN results re ON re.raceId=r.raceId AND re.positionOrder=1
              JOIN drivers d ON d.driverId=re.driverId
              JOIN constructors c ON c.constructorId=re.constructorId
              ORDER BY r.year DESC LIMIT 12""")
    cards = "".join(win_card(int(r.year), r.gp, int(r.driverId), r.driver, r.team, r.nationality)
                    for r in lw.itertuples())
    st.markdown(f'<div class="win-scroll">{cards}</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def page_drivers():
    hero("DRIVERS", "Career records across the grid — filter, rank, and drill into any driver.", "DRIVER PADDOCK")
    if not FEATURES:
        st.info("Run Notebook 1 to build `f1_driver_features`."); return
    min_starts = st.slider("Minimum career starts", 1, 300, 20)
    df = q(f"""SELECT driverId, driver, nationality, starts, wins, podiums, poles, points,
                      ROUND(points / NULLIF(starts, 0), 2) points_per_start,
                      ROUND(100 * podiums / NULLIF(starts, 0), 1) podium_pct,
                      win_pct, dnf_rate, avg_grid, teams, first_season, last_season
               FROM f1_driver_features WHERE starts>={min_starts} ORDER BY wins DESC, points DESC""")
    if df.empty:
        st.info("No driver meets this minimum. Lower the minimum career starts to continue.")
        return

    champ_ids = set(q("""SELECT DISTINCT ds.driverId FROM driver_standings ds
                         JOIN races r ON r.raceId=ds.raceId
                         WHERE ds.position=1
                           AND r.round=(SELECT MAX(round) FROM races r2 WHERE r2.year=r.year)"""
                      )["driverId"].tolist())
    n_champs = int(df["driverId"].isin(champ_ids).sum())
    kpi_row([
        ("Total wins", f"{int(df['wins'].sum()):,}", "🏆"),
        ("Total podiums", f"{int(df['podiums'].sum()):,}", "🥂"),
        ("Total poles", f"{int(df['poles'].sum()):,}", "⚡"),
        ("Avg win rate", f"{100 * df['wins'].sum() / max(int(df['starts'].sum()), 1):.2f}%", "📈"),
        ("World champions", f"{n_champs}", "👑"),
    ])

    section_header("Driver database", f"{len(df):,} drivers with ≥ {min_starts} starts.")
    c_rows, c_sp, c_page = st.columns([1.1, 5.2, 1.7])
    per = c_rows.selectbox("Rows per page", [10, 25, 50, 100], index=1, key="drv_per")
    total = len(df)
    pages = max(1, (total + per - 1) // per)
    page = c_page.number_input("Page", 1, pages, 1, key="drv_page")
    start = (page - 1) * per
    view = df.iloc[start:start + per]
    st.markdown(driver_table(view, start + 1), unsafe_allow_html=True)
    st.markdown(f'<div style="text-align:right;color:#8f8e99;font-size:.82rem;margin-top:6px;'
                f'font-family:Barlow Condensed;letter-spacing:.05em">SHOWING '
                f'{start + 1:,}–{min(start + per, total):,} OF {total:,} · PAGE {page}/{pages}</div>',
                unsafe_allow_html=True)

    st.markdown('<div class="checker"></div>', unsafe_allow_html=True)
    st.markdown('<div class="dossier-title">Career deep-dive</div>', unsafe_allow_html=True)
    did = st.selectbox("Pick a driver", df["driverId"].tolist(),
                       format_func=lambda driver_id: df.loc[df["driverId"] == driver_id, "driver"].iloc[0])
    row = df[df["driverId"] == did].iloc[0]
    who = row["driver"]
    info = q(f"""SELECT d.number, d.code, d.nationality,
                 COALESCE((SELECT c.name FROM results re
                           JOIN races ra ON ra.raceId=re.raceId
                           JOIN constructors c ON c.constructorId=re.constructorId
                           WHERE re.driverId=d.driverId
                           ORDER BY ra.date DESC, ra.round DESC LIMIT 1), 'Independent') constructor
                 FROM drivers d WHERE d.driverId={did}""").iloc[0]
    tc = team_color(info["constructor"])
    season = q(f"""SELECT year, points, wins, dnfs FROM f1_season_driver
                   WHERE driverId={did} ORDER BY year""")
    favourite = q(f"""SELECT ci.circuitId, ci.name circuit, ci.country, COUNT(*) wins
                       FROM results re JOIN races ra ON ra.raceId=re.raceId
                       JOIN circuits ci ON ci.circuitId=ra.circuitId
                       WHERE re.driverId={did} AND re.positionOrder=1
                       GROUP BY ci.circuitId, ci.name, ci.country
                       ORDER BY wins DESC, MAX(ra.date) DESC LIMIT 1""")
    favourite = favourite.iloc[0] if not favourite.empty else None
    favourite_coords = circuit_coords().get(int(favourite["circuitId"])) if favourite is not None else None
    st.session_state.pop("sidebar_circuit", None)
    st.session_state["sidebar_driver_context"] = False
    if favourite is not None and favourite_coords is not None:
        st.session_state["sidebar_circuit"] = favourite.to_dict()
        st.session_state["sidebar_driver_context"] = True
    card, telemetry, chart = st.columns([1.45, .9, 2.55], gap="small")
    with card:
        st.markdown(driver_dossier(did, who, info, row), unsafe_allow_html=True)
    with telemetry:
        st.markdown(telemetry_stack(row, tc), unsafe_allow_html=True)
    with chart:
        st.markdown(f'<div class="chart-panel-label" style="--team:{RED}">Season points · {esc(who)}</div>',
                    unsafe_allow_html=True)
        fig = px.area(season, x="year", y="points", markers=True)
        fig.update_traces(line_color=RED, line_width=3, marker=dict(size=7, color=RED,
                          line=dict(color="#101014", width=1.5)),
                          fillcolor=with_alpha(RED, .20))
        fig.update_layout(height=245, margin=dict(l=16, r=16, t=4, b=16),
                          yaxis_title="POINTS", xaxis_title="")
        fig.update_xaxes(dtick=1, tickangle=0)
        plot(fig, h=245)


def constructor_table(df: pd.DataFrame) -> str:
    cols = [("entries", "Entries"), ("wins", "Wins"), ("podiums", "Podiums"),
            ("poles", "Poles"), ("points", "Points"), ("debut", "Debut"), ("last_season", "Last season")]
    head = "".join(f"<th>{c}</th>" for _, c in cols)
    rows = ""
    for rank, r in enumerate(df.itertuples(), 1):
        tc = team_color(r.constructor)
        cells = f'<td class="rank">{rank}</td>'
        cells += (f'<td>{team_badge(r.constructor, 30)}'
                  f'<span class="driver-name" style="color:{team_text_color(r.constructor)}">{esc(r.constructor)}</span></td>')
        for field, _ in cols:
            v = getattr(r, field)
            if field == "points":
                cells += f'<td style="color:{team_text_color(r.constructor)};font-weight:800">{float(v):,.1f}</td>'
            elif field in ("debut", "last_season"):
                cells += f"<td>{int(v)}</td>"
            else:
                cells += f"<td>{int(v):,}</td>"
        cls = ' class="leader"' if rank == 1 else ""
        rows += f"<tr{cls}>{cells}</tr>"
    return (f'<div class="race-table-wrap"><table class="race-table" style="min-width:900px">'
            f'<thead><tr><th>Rank</th><th>Constructor</th>{head}</tr></thead><tbody>{rows}</tbody></table></div>')


def page_constructors():
    hero("CONSTRUCTORS", "Team dynasties — race wins and championships in team colours.", "CONSTRUCTORS")
    section_header("Team performance", "The teams that turned machinery into race-winning eras.")
    wins = q("""SELECT c.name, SUM(re.positionOrder=1) wins FROM results re
                JOIN constructors c ON c.constructorId=re.constructorId
                GROUP BY c.constructorId HAVING wins>0 ORDER BY wins DESC LIMIT 12""")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(panel_open("Race wins by team", "Total wins"), unsafe_allow_html=True)
        fig = px.bar(wins.iloc[::-1], x="wins", y="name", orientation="h", text="wins")
        fig.update_traces(marker_color=[team_color(n) for n in wins.iloc[::-1]["name"]],
                          textposition="outside")
        fig.update_layout(yaxis_title="", xaxis_title="WINS",
                          xaxis=dict(range=[0, wins["wins"].max() * 1.12]))
        plot(fig, h=430)
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown(panel_open("Constructors' titles", "Total titles"), unsafe_allow_html=True)
        titles = q("""SELECT c.name, COUNT(*) titles
                      FROM (SELECT year, MAX(round) mr FROM races GROUP BY year) fr
                      JOIN races r ON r.year=fr.year AND r.round=fr.mr
                      JOIN constructor_standings cs ON cs.raceId=r.raceId AND cs.position=1
                      JOIN constructors c ON c.constructorId=cs.constructorId
                      GROUP BY c.constructorId ORDER BY titles DESC LIMIT 12""")
        fig = px.bar(titles.iloc[::-1], x="titles", y="name", orientation="h", text="titles")
        fig.update_traces(marker_color=[team_color(n) for n in titles.iloc[::-1]["name"]],
                          textposition="outside")
        fig.update_layout(yaxis_title="", xaxis_title="TITLES",
                          xaxis=dict(range=[0, titles["titles"].max() * 1.14]))
        plot(fig, h=430)
        st.markdown("</div>", unsafe_allow_html=True)
    section_header("Constructor register", "Race entries, podiums, poles, points and active eras — all in one view.")
    teams = q("""SELECT c.name constructor, COUNT(DISTINCT re.raceId) entries,
                      SUM(re.positionOrder=1) wins, SUM(re.positionOrder<=3) podiums,
                      SUM(re.grid=1) poles, ROUND(SUM(re.points),1) points,
                      MIN(ra.year) debut, MAX(ra.year) last_season
                   FROM results re JOIN constructors c ON c.constructorId=re.constructorId
                   JOIN races ra ON ra.raceId=re.raceId
                   GROUP BY c.constructorId, c.name ORDER BY wins DESC, points DESC LIMIT 30""")
    st.markdown(constructor_table(teams), unsafe_allow_html=True)


def status_chip(status: str) -> str:
    s = str(status)
    if s == "Finished":
        cls = "fin"
    elif "Lap" in s:
        cls = "lap"
    else:
        cls = "out"
    return f'<span class="status-chip {cls}">{esc(s)}</span>'


def results_table(res: pd.DataFrame) -> str:
    head = ("<tr><th>Pos</th><th>Driver</th><th>Constructor</th><th>Grid</th>"
            "<th>Laps</th><th>Points</th><th>Fastest lap</th><th>Status</th></tr>")
    rows = ""
    for i, r in enumerate(res.itertuples(), 1):
        pos = str(r.pos)
        dnf = not pos.isdigit()
        pcls = f"pos-{pos}" if pos in ("1", "2", "3") else ""
        rcls = f"p{pos}" if pos in ("1", "2", "3") else ("dnf" if dnf else "")
        pts = 0 if pd.isna(r.points) else float(r.points)
        laps = "—" if pd.isna(r.laps) else f"{int(r.laps)}"
        fl = "—" if (pd.isna(r.fastest_lap) or not str(r.fastest_lap).strip()) else esc(r.fastest_lap)
        tc = team_color(r.constructor)
        rows += (f'<tr class="{rcls}">'
                 f'<td class="rank {pcls}">{esc(pos)}</td>'
                 f'<td>{flag_html(r.nationality)}<span class="driver-name">{esc(r.driver)}</span></td>'
                 f'<td>{team_badge(r.constructor, 26)}<span style="color:{team_text_color(r.constructor)};font-weight:700">{esc(r.constructor)}</span></td>'
                 f'<td>{display_grid(r.grid)}</td><td>{laps}</td>'
                 f'<td class="pts{" zero" if pts == 0 else ""}">{pts:g}</td>'
                 f'<td>{fl}</td><td>{status_chip(r.status)}</td></tr>')
    return (f'<div class="race-table-wrap" style="max-height:460px"><table class="race-table" '
            f'style="min-width:860px"><thead>{head}</thead><tbody>{rows}</tbody></table></div>')


def page_race_explorer():
    hero("RACE EXPLORER", "Pick any Grand Prix in history and replay the podium.", "RACE CONTROL")
    c1, c2 = st.columns(2)
    years = q("SELECT DISTINCT year FROM races ORDER BY year DESC")["year"].tolist()
    yr = c1.selectbox("Season", years)
    races = q(f"SELECT raceId, round, name FROM races WHERE year={yr} ORDER BY round")
    label = c2.selectbox("Grand Prix", races["name"].tolist())
    rid = int(races[races["name"] == label]["raceId"].iloc[0])

    info = q(f"""SELECT ra.name race, ra.round, ra.date,
                    (SELECT MAX(round) FROM races WHERE year={yr}) total_rounds,
                    ci.circuitId, ci.name circuit, ci.location, ci.country
                 FROM races ra JOIN circuits ci ON ci.circuitId=ra.circuitId
                 WHERE ra.raceId={rid}""").iloc[0]
    res = q(f"""SELECT re.driverId, re.positionText pos, CONCAT(d.forename,' ',d.surname) driver,
                       d.nationality, c.name constructor, re.grid, re.points, re.laps,
                       re.fastestLapTime fastest_lap, s.status
                FROM results re JOIN drivers d ON d.driverId=re.driverId
                JOIN constructors c ON c.constructorId=re.constructorId
                JOIN status s ON s.statusId=re.statusId
                WHERE re.raceId={rid} ORDER BY re.positionOrder""")

    _d = fmt_date(info["date"])
    date_txt = f'<span class="dot">•</span>{_d}' if _d else ""
    coords = circuit_coords().get(int(info["circuitId"]))
    rows = res.head(3).to_dict("records")

    left, right = st.columns([1, 1.1], gap="large")
    with left:
        map_html = (circuit_map_svg(coords) if coords else
                    '<div style="height:300px;display:flex;align-items:center;justify-content:center;'
                    'color:#666;font-family:Barlow Condensed;font-style:italic;font-weight:700">◇ layout map not available for this circuit</div>')
        length_stat = ""
        st.markdown(f'''<div class="mapcard">
            <div class="cn">🏁 {esc(info["circuit"])}</div>
            <div class="cm">📍 {esc(info["location"])}, {flag_country_html(info["country"])} {esc(info["country"])}
              <span class="dot">•</span>Round {info["round"]} of {info["total_rounds"]}
              <span class="dot">•</span>{yr}{date_txt}</div>
            {map_html}
            <div><span class="stat">Winner<br><b>{esc(rows[0]['driver']) if rows else '—'}</b></span>
                 <span class="stat">From grid<br><b>{display_grid(rows[0]['grid']) if rows else '—'}</b></span>
                 <span class="stat">Entries<br><b>{len(res)}</b></span></div>
        </div>{length_stat}''', unsafe_allow_html=True)
    with right:
        st.markdown('<div style="font-family:Barlow Condensed;font-style:italic;font-weight:800;color:#fff;'
                    'font-size:1.05rem;margin:2px 0 10px">🏆 PODIUM</div>', unsafe_allow_html=True)
        if len(rows) >= 3:
            ids = [int(x) for x in res.head(3)["driverId"].tolist()]
            st.markdown(podium(rows, ids), unsafe_allow_html=True)
        else:
            st.info("Not enough classified finishers for a podium.")

    section_header("Full classification", f"{len(res)} entries · {esc(label)} {yr}")
    st.markdown(results_table(res), unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _champ_view():
    """Create the champion view plus helper indexes once per app process."""
    if is_sqlite():
        with get_engine().begin() as conn:
            conn.execute(text("DROP VIEW IF EXISTS v_season_champion"))
            conn.execute(text("""CREATE VIEW v_season_champion AS
                SELECT r.year, ds.driverId FROM driver_standings ds JOIN races r ON r.raceId=ds.raceId
                WHERE ds.position=1
                  AND r.round=(SELECT MAX(round) FROM races r2 WHERE r2.year=r.year)"""))
        return True
    with get_engine().begin() as conn:
        conn.execute(text("""CREATE OR REPLACE VIEW v_season_champion AS
            SELECT r.year, ds.driverId FROM driver_standings ds JOIN races r ON r.raceId=ds.raceId
            WHERE ds.position=1 AND r.round=(SELECT MAX(round) FROM races r2 WHERE r2.year=r.year)"""))
        for tbl, idx, cols in (("f1_result_features", "idx_frf_year_driver", "year, driverId"),
                               ("f1_season_driver", "idx_fsd_year_driver", "year, driverId")):
            exists = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() "
                "AND table_name=:t AND index_name=:i"), {"t": tbl, "i": idx}).scalar()
            if not exists:
                try:
                    conn.execute(text(f"CREATE INDEX {idx} ON {tbl}({cols})"))
                except Exception:
                    pass
    return True


def page_thesis():
    hero("THE CHAMPION THESIS", "Five questions, five charts — what separates champions from the grid.",
         "DATA VERDICT")
    st.caption("Champion = P1 in the drivers' standings at a season's final round.")
    if not FEATURES:
        st.info("Run Notebook 1 to build the feature tables."); return
    _champ_view()
    mech = q("""SELECT
        ROUND(100*AVG(CASE WHEN sc.driverId IS NOT NULL THEN f.status_category='Mechanical' END),1) champ_mech,
        ROUND(100*AVG(CASE WHEN sc.driverId IS NULL THEN f.status_category='Mechanical' END),1) field_mech,
        COUNT(DISTINCT sc.driverId) champs
      FROM f1_result_features f
      LEFT JOIN v_season_champion sc ON sc.year=f.year AND sc.driverId=f.driverId""").iloc[0]
    cw = q("""SELECT ROUND(AVG(sd.wins),2) w FROM f1_season_driver sd
              JOIN v_season_champion sc ON sc.year=sd.year AND sc.driverId=sd.driverId""").iloc[0].w
    kpi_row([("Champion wins / season", f"{cw}", "🏆"),
             ("Champion mech DNF", f"{mech.champ_mech}%", "🛠️"),
             ("Field mech DNF", f"{mech.field_mech}%", "⚠️"),
             ("World champions", f"{int(mech.champs)}", "👑")])

    t1, t2, t3 = st.tabs(["⚔️ Consistency & Reliability", "🏎️ Car vs Driver · Age", "⏱️ Qualifying"])
    with t1:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(panel_open("Consistency vs peak", "Wins & DNFs"), unsafe_allow_html=True)
            comp = q("""SELECT CASE WHEN sc.driverId IS NOT NULL THEN 'Champion' ELSE 'Field' END grp,
                          ROUND(AVG(sd.wins),2) avg_wins, ROUND(AVG(sd.dnfs),2) avg_dnfs
                        FROM f1_season_driver sd LEFT JOIN v_season_champion sc
                          ON sc.year=sd.year AND sc.driverId=sd.driverId GROUP BY grp""")
            m = comp.melt("grp", var_name="metric", value_name="avg")
            fig = px.bar(m, x="metric", y="avg", color="grp", barmode="group",
                         color_discrete_map={"Champion": RED, "Field": "#5a5a66"})
            plot(fig)
        with c2:
            st.markdown(panel_open("Reliability edge", "Mechanical DNF %"), unsafe_allow_html=True)
            rel = q("""SELECT CASE WHEN sc.driverId IS NOT NULL THEN 'Champion' ELSE 'Field' END grp,
                         ROUND(100*AVG(f.status_category='Mechanical'),2) mech
                       FROM f1_result_features f LEFT JOIN v_season_champion sc
                         ON sc.year=f.year AND sc.driverId=f.driverId GROUP BY grp""")
            fig = px.bar(rel, x="grp", y="mech", text="mech",
                         color="grp", color_discrete_map={"Champion": RED, "Field": "#5a5a66"})
            fig.update_traces(texttemplate="%{text}%", textposition="outside")
            fig.update_layout(showlegend=False, yaxis_title="mechanical DNF %")
            plot(fig)
    with t2:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(panel_open("Car vs driver", "Champions by team count"), unsafe_allow_html=True)
            mt = q("""SELECT SUM(t>1) multi, SUM(t=1) single FROM (
                        SELECT sc.driverId, COUNT(DISTINCT CASE WHEN f.is_win=1 THEN f.constructorId END) t
                        FROM (SELECT DISTINCT driverId FROM v_season_champion) sc
                        JOIN f1_result_features f ON f.driverId=sc.driverId GROUP BY sc.driverId) x""").iloc[0]
            fig = go.Figure(go.Pie(values=[int(mt.multi), int(mt.single)],
                            labels=["Multiple teams", "Single team"], hole=.55,
                            marker=dict(colors=[RED, "#3a3a44"])))
            fig.update_traces(textinfo="label+percent")
            plot(fig)
        with c2:
            st.markdown(panel_open("Age curve", "Points per race"), unsafe_allow_html=True)
            age = q("""SELECT driver_age age, ROUND(AVG(points),3) ppr, COUNT(*) n
                       FROM f1_result_features WHERE driver_age IS NOT NULL
                       GROUP BY age HAVING n>=100 ORDER BY age""")
            fig = px.line(age, x="age", y="ppr", markers=True)
            fig.update_traces(line_color=RED)
            plot(fig)
    with t3:
        st.markdown(panel_open("Qualifying vs points", "Driver-seasons"), unsafe_allow_html=True)
        ds = q("""SELECT avg_grid, points FROM f1_season_driver WHERE avg_grid IS NOT NULL AND points>0""")
        r = ds["avg_grid"].corr(ds["points"])
        st.caption(f"Pearson r = {r:.2f} — lower grid number = better start")
        fig = px.scatter(ds, x="avg_grid", y="points", opacity=.4)
        fig.update_traces(marker=dict(color=RED, size=6))
        m, b = np.polyfit(ds["avg_grid"], ds["points"], 1)
        xs = np.array([ds["avg_grid"].min(), ds["avg_grid"].max()])
        fig.add_trace(go.Scatter(x=xs, y=m*xs + b, mode="lines",
                                 line=dict(color="#ffffff", width=3, dash="dash"),
                                 name="trend"))
        fig.update_layout(showlegend=False)
        plot(fig, h=460)


RUNNER = {
    "2023 race winners": """SELECT ra.round, ra.name race,
        CONCAT(d.forename,' ',d.surname) winner, c.name constructor
      FROM results re JOIN races ra ON ra.raceId=re.raceId
      JOIN drivers d ON d.driverId=re.driverId JOIN constructors c ON c.constructorId=re.constructorId
      WHERE ra.year=2023 AND re.positionOrder=1 ORDER BY ra.round""",
    "Career wins: RANK vs DENSE_RANK": """WITH w AS (
        SELECT driverId, SUM(positionOrder=1) wins FROM results GROUP BY driverId)
      SELECT CONCAT(d.forename,' ',d.surname) driver, w.wins,
             RANK() OVER (ORDER BY w.wins DESC) rank_pos,
             DENSE_RANK() OVER (ORDER BY w.wins DESC) dense_rank_pos
      FROM w JOIN drivers d ON d.driverId=w.driverId WHERE w.wins>0 ORDER BY w.wins DESC LIMIT 20""",
    "Longest podium streak (gaps-and-islands)": """WITH seq AS (
        SELECT re.driverId, ROW_NUMBER() OVER (PARTITION BY re.driverId ORDER BY ra.date, ra.round) rn,
               (re.positionOrder<=3) is_podium FROM results re JOIN races ra ON ra.raceId=re.raceId),
      grp AS (SELECT driverId, is_podium,
               rn - ROW_NUMBER() OVER (PARTITION BY driverId, is_podium ORDER BY rn) island FROM seq)
      SELECT CONCAT(d.forename,' ',d.surname) driver, COUNT(*) podium_streak
      FROM grp JOIN drivers d ON d.driverId=grp.driverId WHERE is_podium=1
      GROUP BY grp.driverId, grp.island ORDER BY podium_streak DESC LIMIT 10""",
    "Constructor title streaks": """WITH fr AS (SELECT year, MAX(round) mr FROM races GROUP BY year),
      champ AS (SELECT r.year, cs.constructorId FROM fr JOIN races r ON r.year=fr.year AND r.round=fr.mr
                JOIN constructor_standings cs ON cs.raceId=r.raceId AND cs.position=1),
      seq AS (SELECT year, constructorId, year-ROW_NUMBER() OVER (PARTITION BY constructorId ORDER BY year) island FROM champ)
      SELECT c.name, COUNT(*) consecutive_titles, MIN(year) from_y, MAX(year) to_y
      FROM seq JOIN constructors c ON c.constructorId=seq.constructorId
      GROUP BY seq.constructorId, seq.island ORDER BY consecutive_titles DESC LIMIT 10""",
    "Driver career summary (view)": "SELECT * FROM v_driver_career_summary ORDER BY wins DESC LIMIT 25",
}
_BLOCKED = ("insert", "update", "delete", "drop", "alter", "truncate",
            "create", "replace", "grant", "call", "set ")


TABLE_ORDER = ["seasons", "circuits", "drivers", "constructors", "status", "races",
               "qualifying", "results", "sprint_results", "lap_times", "pit_stops",
               "driver_standings", "constructor_standings", "constructor_results"]


def _sqlite_schema():
    """PRAGMA-based schema read for the bundled demo database."""
    rows, fk_map = [], {}
    names = q("SELECT name FROM sqlite_master WHERE type='table' "
              "AND name NOT LIKE 'f1!_%' ESCAPE '!' AND name NOT LIKE 'sqlite!_%' ESCAPE '!' "
              "ORDER BY name")["name"].tolist()
    for t in names:
        for c in q(f'PRAGMA table_info("{t}")').itertuples():
            rows.append({"tn": t, "cn": c.name, "ct": (c.type or "TEXT").upper(),
                         "ck": "PRI" if c.pk else ""})
    # The exported copy carries no declared constraints; use the documented model.
    pk_of = {"seasons": "year", "circuits": "circuitId", "drivers": "driverId",
             "constructors": "constructorId", "status": "statusId", "races": "raceId"}
    present = set(names)
    for child, parent in ERD_LINKS:
        if child in present and parent in pk_of:
            fk_map[(child, pk_of[parent])] = parent
    # primary keys are not preserved by the export either
    for t in present:
        pk = pk_of.get(t)
        if pk:
            for r in rows:
                if r["tn"] == t and r["cn"] == pk:
                    r["ck"] = "PRI"
    return pd.DataFrame(rows), fk_map


def schema_html() -> str:
    if is_sqlite():
        cols, fk_map = _sqlite_schema()
    else:
        cols = q("""SELECT TABLE_NAME tn, COLUMN_NAME cn, COLUMN_TYPE ct, COLUMN_KEY ck
                    FROM information_schema.columns WHERE table_schema=DATABASE()
                      AND TABLE_NAME NOT LIKE 'f1\\_%' AND TABLE_NAME NOT LIKE 'v\\_%'
                    ORDER BY TABLE_NAME, ORDINAL_POSITION""")
        fks = q("""SELECT TABLE_NAME tn, COLUMN_NAME cn, REFERENCED_TABLE_NAME rt
                   FROM information_schema.KEY_COLUMN_USAGE
                   WHERE table_schema=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL""")
        fk_map = {(r.tn, r.cn): r.rt for r in fks.itertuples()}
    order = {t: i for i, t in enumerate(TABLE_ORDER)}
    tables = sorted(cols["tn"].unique(), key=lambda t: order.get(t, 99))
    cards = ""
    for t in tables:
        sub = cols[cols["tn"] == t]
        rows = ""
        for r in sub.itertuples():
            badge = ""
            if r.ck == "PRI":
                badge = '<span class="badge pk">PK</span>'
            elif (t, r.cn) in fk_map:
                badge = f'<span class="badge fk">FK → {esc(fk_map[(t, r.cn)])}</span>'
            rows += (f'<div class="schema-row"><span class="schema-col">{esc(r.cn)}</span>'
                     f'<span class="schema-type">{esc(r.ct)}</span>{badge}</div>')
        cards += (f'<div class="schema-card"><div class="sc-head">'
                  f'<span class="sc-name">{esc(t)}</span>'
                  f'<span class="sc-count">{len(sub)} cols</span></div>{rows}</div>')
    return f'<div class="schema-grid">{cards}</div>'


ERD_POS = {
    "seasons": (20, 30), "circuits": (20, 150), "races": (300, 70),
    "drivers": (580, 20), "constructors": (860, 20), "status": (860, 175),
    "qualifying": (20, 330), "results": (300, 300), "sprint_results": (580, 300),
    "lap_times": (20, 500), "pit_stops": (300, 505), "driver_standings": (580, 500),
    "constructor_standings": (860, 330), "constructor_results": (860, 500),
}
ERD_COLS = {
    "seasons": ["year*"], "circuits": ["circuitId*", "name", "country"],
    "drivers": ["driverId*", "forename", "surname", "dob"],
    "constructors": ["constructorId*", "name"], "status": ["statusId*", "status"],
    "races": ["raceId*", "year^", "circuitId^", "round", "date"],
    "qualifying": ["qualifyId*", "raceId^", "driverId^", "constructorId^"],
    "results": ["resultId*", "raceId^", "driverId^", "constructorId^", "statusId^", "points"],
    "sprint_results": ["resultId*", "raceId^", "driverId^", "constructorId^", "statusId^"],
    "lap_times": ["raceId^", "driverId^", "lap", "milliseconds"],
    "pit_stops": ["raceId^", "driverId^", "stop", "duration"],
    "driver_standings": ["driverStandingsId*", "raceId^", "driverId^", "points"],
    "constructor_standings": ["constructorStandingsId*", "raceId^", "constructorId^"],
    "constructor_results": ["constructorResultsId*", "raceId^", "constructorId^"],
}
ERD_LINKS = [("races", "seasons"), ("races", "circuits"),
             ("qualifying", "races"), ("qualifying", "drivers"), ("qualifying", "constructors"),
             ("lap_times", "races"), ("lap_times", "drivers"),
             ("pit_stops", "races"), ("pit_stops", "drivers"),
             ("results", "races"), ("results", "drivers"), ("results", "constructors"), ("results", "status"),
             ("sprint_results", "races"), ("sprint_results", "drivers"),
             ("sprint_results", "constructors"), ("sprint_results", "status"),
             ("driver_standings", "races"), ("driver_standings", "drivers"),
             ("constructor_standings", "races"), ("constructor_standings", "constructors"),
             ("constructor_results", "races"), ("constructor_results", "constructors")]


def schema_erd_svg(w=1180, h=690):
    BW, RH, HH = 218, 19, 26
    box = {t: (x, y, BW, HH + RH * len(ERD_COLS[t]) + 6) for t, (x, y) in ERD_POS.items()}

    def edge(t, tx, ty):
        x, y, bw, bh = box[t]
        cx, cy = x + bw / 2, y + bh / 2
        dx, dy = tx - cx, ty - cy
        if not dx and not dy:
            return cx, cy
        sx = bw / 2 / abs(dx) if dx else 1e9
        sy = bh / 2 / abs(dy) if dy else 1e9
        s = min(sx, sy)
        return cx + dx * s, cy + dy * s

    lines = ""
    for child, parent in ERD_LINKS:
        cx1, cy1, bw1, bh1 = box[child]
        cx2, cy2, bw2, bh2 = box[parent]
        c1 = (cx1 + bw1 / 2, cy1 + bh1 / 2)
        c2 = (cx2 + bw2 / 2, cy2 + bh2 / 2)
        x1, y1 = edge(child, *c2)
        x2, y2 = edge(parent, *c1)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - 16
        lines += (f'<path d="M {x1:.0f} {y1:.0f} Q {mx:.0f} {my:.0f} {x2:.0f} {y2:.0f}" '
                  f'fill="none" stroke="url(#lg)" stroke-width="1.5" opacity=".75"/>'
                  f'<circle cx="{x2:.0f}" cy="{y2:.0f}" r="3.4" fill="{RED}"/>'
                  f'<rect x="{x1 - 2.6:.0f}" y="{y1 - 2.6:.0f}" width="5.2" height="5.2" '
                  f'fill="#7db4ff" transform="rotate(45 {x1:.0f} {y1:.0f})"/>')

    boxes = ""
    for t, (x, y, bw, bh) in box.items():
        rows = ""
        yy = y + HH
        for col in ERD_COLS[t]:
            pk, fk = col.endswith("*"), col.endswith("^")
            label = col.rstrip("*^")
            colr = "#ff8a86" if pk else ("#7db4ff" if fk else "#c9c8d0")
            tag = "PK" if pk else ("FK" if fk else "")
            rows += (f'<text x="{x + 11}" y="{yy + 13}" fill="{colr}" font-size="11.5" '
                     f'font-family="Rajdhani,sans-serif" font-weight="{700 if (pk or fk) else 500}">{esc(label)}</text>')
            if tag:
                rows += (f'<text x="{x + bw - 11}" y="{yy + 13}" fill="{colr}" font-size="9" text-anchor="end" '
                         f'font-family="Barlow Condensed,sans-serif" font-weight="800" opacity=".85">{tag}</text>')
            yy += RH
        boxes += (f'<g><rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="5" fill="#15151b" '
                  f'stroke="#33333e" stroke-width="1"/>'
                  f'<path d="M {x} {y + 5} a5 5 0 0 1 5 -5 h{bw - 10} a5 5 0 0 1 5 5 v{HH - 5} h-{bw} z" fill="url(#hg)"/>'
                  f'<text x="{x + 11}" y="{y + 18}" fill="#fff" font-size="13" font-style="italic" '
                  f'font-family="Barlow Condensed,sans-serif" font-weight="900" letter-spacing=".5">{esc(t)}</text>'
                  f'{rows}</g>')

    return (f'<div class="erd-wrap"><svg viewBox="0 0 {w} {h}" width="100%" style="min-width:1050px" '
            f'xmlns="http://www.w3.org/2000/svg"><defs>'
            f'<linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="#7db4ff" stop-opacity=".85"/>'
            f'<stop offset="1" stop-color="{RED}" stop-opacity=".9"/></linearGradient>'
            f'<linearGradient id="hg" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="rgba(225,6,0,.55)"/>'
            f'<stop offset="1" stop-color="#20202a"/></linearGradient></defs>'
            f'{lines}{boxes}</svg></div>')


def page_runner():
    hero("SQL QUERY RUNNER", "Run the showcase queries live, then explore the full database schema.", "PIT WALL")
    section_header("Showcase queries", "Every tier of the analysis, ready to run against the live database.")
    choice = st.selectbox("Sample query", list(RUNNER.keys()))
    sql = RUNNER[choice]
    st.code(sql, language="sql")
    if st.button("▶  Run sample", type="primary"):
        try:
            st.dataframe(q(sql), width='stretch', height=400, hide_index=True)
        except Exception as e:
            st.error(str(e))

    section_header("Database schema", "14 tables · 23 foreign keys — ◆ many side, ● one side.")
    if is_sqlite():
        st.caption("Running on the bundled read-only SQLite build. It mirrors the MySQL schema "
                   "except for `lap_times` (589k rows), which is omitted to keep the deployment "
                   "light; the full MySQL build created by `01_schema.sql` includes it.")
    st.markdown(schema_erd_svg(), unsafe_allow_html=True)
    st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
    st.markdown(panel_open("Table detail", "All columns"), unsafe_allow_html=True)
    st.markdown(schema_html(), unsafe_allow_html=True)


{"Overview": page_overview, "Drivers": page_drivers, "Constructors": page_constructors,
 "Race Explorer": page_race_explorer, "The Champion Thesis": page_thesis,
 "SQL Query Runner": page_runner}[PAGE]()

st.sidebar.divider()
side_circuit = st.session_state.get("sidebar_circuit")
if side_circuit is None:
    side = q("""SELECT ci.circuitId, ci.name circuit, ci.country, COUNT(*) wins
                FROM results re JOIN races ra ON ra.raceId=re.raceId
                JOIN circuits ci ON ci.circuitId=ra.circuitId
                WHERE re.positionOrder=1 GROUP BY ci.circuitId, ci.name, ci.country
                ORDER BY wins DESC LIMIT 1""")
    side_circuit = side.iloc[0].to_dict() if not side.empty else None
if side_circuit is not None:
    side_coords = circuit_coords().get(int(side_circuit["circuitId"]))
    if side_coords:
        context = "DRIVER'S MOST SUCCESSFUL CIRCUIT" if st.session_state.get("sidebar_driver_context") else "MOST SUCCESSFUL CIRCUIT"
        st.sidebar.markdown(f'''<div class="sidebar-track">
            <div class="st-label">{context}</div>
            <div class="st-name">{flag_country_html(side_circuit['country'])}{esc(side_circuit['circuit'])}</div>
            <div class="st-meta">{int(side_circuit['wins'])} RACE WINS</div>
            {circuit_map_svg(side_coords, w=182, h=104, pad=11)}</div>''', unsafe_allow_html=True)
st.sidebar.markdown("<span style='color:#e10600'>●</span> <span style='font-family:Barlow Condensed;font-weight:700;letter-spacing:.08em;color:#d7d7dd'>RACE CONTROL ONLINE</span>", unsafe_allow_html=True)
st.sidebar.caption(f"DATASET 1950–2024 · {'SQLITE (DEMO)' if is_sqlite() else 'MYSQL 8.0'} · STREAMLIT")
