"""
TripleM – moodle_scraper.py  V6
================================
מדיניות: ייבא הכל, קטלג לפי מספר קורס.
אין whitelist. אין סינון בצד השרת חוץ מ-dedup.
המשתמש מסנן דינמית מה שהוא לא רוצה לראות.
"""

import re, logging, requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  מפת קורסים: מספר → (שם, צבע)
# ══════════════════════════════════════════════════════════════
COURSE_ID_MAP = {
    '00840630': ('שרטוט הנדסי ממוחשב',                '#16a085'),
    '01040131': ('משוואות דיפרונציאליות רגילות/ח',     '#2980b9'),
    '00840506': ('מכניקת המוצקים',                     '#e74c3c'),
    '01140052': ('פיסיקה 2',                           '#9b59b6'),
    '01040043': ('חשבון דיפרונציאלי ואינטגרלי 2מ1',   '#27ae60'),
    '03140200': ('מבוא להנדסת חומרים לתעופה וחלל',    '#e67e22'),
}

# גיבוי: מילות מפתח לזיהוי קורס כשאין מספר ב-UID
COURSE_KEYWORDS = [
    (r'מוצקים|solid\s*mech',                           'מכניקת המוצקים'),
    (r'שרטוט|engineering\s*draw',                      'שרטוט הנדסי ממוחשב'),
    (r'מד"ר|משוואות\s*דיפ|\bode\b',                   'משוואות דיפרונציאליות רגילות/ח'),
    (r'פיסיקה|physics|גאוס|קיבול|מוליכים|אמפר|חשמל', 'פיסיקה 2'),
    (r'חשבון\s*דיפ|חדו"?א|אינטגר|calculus',           'חשבון דיפרונציאלי ואינטגרלי 2מ1'),
    (r'חומרים|materials|גביש|תעופה|aerospace',         'מבוא להנדסת חומרים לתעופה וחלל'),
]

_PALETTE = ['#e74c3c','#e67e22','#f39c12','#27ae60','#2980b9','#9b59b6','#16a085']

def get_course_color(name: str) -> str:
    for _, (n, color) in COURSE_ID_MAP.items():
        if n == name:
            return color
    return _PALETTE[hash(name or '') % len(_PALETTE)]


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════
def _ical_date(raw: str):
    if not raw:
        return None
    s = raw.strip()
    # strip TZID prefix like "TZID=Asia/Jerusalem:"
    if ':' in s and not s[:8].isdigit():
        s = s.split(':', 1)[-1]
    try:
        if s.endswith('Z'):
            dt = datetime.strptime(s, '%Y%m%dT%H%M%SZ')
            dt = dt.replace(tzinfo=timezone.utc) + timedelta(hours=3)
            return dt.strftime('%Y-%m-%d %H:%M')
        if 'T' in s:
            return datetime.strptime(s[:15], '%Y%m%dT%H%M%S').strftime('%Y-%m-%d %H:%M')
        if len(s) == 8:
            return datetime.strptime(s, '%Y%m%d').strftime('%Y-%m-%d 23:59')
    except Exception:
        pass
    return None

def _unesc(s: str) -> str:
    return (s.replace('\\n', ' ').replace('\\,', ',')
             .replace('\\;', ';').replace('\\\\', '\\').strip())

def _find_course(uid: str, categories: str, location: str, summary: str, desc: str):
    """
    מחזיר (שם_קורס, צבע).
    מספר הקורס נמצא ב-CATEGORIES (לדוגמה: "01040043.201")
    """
    # 1. CATEGORIES — המיקום האמיתי ב-Moodle הטכניון
    #    פורמט: "01040043.201" או "01040043"
    for num in re.findall(r'\d{8}', categories):
        if num in COURSE_ID_MAP:
            name, color = COURSE_ID_MAP[num]
            return name, color

    # 2. UID (גיבוי)
    for num in re.findall(r'\d{8}', uid):
        if num in COURSE_ID_MAP:
            name, color = COURSE_ID_MAP[num]
            return name, color

    # 3. LOCATION (גיבוי)
    for num in re.findall(r'\d{8}', location):
        if num in COURSE_ID_MAP:
            name, color = COURSE_ID_MAP[num]
            return name, color

    # 4. מילות מפתח בכותרת/תיאור (גיבוי אחרון)
    combined = f'{summary} {desc} {location}'.lower()
    for pattern, name in COURSE_KEYWORDS:
        if re.search(pattern, combined, re.IGNORECASE):
            return name, get_course_color(name)

    return 'לא ידוע', '#4f8ef7'

def _clean_title(raw: str) -> str:
    t = raw.strip()
    # Remove Moodle event prefixes
    t = re.sub(r'^נפתח\s+ב\s+', '', t)
    t = re.sub(r'^תאריך\s+הגשה\s+', '', t)
    t = re.sub(r'^יש\s+להגיש\s+את\s+', '', t)
    # Remove date suffix like "- להגשה עד 11.6.26"
    t = re.sub(r'\s*[-–]\s*להגשה\s+עד.*$', '', t)
    t = re.sub(r'\s*[-–]\s*להגשה.*$', '', t)
    t = t.strip("'\"'׳")
    return re.sub(r'\s{2,}', ' ', t).strip()


# ══════════════════════════════════════════════════════════════
#  MAIN PARSER — מייבא הכל, רק dedup
# ══════════════════════════════════════════════════════════════
def parse_ical(ical_text: str) -> list:
    """
    מייבא הכל, קטלג לפי CATEGORIES, dedup חכם:
    אם יש שתי רשומות עם אותו title+date — מעדיף את זו עם CATEGORIES (קורס מזוהה).
    """
    # שלב 1: אסוף את כל האירועים
    raw_events = []

    text = ical_text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n[ \t]', '', text)

    for block in re.split(r'BEGIN:VEVENT', text)[1:]:

        def f(name):
            m = re.search(
                rf'^{re.escape(name)}(?:;[^:\n]*)?:(.*)$',
                block, re.MULTILINE | re.IGNORECASE
            )
            return m.group(1).strip() if m else ''

        uid        = f('UID')
        summary    = _unesc(f('SUMMARY'))
        dtend      = f('DTEND')
        dtstart    = f('DTSTART')
        location   = _unesc(f('LOCATION'))
        desc       = _unesc(f('DESCRIPTION'))
        url_val    = _unesc(f('URL'))
        categories = f('CATEGORIES')

        if not summary:
            continue

        title    = _clean_title(summary)
        due_date = _ical_date(dtend) or _ical_date(dtstart)
        course, color = _find_course(uid, categories, location, summary, desc)

        uid_clean = re.sub(r'[^a-zA-Z0-9_-]', '_', uid) if uid else ''
        moodle_id = (f'ical_{uid_clean}' if uid_clean
                     else f'ical_{abs(hash(title + str(due_date)))}')

        # Extract all 8-digit course IDs from CATEGORIES (e.g. "00940411.201" → "00940411")
        # This is stored in DB as course_num and used by apply_category for dynamic course matching
        cat_nums = re.findall(r'\d{8}', categories)
        course_num = ','.join(cat_nums)   # e.g. "00940411" or "00940411,01040043"

        raw_events.append({
            'moodle_id':    moodle_id,
            'title':        title,
            'course':       course,
            'course_color': color,
            'due_date':     due_date,
            'url':          url_val,
            'course_num':   course_num,   # ← NEW: for dynamic category matching
            'has_category': bool(cat_nums),
        })

    # שלב 2: dedup חכם — key = title+date, מעדיף רשומה עם CATEGORIES
    best = {}   # key → event
    for ev in raw_events:
        key = f'{ev["title"].lower()}|{ev["due_date"]}'
        if key not in best:
            best[key] = ev
        else:
            existing = best[key]
            # החלף רק אם הגרסה החדשה יש לה CATEGORIES והישנה לא
            if ev['has_category'] and not existing['has_category']:
                best[key] = ev
            # אם שתיהן ידועות — שמור את עם הקורס הטוב יותר (לא "לא ידוע")
            elif ev['course'] != 'לא ידוע' and existing['course'] == 'לא ידוע':
                best[key] = ev

    accepted = list(best.values())
    n_dup    = len(raw_events) - len(accepted)

    logger.info(f'parse_ical: {len(accepted)} imported, {n_dup} duplicates removed')
    return accepted


# ══════════════════════════════════════════════════════════════
#  LIVE SYNCER
# ══════════════════════════════════════════════════════════════
class ICalSyncer:
    def __init__(self, url: str):
        self.url = url.strip()

    def fetch(self) -> list:
        if not self.url:
            raise ValueError('לא הוגדר קישור iCal')
        resp = requests.get(
            self.url,
            headers={'User-Agent': 'TripleM/6.0'},
            timeout=25,
            allow_redirects=True,
        )
        resp.raise_for_status()
        if 'BEGIN:VCALENDAR' not in resp.text:
            raise ValueError('הקישור אינו מחזיר לוח שנה תקין.')
        return parse_ical(resp.text)


# ══════════════════════════════════════════════════════════════
#  CLI DEBUG — מציג את ה-UIDs האמיתיים
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    if len(sys.argv) < 2:
        print('Usage: python moodle_scraper.py <ical_url>'); sys.exit(1)

    url   = sys.argv[1]
    tasks = ICalSyncer(url).fetch()

    # Also show raw UIDs for debugging
    import requests as req
    raw = req.get(url, headers={'User-Agent':'debug'}, timeout=20).text
    raw = raw.replace('\r\n','\n'); raw = re.sub(r'\n[ \t]','',raw)
    blocks = re.split(r'BEGIN:VEVENT', raw)[1:10]
    print('\n=== RAW UIDs (first 10 events) ===')
    for b in blocks:
        uid = re.search(r'^UID:(.*)$', b, re.M)
        sm  = re.search(r'^SUMMARY:(.*)$', b, re.M)
        if uid and sm:
            nums = re.findall(r'\d{8}', uid.group(1))
            print(f'  SUMMARY: {sm.group(1).strip()[:55]}')
            print(f'  UID:     {uid.group(1).strip()[:80]}')
            print(f'  8-digit#: {nums}')
            print()

    print(f'\n=== RESULT: {len(tasks)} events imported ===\n')
    unknown = [t for t in tasks if t['course'] == 'לא ידוע']
    known   = [t for t in tasks if t['course'] != 'לא ידוע']
    for t in sorted(known, key=lambda x: x.get('due_date') or '9'):
        dd = t.get('due_date') or '—'
        print(f'  [{t["course"]:44}]  {dd:17}  {t["title"]}')
    if unknown:
        print(f'\n  ⚠️  {len(unknown)} events with unknown course:')
        for t in unknown[:10]:
            print(f'    - {t["title"]}')
