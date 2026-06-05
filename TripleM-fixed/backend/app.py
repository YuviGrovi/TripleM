"""
TripleM – Moodle Mission Manager  v5
app.py
"""

from flask import Flask, jsonify, request, send_from_directory
import sqlite3, os, json, threading, time, logging, re, base64
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%H:%M:%S',
)

app = Flask(__name__, static_folder='../frontend', static_url_path='')

DB_PATH       = os.path.join(os.path.dirname(__file__), '..', 'data', 'tasks.db')
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'settings.json')

# ── CORS ──────────────────────────────────────────────────────
@app.after_request
def add_cors(resp):
    resp.headers['Access-Control-Allow-Origin']  = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,PATCH,DELETE,OPTIONS'
    return resp

@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>',             methods=['OPTIONS'])
def options_handler(path): return '', 204

# ── DATABASE ──────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS tasks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                moodle_id    TEXT UNIQUE,
                title        TEXT NOT NULL,
                course       TEXT,
                course_color TEXT DEFAULT '#4f8ef7',
                type         TEXT DEFAULT 'academic',
                due_date     TEXT,
                status       TEXT DEFAULT 'not_started',
                notes        TEXT DEFAULT '',
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sync_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at TEXT DEFAULT (datetime('now')),
                status    TEXT,
                message   TEXT,
                new_tasks INTEGER DEFAULT 0,
                method    TEXT DEFAULT 'ical'
            );
            CREATE TABLE IF NOT EXISTS user_filters (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                filter_text TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );
        ''')
        # ── Migrations (safe – skip if column already exists) ──────
        for col_sql in [
            "ALTER TABLE tasks ADD COLUMN subtasks TEXT DEFAULT '[]'",
            "ALTER TABLE tasks ADD COLUMN progress_mode TEXT DEFAULT 'subtasks'",
            "ALTER TABLE tasks ADD COLUMN manual_progress INTEGER DEFAULT 0",
            "ALTER TABLE categories ADD COLUMN course_id TEXT DEFAULT ''",
            # course_num: 8-digit IDs from iCal CATEGORIES field (e.g. '00940411')
            # This is HOW we match apply_category — NOT from UID/moodle_id!
            "ALTER TABLE tasks ADD COLUMN course_num TEXT DEFAULT ''",
        ]:
            try: conn.execute(col_sql)
            except: pass

        # ── Categories table ────────────────────────────────────────
        conn.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                type       TEXT DEFAULT 'academic',
                color      TEXT DEFAULT '#6c8fff',
                icon       TEXT DEFAULT '📘',
                subtasks   TEXT DEFAULT '[]',
                is_builtin INTEGER DEFAULT 0,
                course_id  TEXT DEFAULT ''
            )
        ''')
        _seed_categories(conn)

def _seed_categories(conn):
    """Populate built-in course categories on first run."""
    BUILTIN = [
        ('מכניקת המוצקים',                          'academic','#ff6b7a','⚙️','מכניקת המוצקים'),
        ('חשבון דיפרונציאלי ואינטגרלי 2מ1',         'academic','#4ade88','∫', 'חשבון דיפרונציאלי'),
        ('פיסיקה 2',                                 'academic','#b48bff','⚡','פיסיקה 2'),
        ('מבוא להנדסת חומרים לתעופה וחלל',           'academic','#fb923c','✈️','מבוא להנדסת חומרים'),
        ('משוואות דיפרונציאליות רגילות/ח',           'academic','#6c8fff','∂', 'משוואות דיפרונציאליות'),
        ('שרטוט הנדסי ממוחשב',                       'academic','#2dd4bf','📐','שרטוט הנדסי'),
    ]
    for name, typ, color, icon, tmpl_key in BUILTIN:
        if conn.execute('SELECT id FROM categories WHERE name=?',(name,)).fetchone():
            continue
        items = AUTO_TEMPLATES.get(tmpl_key, _DEFAULT_TEMPLATE)
        base_w = round(100/len(items))
        subs   = [{'id':str(i+1),'text':t,'done':False,'weight':base_w} for i,t in enumerate(items)]
        # Fix rounding so total == 100
        diff = 100 - sum(s['weight'] for s in subs)
        if subs: subs[-1]['weight'] += diff
        conn.execute(
            'INSERT INTO categories(name,type,color,icon,subtasks,is_builtin) VALUES(?,?,?,?,?,1)',
            (name, typ, color, icon, json.dumps(subs, ensure_ascii=False))
        )
def load_settings() -> dict:
    s = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                s = json.load(f)
        except Exception:
            pass
    # Cloud deployment: env vars override settings.json
    # Set ICAL_URL and ANTHROPIC_API_KEY in Render/Railway dashboard
    _ENV = {
        'ICAL_URL':           'ical_url',
        'ANTHROPIC_API_KEY':  'anthropic_api_key',
        'MOODLE_USERNAME':    'moodle_username',
        'MOODLE_PASSWORD':    'moodle_password',
    }
    for env_key, s_key in _ENV.items():
        val = os.environ.get(env_key, '').strip()
        if val:
            s[s_key] = val
    return s

def save_settings_file(data: dict):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

from moodle_scraper import get_course_color, COURSE_ID_MAP

# ── AUTO-TEMPLATES ────────────────────────────────────────────
AUTO_TEMPLATES = {
    'מכניקת המוצקים': [
        'קריאת רקע תיאורטי ופתרון לדוגמה',
        'פתרון כל השאלות על דף',
        'בדיקת תשובות וניסוח מחדש',
        'סריקה והעלאה למודל',
    ],
    'חשבון דיפרונציאלי': [
        'קריאת הנחיות',
        'פתרון תרגילים',
        'בדיקת חישובים',
        'הגשה ל-WebWork / Moodle',
    ],
    'פיסיקה 2': [
        'קריאת נוסחאות רלוונטיות',
        'ניתוח כל שאלה',
        'פתרון מלא על דף',
        'סריקה והגשה',
    ],
    'מבוא להנדסת חומרים': [
        'קריאת פרק מהספר',
        'פתרון תרגילים',
        'ניסוח דוח',
        'הגשה',
    ],
    'משוואות דיפרונציאליות': [
        'קריאת שיטות פתרון',
        'פתרון כל שאלה',
        'בדיקה',
        'הגשה',
    ],
    'שרטוט הנדסי': [
        'קריאת הנחיות',
        'שרטוט ראשוני',
        'בדיקת מידות',
        'הגשת קובץ',
    ],
    'WebWork': [
        'פתרון שאלות',
        'הגשה',
    ],
}

_DEFAULT_TEMPLATE = ['קריאת חומר רקע', 'פתרון', 'הגשה']

def get_auto_template(course: str) -> str:
    """Return JSON-encoded default subtasks – DB categories take priority."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT subtasks FROM categories WHERE ? LIKE '%' || name || '%'",
                (course,)
            ).fetchone()
            if row and row['subtasks']:
                subs = json.loads(row['subtasks'])
                for s in subs:
                    s['done'] = False
                    if 'id' not in s: s['id'] = str(subs.index(s)+1)
                return json.dumps(subs, ensure_ascii=False)
    except Exception:
        pass
    # Fallback hardcoded
    items = _DEFAULT_TEMPLATE
    for key, tmpl in AUTO_TEMPLATES.items():
        if key in (course or ''):
            items = tmpl
            break
    subs = [{'id': str(i+1), 'text': t, 'done': False} for i, t in enumerate(items)]
    return json.dumps(subs, ensure_ascii=False)

# ── UPSERT ────────────────────────────────────────────────────
def _upsert(assignments: list) -> int:
    new_count = 0
    with get_db() as conn:
        for a in assignments:
            # Use color from parser if available, else compute from name
            color = a.get('course_color') or get_course_color(a.get('course', ''))
            exists = conn.execute(
                'SELECT id FROM tasks WHERE moodle_id=?', (a['moodle_id'],)
            ).fetchone()
            if not exists:
                auto_subs = get_auto_template(a.get('course', ''))
                conn.execute(
                    '''INSERT INTO tasks
                       (moodle_id,title,course,course_color,type,due_date,status,subtasks,course_num)
                       VALUES(?,?,?,?,'academic',?,'not_started',?,?)''',
                    (a['moodle_id'], a['title'], a['course'], color,
                     a.get('due_date'), auto_subs, a.get('course_num', ''))
                )
                new_count += 1
            else:
                # Always update title/course/color/date — preserve user's status & notes
                conn.execute(
                    '''UPDATE tasks SET title=?,course=?,course_color=?,due_date=?,
                       course_num=?,updated_at=datetime('now') WHERE moodle_id=?''',
                    (a['title'], a['course'], color, a.get('due_date'),
                     a.get('course_num', ''), a['moodle_id'])
                )
    return new_count

# ── SYNC ──────────────────────────────────────────────────────
def _auto_apply_all_categories():
    """After every sync: re-apply all categories that have a course_id set.
    This ensures newly imported tasks get properly categorized."""
    try:
        with get_db() as conn:
            cats = conn.execute(
                "SELECT * FROM categories WHERE course_id IS NOT NULL AND course_id != ''"
            ).fetchall()
            for cat in cats:
                ids = [i.strip() for i in (cat['course_id'] or '').split(',') if i.strip()]
                for cid_val in ids:
                    result = conn.execute(
                        """UPDATE tasks SET course=?, course_color=?, updated_at=datetime('now')
                           WHERE course_num LIKE ? AND type='academic'""",
                        (cat['name'], cat['color'] or '#6c8fff', f'%{cid_val}%')
                    )
                    if result.rowcount:
                        logging.info(f'Auto-apply "{cat["name"]}": {result.rowcount} tasks matched')
    except Exception as e:
        logging.warning(f'_auto_apply_all_categories: {e}')


def sync_moodle() -> dict:
    settings  = load_settings()
    ical_url  = settings.get('ical_url', '').strip()
    if not ical_url:
        with get_db() as conn:
            conn.execute("INSERT INTO sync_log(status,message) VALUES(?,?)",
                         ('skipped','הגדר קישור iCal בהגדרות'))
        return {'status':'skipped','message':'הגדר קישור iCal בהגדרות','new_tasks':0}
    try:
        from moodle_scraper import ICalSyncer
        assignments = ICalSyncer(ical_url).fetch()
        new_count   = _upsert(assignments)
        # Auto-apply all categories that have a course_id — fixes newly imported tasks
        _auto_apply_all_categories()
        with get_db() as conn:
            conn.execute("INSERT INTO sync_log(status,message,new_tasks) VALUES(?,?,?)",
                         ('success',f'{len(assignments)} אירועים, {new_count} חדשים',new_count))
        logging.info(f'Sync OK: {len(assignments)} events, {new_count} new')
        return {'status':'success','new_tasks':new_count,'total':len(assignments)}
    except Exception as e:
        logging.error(f'Sync failed: {e}')
        with get_db() as conn:
            conn.execute("INSERT INTO sync_log(status,message) VALUES(?,?)",('error',str(e)))
        return {'status':'error','message':str(e),'new_tasks':0}

_AUTO_SYNC_HOURS = 6

def _scheduler_loop():
    logging.info(f'Auto-sync every {_AUTO_SYNC_HOURS}h')
    while True:
        time.sleep(_AUTO_SYNC_HOURS * 3600)
        try: sync_moodle()
        except Exception as e: logging.warning(f'Auto-sync: {e}')

# ══════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

# ── TASKS ─────────────────────────────────────────────────────
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    status = request.args.get('status')
    typ    = request.args.get('type')
    course = request.args.get('course')
    sort   = request.args.get('sort','due_date')
    if sort not in ['due_date','created_at','title','course','status']:
        sort = 'due_date'
    q,p = 'SELECT * FROM tasks WHERE 1=1',[]
    if status: q+=' AND status=?';  p.append(status)
    if typ:    q+=' AND type=?';    p.append(typ)
    if course: q+=' AND course=?';  p.append(course)
    q += f' ORDER BY CASE WHEN {sort} IS NULL THEN 1 ELSE 0 END, {sort} ASC'
    with get_db() as conn:
        rows = conn.execute(q,p).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tasks', methods=['POST'])
def create_task():
    d = request.json or {}
    if not d.get('title'): return jsonify({'error':'Missing title'}),400
    course = d.get('course','אישי')
    # Auto-template only for academic tasks
    auto_subs = get_auto_template(course) if d.get('type','personal') == 'academic' else '[]'
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO tasks(title,course,type,due_date,status,notes,course_color,subtasks) VALUES(?,?,?,?,?,?,?,?)',
            (d['title'], course, d.get('type','personal'),
             d.get('due_date'), d.get('status','not_started'), d.get('notes',''),
             d.get('course_color', get_course_color(course)), auto_subs))
        task = conn.execute('SELECT * FROM tasks WHERE id=?',(cur.lastrowid,)).fetchone()
    return jsonify(dict(task)),201

@app.route('/api/tasks/<int:tid>', methods=['PATCH'])
def update_task(tid):
    d = request.json or {}
    allowed = ['title','course','due_date','status','notes','course_color','subtasks',
               'progress_mode','manual_progress']
    updates = {k:v for k,v in d.items() if k in allowed}
    if not updates: return jsonify({'error':'Nothing to update'}),400
    clause = ', '.join(f'{k}=?' for k in updates)
    with get_db() as conn:
        conn.execute(f"UPDATE tasks SET {clause}, updated_at=datetime('now') WHERE id=?",
                     list(updates.values())+[tid])
        task = conn.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
    return jsonify(dict(task)) if task else (jsonify({'error':'Not found'}),404)

@app.route('/api/tasks/<int:tid>', methods=['DELETE'])
def delete_task(tid):
    with get_db() as conn:
        conn.execute('DELETE FROM tasks WHERE id=?',(tid,))
    return jsonify({'ok':True})

@app.route('/api/tasks/clear-moodle', methods=['POST'])
def clear_moodle_tasks():
    with get_db() as conn:
        result  = conn.execute("DELETE FROM tasks WHERE type='academic' AND moodle_id LIKE 'ical_%'")
        deleted = result.rowcount
    return jsonify({'ok':True,'deleted':deleted})

# ── SUBTASKS ───────────────────────────────────────────────────
@app.route('/api/tasks/<int:tid>/subtasks', methods=['GET'])
def get_subtasks(tid):
    with get_db() as conn:
        task = conn.execute('SELECT subtasks FROM tasks WHERE id=?', (tid,)).fetchone()
    if not task:
        return jsonify({'error': 'Not found'}), 404
    try:
        return jsonify(json.loads(task['subtasks'] or '[]'))
    except Exception:
        return jsonify([])

@app.route('/api/tasks/<int:tid>/subtasks', methods=['PUT'])
def put_subtasks(tid):
    subtasks = request.json
    if not isinstance(subtasks, list):
        return jsonify({'error': 'Expected array'}), 400

    # Auto-update task status based on subtask completion
    total = len(subtasks)
    done  = sum(1 for s in subtasks if s.get('done'))
    if total == 0:
        new_status = None
    elif done == total:
        new_status = 'done'
    elif done > 0:
        new_status = 'in_progress'
    else:
        new_status = 'not_started'

    subs_json = json.dumps(subtasks, ensure_ascii=False)
    with get_db() as conn:
        if new_status:
            conn.execute(
                "UPDATE tasks SET subtasks=?, status=?, updated_at=datetime('now') WHERE id=?",
                (subs_json, new_status, tid)
            )
        else:
            conn.execute(
                "UPDATE tasks SET subtasks=?, updated_at=datetime('now') WHERE id=?",
                (subs_json, tid)
            )
    return jsonify({'ok': True, 'status': new_status})

# ── STATS & COURSES ───────────────────────────────────────────
@app.route('/api/stats')
def stats():
    with get_db() as conn:
        total     = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        submitted = conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('submitted','done')").fetchone()[0]
        in_prog   = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='in_progress'").fetchone()[0]
        not_start = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='not_started'").fetchone()[0]
        overdue   = conn.execute("SELECT COUNT(*) FROM tasks WHERE due_date<datetime('now') AND status NOT IN ('submitted','done')").fetchone()[0]
        upcoming  = conn.execute("SELECT COUNT(*) FROM tasks WHERE due_date BETWEEN datetime('now') AND datetime('now','+7 days') AND status NOT IN ('submitted','done')").fetchone()[0]
    return jsonify({'total':total,'submitted':submitted,'in_progress':in_prog,
                    'not_started':not_start,'overdue':overdue,'upcoming_week':upcoming})

@app.route('/api/courses')
def get_courses():
    with get_db() as conn:
        rows = conn.execute('SELECT DISTINCT course,course_color FROM tasks WHERE course IS NOT NULL ORDER BY course').fetchall()
    return jsonify([dict(r) for r in rows])
@app.route('/api/tasks/unknown-count')
def unknown_task_count():
    """Returns count of tasks with unknown course — used by defaults UI."""
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE course='לא ידוע' AND type='academic'"
        ).fetchone()[0]
        sample = conn.execute(
            "SELECT moodle_id, title FROM tasks WHERE course='לא ידוע' AND type='academic' LIMIT 5"
        ).fetchall()
    return jsonify({'count': count, 'sample': [dict(r) for r in sample]})

# ── SYNC ──────────────────────────────────────────────────────
@app.route('/api/debug/ical-uids', methods=['GET'])
def debug_ical_uids():
    """מציג את ה-UIDs הגולמיים מה-iCal — לאיבחון בעיות קיטלוג."""
    import requests as req
    settings = load_settings()
    url      = settings.get('ical_url', '').strip()
    if not url:
        return jsonify({'error': 'אין קישור iCal'}), 400
    try:
        resp = req.get(url, headers={'User-Agent': 'TripleM/debug'}, timeout=20)
        text = resp.text.replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'\n[ \t]', '', text)
        results = []
        for block in re.split(r'BEGIN:VEVENT', text)[1:]:
            def f(name):
                m = re.search(rf'^{name}(?:;[^:\n]*)?:(.*)$', block, re.M|re.I)
                return m.group(1).strip() if m else ''
            uid        = f('UID')
            summary    = f('SUMMARY')
            categories = f('CATEGORIES')
            nums_cat   = re.findall(r'\d{8}', categories)
            nums_uid   = re.findall(r'\d{8}', uid)
            course     = 'לא ידוע'
            for num in nums_cat + nums_uid:
                if num in COURSE_ID_MAP:
                    course = COURSE_ID_MAP[num][0]
                    break
            results.append({
                'uid':              uid[:100],
                'summary':          summary[:80],
                'categories':       categories,
                'nums_in_categories': nums_cat,
                'nums_in_uid':      nums_uid,
                'course_resolved':  course,
                # course_num shows ALL 8-digit IDs from CATEGORIES — including unrecognized ones
                # Users should paste these into the category editor's "מזהה קורס" field
                'course_num':       ','.join(nums_cat) if nums_cat else '',
            })
        return jsonify({'total': len(results), 'events': results[:30]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync', methods=['POST'])
def trigger_sync():
    return jsonify(sync_moodle())

@app.route('/api/sync/log')
def sync_log():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM sync_log ORDER BY id DESC LIMIT 15').fetchall()
    return jsonify([dict(r) for r in rows])

# ── SETTINGS ──────────────────────────────────────────────────
@app.route('/api/settings', methods=['GET'])
def get_settings():
    s    = load_settings()
    safe = {k:v for k,v in s.items() if k not in ('moodle_password','anthropic_api_key')}
    safe['has_password']    = bool(s.get('moodle_password'))
    safe['has_ical']        = bool(s.get('ical_url'))
    safe['has_api_key']     = bool(s.get('anthropic_api_key'))
    url = s.get('ical_url','')
    safe['ical_url_preview'] = ('...' + url[-40:]) if len(url)>40 else url
    # Show last 4 chars of API key so user knows which key is saved
    ak = s.get('anthropic_api_key','')
    safe['api_key_preview'] = ('sk-...'+ak[-4:]) if len(ak)>4 else ('' if not ak else '****')
    return jsonify(safe)

@app.route('/api/settings', methods=['POST'])
def post_settings():
    d   = request.json or {}
    cur = load_settings()
    for key in ('ical_url','moodle_username','moodle_password','anthropic_api_key'):
        if key in d:
            cur[key] = d[key].strip() if isinstance(d[key],str) else d[key]
    save_settings_file(cur)
    return jsonify({'ok':True})

@app.route('/api/settings/test-ical', methods=['POST'])
def test_ical():
    d   = request.json or {}
    url = d.get('url','').strip()
    if not url: return jsonify({'ok':False,'message':'לא סופק URL'})
    try:
        from moodle_scraper import ICalSyncer
        assignments = ICalSyncer(url).fetch()
        with_course = sum(1 for a in assignments if a['course']!='לא ידוע')
        return jsonify({'ok':True,'count':len(assignments),
                        'message':f'✅ {len(assignments)} אירועים, {with_course} עם קורס מזוהה'})
    except Exception as e:
        return jsonify({'ok':False,'message':f'❌ {e}'})

# ── USER FILTERS (dynamic) ────────────────────────────────────
@app.route('/api/filters', methods=['GET'])
def get_filters():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM user_filters ORDER BY id').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/filters', methods=['POST'])
def save_filters():
    """Replace all filters with new list."""
    words = (request.json or {}).get('filters', [])
    with get_db() as conn:
        conn.execute('DELETE FROM user_filters')
        for w in words:
            w = w.strip()
            if w:
                conn.execute('INSERT INTO user_filters(filter_text) VALUES(?)', (w,))
    return jsonify({'ok': True, 'count': len(words)})

# ── WEBWORK FROM IMAGE ────────────────────────────────────────
def _ocr_parse_webwork(img_bytes: bytes) -> list:
    """Free OCR-based WebWork extraction using Tesseract.
    Parses HWxx lines + 'Due DD.MM.YYYY @ HH:MM' from screenshot.
    Returns list of {'name':'HW01','due':'2026-05-31 23:59'}."""
    from PIL import Image
    import pytesseract, io

    img = Image.open(io.BytesIO(img_bytes)).convert('L')
    # 2x upscale for better OCR accuracy on small text
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)
    text = pytesseract.image_to_string(img, lang='eng', config='--psm 6')

    results, current_hw = [], None
    for line in text.split('\n'):
        line = line.strip()
        hw_m = re.match(r'\b(HW\s*\d+)\b', line, re.IGNORECASE)
        if hw_m:
            current_hw = re.sub(r'\s+', '', hw_m.group(1)).upper()
        if current_hw:
            due_m = re.search(
                r'Due\s+(\d{1,2})[./](\d{2})[./](\d{4})\s*@\s*(\d{2}):(\d{2})',
                line, re.IGNORECASE
            )
            if due_m:
                day, mon, yr, hh, mm = due_m.groups()
                results.append({
                    'name': current_hw,
                    'due':  f'{yr}-{mon}-{day.zfill(2)} {hh}:{mm}'
                })
                current_hw = None
    return results

@app.route('/api/import/webwork-image', methods=['POST'])
def import_webwork_image():
    if 'image' not in request.files:
        return jsonify({'error': 'לא נשלחה תמונה'}), 400

    img_file  = request.files['image']
    img_bytes = img_file.read()
    course    = request.form.get('course', 'חשבון דיפרונציאלי ואינטגרלי 2מ1')
    settings  = load_settings()
    api_key   = settings.get('anthropic_api_key', '').strip()

    try:
        # ── Mode A: Anthropic API (smarter, requires API key) ──────────
        if api_key:
            import requests as req, json as json_mod
            img_b64 = base64.b64encode(img_bytes).decode()
            mime    = img_file.content_type or 'image/png'
            payload = {
                "model": "claude-haiku-4-5-20251001",  # cheapest model, enough for OCR
                "max_tokens": 800,
                "messages": [{"role": "user", "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": mime, "data": img_b64}},
                    {"type": "text", "text": (
                        "WebWork assignment screenshot. Extract ONLY HW## assignments (not Tirgul/Quiz/Review). "
                        "JSON array only, no markdown: "
                        '[{"name":"HW01","due":"2026-05-31 06:00"}] '
                        "Date format: YYYY-MM-DD HH:MM"
                    )}
                ]}]
            }
            resp = req.post(
                'https://api.anthropic.com/v1/messages',
                headers={'Content-Type':'application/json',
                         'x-api-key': api_key,
                         'anthropic-version':'2023-06-01'},
                json=payload, timeout=30
            )
            resp.raise_for_status()
            raw = re.sub(r'```[a-z]*\n?|```', '',
                         resp.json()['content'][0]['text']).strip()
            hw_list = json_mod.loads(raw)
            mode = 'ai'

        # ── Mode B: Tesseract OCR (free, no API key needed) ─────────────
        else:
            hw_list = _ocr_parse_webwork(img_bytes)
            # normalize key: OCR returns 'due', keep consistent with AI mode
            mode = 'ocr'

        # ── Save to DB ──────────────────────────────────────────────────
        color, added = get_course_color(course), 0
        with get_db() as conn:
            for hw in hw_list:
                name = str(hw.get('name', '')).upper()
                if not name.startswith('HW'):
                    continue
                due      = hw.get('due') or hw.get('due_date')
                mid      = f"ww_{re.sub(r'[^a-z0-9]','_',course.lower()[:8])}_{name}"
                if not conn.execute('SELECT id FROM tasks WHERE moodle_id=?', (mid,)).fetchone():
                    conn.execute(
                        "INSERT INTO tasks(moodle_id,title,course,course_color,type,due_date,status) "
                        "VALUES(?,?,?,?,'academic',?,'not_started')",
                        (mid, f'WebWork {name}', course, color, due)
                    )
                    added += 1

        return jsonify({'ok': True, 'extracted': len(hw_list), 'added': added, 'mode': mode})

    except Exception as e:
        logging.exception('WebWork import failed')
        return jsonify({'error': str(e)}), 500

# ── SCHEDULE ──────────────────────────────────────────────────
def init_schedule_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS schedule_blocks (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                title          TEXT NOT NULL,
                day_of_week    INTEGER NOT NULL,
                start_slot     INTEGER NOT NULL,
                duration_slots INTEGER NOT NULL DEFAULT 2,
                type           TEXT DEFAULT 'academic',
                course         TEXT,
                color          TEXT DEFAULT '#4f8ef7',
                created_at     TEXT DEFAULT (datetime('now'))
            )
        ''')

@app.route('/api/schedule', methods=['GET'])
def get_schedule():
    init_schedule_db()
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM schedule_blocks ORDER BY day_of_week, start_slot').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/schedule', methods=['POST'])
def create_block():
    init_schedule_db()
    d = request.json or {}
    if not d.get('title'): return jsonify({'error': 'Missing title'}), 400
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO schedule_blocks (title,day_of_week,start_slot,duration_slots,type,course,color) VALUES(?,?,?,?,?,?,?)',
            (d['title'], d.get('day_of_week',0), d.get('start_slot',0),
             d.get('duration_slots',2), d.get('type','academic'),
             d.get('course'), d.get('color','#4f8ef7')))
        block = conn.execute('SELECT * FROM schedule_blocks WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(block)), 201

@app.route('/api/schedule/<int:bid>', methods=['PATCH'])
def update_block(bid):
    d = request.json or {}
    allowed = ['title','day_of_week','start_slot','duration_slots','type','course','color']
    updates = {k:v for k,v in d.items() if k in allowed}
    if not updates: return jsonify({'error':'Nothing to update'}), 400
    clause = ', '.join(f'{k}=?' for k in updates)
    with get_db() as conn:
        conn.execute(f'UPDATE schedule_blocks SET {clause} WHERE id=?', list(updates.values())+[bid])
        block = conn.execute('SELECT * FROM schedule_blocks WHERE id=?', (bid,)).fetchone()
    return jsonify(dict(block)) if block else (jsonify({'error':'Not found'}), 404)

@app.route('/api/schedule/<int:bid>', methods=['DELETE'])
def delete_block(bid):
    with get_db() as conn:
        conn.execute('DELETE FROM schedule_blocks WHERE id=?', (bid,))
    return jsonify({'ok': True})

# ── CATEGORIES (defaults editor) ──────────────────────────────
@app.route('/api/categories', methods=['GET'])
def get_categories():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM categories ORDER BY type, name').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/categories', methods=['POST'])
def create_category():
    d = request.json or {}
    if not d.get('name'): return jsonify({'error': 'Missing name'}), 400
    subs = json.dumps(d.get('subtasks', []), ensure_ascii=False)
    try:
        with get_db() as conn:
            cur = conn.execute(
                'INSERT INTO categories(name,type,color,icon,subtasks,is_builtin) VALUES(?,?,?,?,?,0)',
                (d['name'], d.get('type','academic'), d.get('color','#6c8fff'),
                 d.get('icon','📘'), subs)
            )
            cat = conn.execute('SELECT * FROM categories WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(cat)), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/categories/<int:cid>', methods=['PATCH'])
def update_category(cid):
    d = request.json or {}
    updates = {}
    for k in ('name','type','color','icon','course_id'):
        if k in d: updates[k] = d[k]
    if 'subtasks' in d:
        updates['subtasks'] = json.dumps(d['subtasks'], ensure_ascii=False)
    if not updates: return jsonify({'error': 'Nothing to update'}), 400
    clause = ', '.join(f'{k}=?' for k in updates)
    with get_db() as conn:
        conn.execute(f'UPDATE categories SET {clause} WHERE id=?', list(updates.values())+[cid])
        cat = conn.execute('SELECT * FROM categories WHERE id=?', (cid,)).fetchone()
    return jsonify(dict(cat)) if cat else (jsonify({'error':'Not found'}), 404)

@app.route('/api/categories/<int:cid>/apply', methods=['POST'])
def apply_category(cid):
    """Re-assign tasks whose moodle_id contains ANY of the category's course_ids.
    Accepts multiple IDs comma-separated in course_id field: '01040043,12345'.
    Only matches by moodle_id — does NOT blanket-reassign all 'לא ידוע' tasks.
    """
    with get_db() as conn:
        cat = conn.execute('SELECT * FROM categories WHERE id=?', (cid,)).fetchone()
        if not cat: return jsonify({'error':'Not found'}), 404
        ids = [i.strip() for i in (cat['course_id'] or '').split(',') if i.strip()]
        if not ids:
            return jsonify({'updated': 0, 'message': 'אין מספר קורס מוגדר'})
        color = cat['color'] or '#6c8fff'
        name  = cat['name']
        updated = 0
        for cid_val in ids:
            # course_num stores the 8-digit ID from the iCal CATEGORIES field
            # This is the ONLY reliable place the course ID appears for Technion Moodle
            result = conn.execute(
                """UPDATE tasks SET course=?, course_color=?, updated_at=datetime('now')
                   WHERE course_num LIKE ? AND type='academic'""",
                (name, color, f'%{cid_val}%')
            )
            updated += result.rowcount
    return jsonify({'ok': True, 'updated': updated,
                    'message': f'שויכו {updated} מטלות לקורס "{name}"'})

@app.route('/api/categories/<int:cid>', methods=['DELETE'])
def delete_category(cid):
    with get_db() as conn:
        row = conn.execute('SELECT is_builtin FROM categories WHERE id=?', (cid,)).fetchone()
        if not row: return jsonify({'error':'Not found'}), 404
        if row['is_builtin']: return jsonify({'error':'לא ניתן למחוק קטגוריה מובנית'}), 403
        conn.execute('DELETE FROM categories WHERE id=?', (cid,))
    return jsonify({'ok': True})

# ══════════════════════════════════════════════════════════════
#  STARTUP — runs under gunicorn AND when called directly
#  (previously inside __main__ only — tables never created on Render!)
# ══════════════════════════════════════════════════════════════
init_db()
init_schedule_db()
_startup_settings = load_settings()
if _startup_settings.get('ical_url'):
    threading.Thread(target=sync_moodle, daemon=True).start()
threading.Thread(target=_scheduler_loop, daemon=True).start()

# ══════════════════════════════════════════════════════════════
#  MAIN — Flask dev server only (not used by gunicorn/Render)
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print('\n' + '='*50)
    print('  🚀 TripleM – Moodle Mission Manager  v9')
    print(f'  http://localhost:{port}')
    print(f'  Auto-sync every {_AUTO_SYNC_HOURS}h')
    print('='*50 + '\n')
    app.run(host='0.0.0.0', port=port, debug=False)
