import os
import re
import io
import json
import hmac
import datetime
import urllib.parse
from collections import defaultdict
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
)

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
HISTORY_START_DATE = os.environ.get("HISTORY_START_DATE", "2024-01-01")
FUTURE_HORIZON_DAYS = int(os.environ.get("FUTURE_HORIZON_DAYS", "365"))
PRICES_FILE = os.path.join(os.path.dirname(__file__), "prices.json")
PRICES_SHEET_ID = os.environ.get("GOOGLE_PRICES_SHEET_ID", "").strip()
PRICES_SHEET_DATA_RANGE = "Prices!A2:C1000"
PRICES_SHEET_WRITE_START = "Prices!A2"

# כרטסת לקוח: שדות ידניים לכל (לקוח, תאריך), בלשונית נפרדת באותו גיליון של
# המחירון - לא ביומן, שנשאר read-only. כל ביקור יכול להכיל כמה "שורות פרטים"
# (RowIndex) - בעיקר לצבע, כשמערבבים שני מספרי צבע באותו ביקור.
# עמודות: Client, Date, TreatmentType, RowIndex, Company, TypeDetail, Number,
# Amount, OxidantType, OxidantAmount.
CLIENT_NOTES_SHEET_DATA_RANGE = "ClientNotes!A2:J5000"
CLIENT_NOTES_SHEET_WRITE_START = "ClientNotes!A2"

# אפליקציית המספרה (צ'ק-אין בברקוד + כרטיסייה):
# Clients - טלפון <-> שם (השם זהה לשם שבתיאור התור ביומן ובאנשי הקשר).
#   עמודות: Phone (972XXXXXXXXX), Name, CreatedAt, Source.
# Checkins - כל סריקה של הברקוד בכניסה. עמודות: Time, Phone, Name, IsNew.
# שתי הלשוניות נוצרות אוטומטית בגיליון בפעם הראשונה שצריך אותן.
CLIENTS_TAB = "Clients"
CHECKINS_TAB = "Checkins"
CLIENTS_SHEET_DATA_RANGE = "Clients!A2:D5000"
CHECKINS_SHEET_DATA_RANGE = "Checkins!A2:D20000"
TAB_HEADERS = {
    CLIENTS_TAB: ["Phone", "Name", "CreatedAt", "Source"],
    CHECKINS_TAB: ["Time", "Phone", "Name", "IsNew"],
}

TIMEZONE = os.environ.get("TIMEZONE", "Asia/Jerusalem")
LOCAL_TZ = ZoneInfo(TIMEZONE)
# סוד משותף בין Make לבין הדשבורד - בלעדיו אף אחד מבחוץ לא יכול "לרשום הגעה".
CHECKIN_SECRET = os.environ.get("CHECKIN_SECRET", "").strip()
# המספר של העסק (המספר המחובר ל-Green-API) - אליו הלקוח שולח הודעה מהברקוד.
BUSINESS_WHATSAPP = re.sub(r"\D", "", os.environ.get("BUSINESS_WHATSAPP", ""))
CHECKIN_KEYWORD = os.environ.get("CHECKIN_KEYWORD", "הגעתי").strip()
CHECKIN_MESSAGE = os.environ.get("CHECKIN_MESSAGE", f"היי, {CHECKIN_KEYWORD} למספרה").strip()
# כתובת הדשבורד כפי שהיא נראית מבחוץ (למשל https://hezi-dashboard.onrender.com),
# לקישורים שנשלחים בוואטסאפ. אם ריק - נלקח מהבקשה עצמה.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
# כרטיסייה: כל ביקור N הוא מתנה. 0 מבטל את הכרטיסייה.
LOYALTY_VISITS = int(os.environ.get("LOYALTY_VISITS", "10") or 0)

# סט השדות שמוצג בכרטסת לכל סוג טיפול - person keys חייבים להישאר תואמים
# ל-FIELD_SETS ב-templates/client_card.html (ה-JS שם מפעיל את אותה מיפוי
# בצד הלקוח, כדי לא לדרוש רענון עמוד בכל שינוי בחירה).
TREATMENT_FIELD_SETS = {
    "צבע": [
        {"key": "company", "label": "חברה"},
        {"key": "type_detail", "label": "סוג הצבע"},
        {"key": "number", "label": "מספר צבע"},
        {"key": "amount", "label": "כמות צבע"},
        {"key": "oxidant_type", "label": "סוג חמצן"},
        {"key": "oxidant_amount", "label": "כמות חמצן"},
    ],
    "החלקה": [
        {"key": "company", "label": "חברה"},
        {"key": "type_detail", "label": "סוג החלקה"},
        {"key": "amount", "label": "כמות"},
        {"key": "number", "label": "הערה"},
    ],
    "פסים": [
        {"key": "amount", "label": "כמות החומר"},
        {"key": "oxidant_type", "label": "סוג חמצן"},
        {"key": "oxidant_amount", "label": "כמות חמצן"},
    ],
}
TREATMENT_FIELD_SET_DEFAULT = [{"key": "type_detail", "label": "הערה"}]
# סוגי טיפול שמאפשרים כמה שורות פרטים (כפתור "הוסף שורה") - צבע (ערבוב של
# כמה מספרי צבע באותו ביקור) והחלקה (למשל שלב ראשון ושני עם סוג/כמות שונים).
MULTI_ROW_TREATMENT_TYPES = {"צבע", "החלקה"}

# שמות הצבעים כפי שהם מוצגים בבורר הצבעים של Google Calendar (עברית ואנגלית),
# ממופים ל-colorId שה-API מחזיר על כל אירוע. עמודה שלישית (C) בגיליון המחירון
# מגדירה אילו טיפולים מזוהים לפי צבע האירוע במקום לפי שדה המיקום.
COLOR_NAME_TO_ID = {
    "lavender": "1", "לבנדר": "1",
    "sage": "2", "מרווה": "2",
    "grape": "3", "ענבים": "3",
    "flamingo": "4", "פלמינגו": "4",
    "banana": "5", "בננה": "5",
    "tangerine": "6", "קלמנטינה": "6", "כתום": "6",
    "peacock": "7", "טווס": "7",
    "graphite": "8", "גרפיט": "8", "אפור": "8",
    "blueberry": "9", "אוכמניות": "9", "כחול": "9",
    "basil": "10", "בזיליקום": "10", "ירוק כהה": "10",
    "tomato": "11", "עגבנייה": "11", "אדום": "11",
}

# היומן נשאר readonly בכוונה: גם אם מישהו ישנה קוד בטעות ויקרא ל-insert/update על
# היומן, הבקשה תידחה ע"י גוגל ברמת ה-API. spreadsheets משמש רק לגיליון המחירון
# הנפרד (לא היומן) כדי שעריכת מחירים תישרד הפעלות מחדש של השרת.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

_cache = {"data": None, "fetched_at": None}
_events_cache = {"events": None, "fetched_at": None}
_price_rows_cache = {"rows": None, "fetched_at": None}
_client_notes_rows_cache = {"rows": None, "fetched_at": None}
_clients_rows_cache = {"rows": None, "fetched_at": None}
_checkins_rows_cache = {"rows": None, "fetched_at": None}
_ensured_tabs = set()
CACHE_TTL_SECONDS = 300


def normalize_treatment_name(name):
    return re.sub(r"\s+", " ", (name or "").strip())


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


_credentials = None
_calendar_service = None
_sheets_service = None


def get_credentials():
    global _credentials
    if _credentials is None:
        _credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return _credentials


def get_calendar_service():
    # בונים את השירות פעם אחת ומשתמשים בו חוזר - build() עם
    # cache_discovery=False מוריד מחדש מהרשת את כל מסמך ה-discovery של ה-API
    # בכל קריאה, וזה נקרא הרבה פעמים בכל בקשה (במיוחד בשמירת כרטסת לקוח) -
    # זה היה כנראה המקור העיקרי לעומס הזיכרון/קריסות.
    global _calendar_service
    if _calendar_service is None:
        _calendar_service = build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)
    return _calendar_service


def get_sheets_service():
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)
    return _sheets_service


def _fetch_price_rows():
    if not PRICES_SHEET_ID:
        return []
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=PRICES_SHEET_ID, range=PRICES_SHEET_DATA_RANGE
    ).execute()
    return result.get("values", [])


def _read_price_rows():
    # קאש משותף ל-load_prices ול-load_price_colors - בלעדיו כל טעינת כרטסת
    # לקוח הייתה קוראת את אותה לשונית Prices מגוגל פעמיים (אחת לכל פונקציה),
    # ובלי שום קאש בכלל - זו הייתה תרומה מרכזית לכרטסת שנפתחת לאט.
    now = datetime.datetime.utcnow()
    stale = (
        _price_rows_cache["rows"] is None
        or not _price_rows_cache["fetched_at"]
        or (now - _price_rows_cache["fetched_at"]).total_seconds() > CACHE_TTL_SECONDS
    )
    if stale:
        _price_rows_cache["rows"] = _fetch_price_rows()
        _price_rows_cache["fetched_at"] = now
    return _price_rows_cache["rows"]


def load_prices():
    if PRICES_SHEET_ID:
        prices = {}
        for row in _read_price_rows():
            if len(row) < 2:
                continue
            name = normalize_treatment_name(row[0])
            if not name:
                continue
            try:
                prices[name] = float(row[1])
            except ValueError:
                continue
        return prices

    with open(PRICES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_price_colors():
    """מיפוי טיפול -> colorId, לפי עמודה C בגיליון המחירון (שם צבע בעברית/אנגלית)."""
    colors = {}
    for row in _read_price_rows():
        if len(row) < 3:
            continue
        name = normalize_treatment_name(row[0])
        color_id = COLOR_NAME_TO_ID.get(row[2].strip().lower())
        if name and color_id:
            colors[name] = color_id
    return colors


def save_prices(prices):
    if PRICES_SHEET_ID:
        existing_colors = {}
        for row in _read_price_rows():
            if len(row) >= 3:
                name = normalize_treatment_name(row[0])
                if name:
                    existing_colors[name] = row[2]

        service = get_sheets_service()
        service.spreadsheets().values().clear(
            spreadsheetId=PRICES_SHEET_ID, range=PRICES_SHEET_DATA_RANGE
        ).execute()
        rows = [[name, price, existing_colors.get(name, "")] for name, price in sorted(prices.items())]
        if rows:
            service.spreadsheets().values().update(
                spreadsheetId=PRICES_SHEET_ID,
                range=PRICES_SHEET_WRITE_START,
                valueInputOption="RAW",
                body={"values": rows},
            ).execute()
        # מעדכנים את הקאש ישירות עם מה שבדיוק נכתב, כדי שה-GET שמגיע תכף אחרי
        # ה-redirect לא יצטרך לקרוא שוב מה-API.
        _price_rows_cache["rows"] = [[str(v) for v in row] for row in rows]
        _price_rows_cache["fetched_at"] = datetime.datetime.utcnow()
        return

    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2, sort_keys=True)


def _fetch_client_note_rows():
    if not PRICES_SHEET_ID:
        return []
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=PRICES_SHEET_ID, range=CLIENT_NOTES_SHEET_DATA_RANGE
    ).execute()
    return result.get("values", [])


def _read_client_note_rows():
    # בלי קאש, כל פתיחת כרטסת לקוח (GET) וכל שמירה (POST, שגם קוראת את כל
    # הלקוחות לפני הכתיבה) היו קוראות מחדש מגוגל עד 5000 שורות - עם ה-redirect
    # שמגיע אחרי שמירה, זה הכפיל את מספר הקריאות ל-API בלחיצת "שמירה" אחת.
    now = datetime.datetime.utcnow()
    stale = (
        _client_notes_rows_cache["rows"] is None
        or not _client_notes_rows_cache["fetched_at"]
        or (now - _client_notes_rows_cache["fetched_at"]).total_seconds() > CACHE_TTL_SECONDS
    )
    if stale:
        _client_notes_rows_cache["rows"] = _fetch_client_note_rows()
        _client_notes_rows_cache["fetched_at"] = now
    return _client_notes_rows_cache["rows"]


def load_client_notes():
    """מחזיר dict: (שם לקוח, תאריך YYYY-MM-DD) -> {treatment_type, details: [שורות פרטים]}."""
    notes = {}
    for row in _read_client_note_rows():
        if len(row) < 2:
            continue
        client = normalize_treatment_name(row[0])
        date = (row[1] or "").strip()
        if not client or not date:
            continue
        treatment_type = row[2] if len(row) > 2 else ""
        try:
            row_index = int(row[3]) if len(row) > 3 and row[3] != "" else 0
        except ValueError:
            row_index = 0
        detail = {
            "company": row[4] if len(row) > 4 else "",
            "type_detail": row[5] if len(row) > 5 else "",
            "number": row[6] if len(row) > 6 else "",
            "amount": row[7] if len(row) > 7 else "",
            "oxidant_type": row[8] if len(row) > 8 else "",
            "oxidant_amount": row[9] if len(row) > 9 else "",
        }
        entry = notes.setdefault((client, date), {"treatment_type": "", "details": []})
        if treatment_type:
            entry["treatment_type"] = treatment_type
        entry["details"].append((row_index, detail))

    for entry in notes.values():
        entry["details"] = [d for _, d in sorted(entry["details"], key=lambda t: t[0])]
    return notes


def save_client_notes(notes):
    """notes: dict (לקוח, תאריך) -> {treatment_type, details: [שורות פרטים]}.
    דריסה מלאה של הלשונית, כמו המחירון."""
    if not PRICES_SHEET_ID:
        return
    service = get_sheets_service()
    service.spreadsheets().values().clear(
        spreadsheetId=PRICES_SHEET_ID, range=CLIENT_NOTES_SHEET_DATA_RANGE
    ).execute()
    rows = []
    for (client, date), entry in sorted(notes.items()):
        treatment_type = entry.get("treatment_type", "")
        details = entry.get("details") or [{}]
        for idx, d in enumerate(details):
            rows.append([
                client, date, treatment_type, idx,
                d.get("company", ""), d.get("type_detail", ""), d.get("number", ""),
                d.get("amount", ""), d.get("oxidant_type", ""), d.get("oxidant_amount", ""),
            ])
    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=PRICES_SHEET_ID,
            range=CLIENT_NOTES_SHEET_WRITE_START,
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()
    # מעדכנים את הקאש ישירות עם מה שבדיוק נכתב (כולל המקרה של רשימה ריקה),
    # כדי שה-redirect אחרי שמירה לא יקרא שוב את כל הלקוחות מגוגל.
    _client_notes_rows_cache["rows"] = [[str(v) for v in row] for row in rows]
    _client_notes_rows_cache["fetched_at"] = datetime.datetime.utcnow()


def fetch_events():
    service = get_calendar_service()
    time_min = f"{HISTORY_START_DATE}T00:00:00Z"
    time_max = (datetime.datetime.utcnow() + datetime.timedelta(days=FUTURE_HORIZON_DAYS)).isoformat() + "Z"
    events = []
    page_token = None
    # רק השדות שבאמת בשימוש (ב-parse_event/compute_stats) - גוגל מחזיר ברירת
    # מחדל גם attendees/reminders/extendedProperties וכו' שלא נחוצים כאן בכלל
    # ומנפחים מאוד את כמות הנתונים בזיכרון עבור יומן עם הרבה היסטוריה.
    fields = "items(id,description,location,colorId,eventType,start),nextPageToken"
    while True:
        resp = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=2500,
            pageToken=page_token,
            fields=fields,
        ).execute()
        events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return events


def get_cached_events(force=False):
    """כמו fetch_events(), אבל עם cache משותף (CACHE_TTL_SECONDS) - כל מסך
    שצריך את היסטוריית היומן (דשבורד, לקוחות, כרטסת לקוח) קורא לזה במקום
    ל-fetch_events() ישירות, כדי לא לבצע שליפה כבדה מה-API בכל בקשה."""
    now = datetime.datetime.utcnow()
    stale = (
        not _events_cache["events"]
        or not _events_cache["fetched_at"]
        or (now - _events_cache["fetched_at"]).total_seconds() > CACHE_TTL_SECONDS
    )
    if force or stale:
        _events_cache["events"] = fetch_events()
        _events_cache["fetched_at"] = now
    return _events_cache["events"]


def parse_event(ev, color_id_to_treatment=None):
    """מפענח אירוע בודד ל-(לקוח, טיפול, dt), או None אם זה לא תור בר-חיוב.
    משותף בין compute_stats לבין עמודי כרטסת הלקוח, כדי ששני המקומות יזהו
    לקוח/טיפול באותה דרך בדיוק."""
    start = ev.get("start", {})
    start_str = start.get("dateTime") or start.get("date")
    if not start_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    # normalize_treatment_name (לא רק strip) - שתי רווחים בין שם פרטי למשפחה
    # בתיאור האירוע ביומן (טעות הקלדה נפוצה) היו יוצרים "לקוח" כפול ברשימה,
    # כי "דנה לוי" ו-"דנה  לוי" נשארו שני מחרוזות שונות.
    customer = normalize_treatment_name(ev.get("description"))
    treatment = (color_id_to_treatment or {}).get(ev.get("colorId"))
    if not treatment:
        treatment = normalize_treatment_name(ev.get("location"))
    if not customer or not treatment:
        return None
    return customer, treatment, dt


def compute_stats(events, prices, price_colors=None):
    normalized_prices = {normalize_treatment_name(name): price for name, price in prices.items()}
    color_id_to_treatment = {color_id: name for name, color_id in (price_colors or {}).items()}
    now = datetime.datetime.now(datetime.timezone.utc)

    monthly = defaultdict(lambda: defaultdict(lambda: {"visits": 0, "revenue": 0}))
    treatment_stats = defaultdict(lambda: {"count": 0, "revenue": 0})
    monthly_treatment_stats = defaultdict(lambda: defaultdict(lambda: {"count": 0, "revenue": 0}))
    monthly_totals = defaultdict(float)
    yearly_totals = defaultdict(float)
    future_monthly_totals = defaultdict(float)
    future_total = 0.0
    future_count = 0
    missing_prices = set()
    skipped = 0

    for ev in events:
        if ev.get("eventType") == "birthday":
            continue

        parsed = parse_event(ev, color_id_to_treatment)
        if not parsed:
            skipped += 1
            continue
        customer, treatment, dt = parsed

        price = normalized_prices.get(treatment)
        if price is None:
            missing_prices.add(treatment)
            price = 0

        if dt > now:
            month_key = dt.strftime("%Y-%m")
            future_monthly_totals[month_key] += price
            future_total += price
            future_count += 1
            continue

        month_key = dt.strftime("%Y-%m")
        year_key = dt.strftime("%Y")

        monthly[month_key][customer]["visits"] += 1
        monthly[month_key][customer]["revenue"] += price

        treatment_stats[treatment]["count"] += 1
        treatment_stats[treatment]["revenue"] += price

        monthly_treatment_stats[month_key][treatment]["count"] += 1
        monthly_treatment_stats[month_key][treatment]["revenue"] += price

        monthly_totals[month_key] += price
        yearly_totals[year_key] += price

    return {
        "monthly": monthly,
        "treatment_stats": treatment_stats,
        "monthly_treatment_stats": monthly_treatment_stats,
        "monthly_totals": monthly_totals,
        "yearly_totals": yearly_totals,
        "future_monthly_totals": future_monthly_totals,
        "future_total": future_total,
        "future_count": future_count,
        "missing_prices": sorted(missing_prices),
        "skipped": skipped,
        "total_events": len(events),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if DASHBOARD_PASSWORD and password == DASHBOARD_PASSWORD:
            session["authed"] = True
            session.permanent = True
            next_url = request.args.get("next") or url_for("app_home")
            # רק נתיב פנימי - לא מפנים לאתר אחר דרך ?next=
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("app_home")
            return redirect(next_url)
        flash("סיסמה שגויה")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/data")
@login_required
def api_data():
    force = request.args.get("refresh") == "1"
    now = datetime.datetime.utcnow()
    stale = (
        not _cache["data"]
        or not _cache["fetched_at"]
        or (now - _cache["fetched_at"]).total_seconds() > CACHE_TTL_SECONDS
    )
    if force or stale:
        events = get_cached_events(force=force)
        prices = load_prices()
        price_colors = load_price_colors()
        _cache["data"] = compute_stats(events, prices, price_colors)
        _cache["fetched_at"] = now

    payload = dict(_cache["data"])
    payload["fetched_at"] = _cache["fetched_at"].isoformat()
    return jsonify(payload)


@app.route("/prices", methods=["GET", "POST"])
@login_required
def prices_page():
    if request.method == "POST":
        orig_names = request.form.getlist("orig_name")
        names = request.form.getlist("name")
        values = request.form.getlist("price")
        deleted = set(request.form.getlist("delete_orig"))
        new_prices = {}
        for orig_name, name, value in zip(orig_names, names, values):
            if orig_name in deleted:
                continue
            name = normalize_treatment_name(name)
            if not name:
                continue
            try:
                new_prices[name] = float(value)
            except ValueError:
                continue

        new_name = normalize_treatment_name(request.form.get("new_name", ""))
        new_price = request.form.get("new_price", "").strip()
        if new_name and new_price:
            try:
                new_prices[new_name] = float(new_price)
            except ValueError:
                pass

        save_prices(new_prices)
        _cache["data"] = None
        flash("המחירון עודכן בהצלחה")
        return redirect(url_for("prices_page"))

    return render_template("prices.html", prices=load_prices())


@app.route("/clients")
@login_required
def clients_list():
    events = get_cached_events()
    color_id_to_treatment = {color_id: name for name, color_id in load_price_colors().items()}
    now = datetime.datetime.now(datetime.timezone.utc)

    names = set()
    for ev in events:
        if ev.get("eventType") == "birthday":
            continue
        parsed = parse_event(ev, color_id_to_treatment)
        if not parsed:
            continue
        customer, _treatment, dt = parsed
        if dt <= now:
            names.add(customer)

    return render_template("clients.html", names=sorted(names))


@app.route("/clients/frequency")
@login_required
def clients_frequency():
    events = get_cached_events()
    prices = load_prices()
    price_colors = load_price_colors()
    stats = compute_stats(events, prices, price_colors)
    monthly = stats["monthly"]

    months = sorted(monthly.keys())
    visits_by_client = defaultdict(dict)
    totals_by_client = defaultdict(int)
    for month, clients_in_month in monthly.items():
        for client, s in clients_in_month.items():
            visits_by_client[client][month] = s["visits"]
            totals_by_client[client] += s["visits"]

    rows = [
        {
            "name": client,
            "total": totals_by_client[client],
            "counts": [visits_by_client[client].get(m, 0) for m in months],
        }
        for client in sorted(totals_by_client, key=lambda c: (-totals_by_client[c], c))
    ]

    return render_template("client_frequency.html", months=months, rows=rows)


@app.route("/clients/<path:name>", methods=["GET", "POST"])
@login_required
def client_card(name):
    name = normalize_treatment_name(name)

    if request.method == "POST":
        all_notes = load_client_notes()
        all_notes = {key: entry for key, entry in all_notes.items() if key[0] != name}

        dates = request.form.getlist("date")
        treatment_types = request.form.getlist("treatment_type")
        treatment_type_by_date = {
            date.strip(): treatment_type.strip()
            for date, treatment_type in zip(dates, treatment_types)
            if date.strip()
        }

        detail_dates = request.form.getlist("detail_date")
        companies = request.form.getlist("company")
        type_details = request.form.getlist("type_detail")
        numbers = request.form.getlist("number")
        amounts = request.form.getlist("amount")
        detail_oxidant_types = request.form.getlist("oxidant_type")
        detail_oxidant_amounts = request.form.getlist("oxidant_amount")

        details_by_date = defaultdict(list)
        for detail_date, company, type_detail, number, amount, oxidant_type, oxidant_amount in zip(
            detail_dates, companies, type_details, numbers, amounts, detail_oxidant_types, detail_oxidant_amounts
        ):
            detail_date = detail_date.strip()
            detail = {
                "company": company.strip(),
                "type_detail": type_detail.strip(),
                "number": number.strip(),
                "amount": amount.strip(),
                "oxidant_type": oxidant_type.strip(),
                "oxidant_amount": oxidant_amount.strip(),
            }
            if detail_date and any(detail.values()):
                details_by_date[detail_date].append(detail)

        for date, treatment_type in treatment_type_by_date.items():
            details = details_by_date.get(date, [])
            if not treatment_type and not details:
                continue
            all_notes[(name, date)] = {"treatment_type": treatment_type, "details": details}

        save_client_notes(all_notes)
        flash("הכרטסת עודכנה בהצלחה")
        return redirect(url_for("client_card", name=name))

    events = get_cached_events()
    prices = load_prices()
    price_colors = load_price_colors()
    color_id_to_treatment = {color_id: n for n, color_id in price_colors.items()}
    normalized_prices = {normalize_treatment_name(n): p for n, p in prices.items()}
    notes = load_client_notes()
    now = datetime.datetime.now(datetime.timezone.utc)
    today = local_now().date()
    completed_visits = 0

    history = []
    for ev in events:
        if ev.get("eventType") == "birthday":
            continue
        parsed = parse_event(ev, color_id_to_treatment)
        if not parsed:
            continue
        customer, treatment, dt = parsed
        # כולל תור של היום גם אם השעה שלו עוד לא הגיעה - הלקוח כבר במספרה
        is_today = dt.astimezone(LOCAL_TZ).date() == today
        if customer != name or (dt > now and not is_today):
            continue
        date_key = dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
        if dt <= now:
            completed_visits += 1
        note = notes.get((name, date_key), {})
        history.append({
            "date": date_key,
            "date_display": dt.strftime("%d/%m/%Y"),
            "treatment": treatment,
            "price": normalized_prices.get(treatment, 0),
            # ברירת המחדל לסוג הטיפול היא מה שרשום ביומן לאותו ביקור - עם
            # אפשרות לשנות; ברגע שנשמר שינוי ידני, הוא גובר.
            "treatment_type": note.get("treatment_type") or treatment,
            "details": note.get("details") or [{}],
        })
    # תמיד יש שורה לביקור של היום (גם ללקוח שלא ביומן / בלי תור), כדי שאפשר
    # יהיה לרשום צבע וטיפול כשהוא מגיע.
    today_key = today.strftime("%Y-%m-%d")
    if not any(h["date"] == today_key for h in history):
        note = notes.get((name, today_key), {})
        history.append({
            "date": today_key,
            "date_display": today.strftime("%d/%m/%Y"),
            "treatment": "",
            "price": 0,
            "treatment_type": note.get("treatment_type", ""),
            "details": note.get("details") or [{}],
            "walk_in": True,
        })
    history.sort(key=lambda h: h["date"], reverse=True)

    phone = phone_for_name(name)
    next_appt = None
    for ev in events:
        if ev.get("eventType") == "birthday":
            continue
        parsed = parse_event(ev, color_id_to_treatment)
        if parsed and parsed[0] == name and parsed[2] > now:
            local = parsed[2].astimezone(LOCAL_TZ)
            if next_appt is None or local < next_appt["dt"]:
                next_appt = {"dt": local, "treatment": parsed[1]}
    if next_appt:
        next_appt["display"] = f"{HEB_DAYS[next_appt['dt'].weekday()]} {next_appt['dt'].strftime('%d/%m %H:%M')}"

    return render_template(
        "client_card.html",
        name=name,
        phone=phone,
        phone_display=format_phone(phone),
        loyalty=loyalty_for(completed_visits),
        completed_visits=completed_visits,
        today_key=today.strftime("%Y-%m-%d"),
        last_visit=next((h["date_display"] for h in history if h["date"] < today.strftime("%Y-%m-%d")), ""),
        next_appt=next_appt,
        history=history,
        treatment_types=sorted(prices.keys()),
        field_sets=TREATMENT_FIELD_SETS,
        field_set_default=TREATMENT_FIELD_SET_DEFAULT,
        multi_row_types=sorted(MULTI_ROW_TREATMENT_TYPES),
        sheet_configured=bool(PRICES_SHEET_ID),
    )


# ---------------------------------------------------------------------------
# אפליקציית המספרה: צ'ק-אין בברקוד, תורים של היום, כרטיסייה, התקנה בטלפון/אייפד
# ---------------------------------------------------------------------------

HEB_DAYS = ["יום ב'", "יום ג'", "יום ד'", "יום ה'", "יום ו'", "שבת", "יום א'"]


def normalize_phone(raw):
    """כל צורה של מספר (050-1234567, +972 50..., 972501234567@c.us) -> 972501234567."""
    d = re.sub(r"\D", "", str(raw or ""))
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0") and len(d) == 10:
        d = "972" + d[1:]
    return d if len(d) >= 9 else ""


def format_phone(phone):
    if phone.startswith("972") and len(phone) == 12:
        local = "0" + phone[3:]
        return f"{local[:3]}-{local[3:]}"
    return phone


def local_now():
    return datetime.datetime.now(LOCAL_TZ)


def ensure_tab(title):
    """יוצר לשונית (עם שורת כותרות) בגיליון אם היא עוד לא קיימת."""
    if title in _ensured_tabs or not PRICES_SHEET_ID:
        return
    service = get_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=PRICES_SHEET_ID, fields="sheets.properties.title").execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if title not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=PRICES_SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=PRICES_SHEET_ID, range=f"{title}!A1",
            valueInputOption="RAW", body={"values": [TAB_HEADERS[title]]},
        ).execute()
    _ensured_tabs.add(title)


def _read_tab_rows(cache, title, data_range, force=False):
    if not PRICES_SHEET_ID:
        return cache["rows"] or []
    now = datetime.datetime.utcnow()
    stale = (
        cache["rows"] is None
        or not cache["fetched_at"]
        or (now - cache["fetched_at"]).total_seconds() > CACHE_TTL_SECONDS
    )
    if force or stale:
        ensure_tab(title)
        result = get_sheets_service().spreadsheets().values().get(
            spreadsheetId=PRICES_SHEET_ID, range=data_range
        ).execute()
        cache["rows"] = result.get("values", [])
        cache["fetched_at"] = now
    return cache["rows"]


def _append_row(cache, title, row):
    if PRICES_SHEET_ID:
        ensure_tab(title)
        get_sheets_service().spreadsheets().values().append(
            spreadsheetId=PRICES_SHEET_ID, range=f"{title}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
    if cache["rows"] is None:
        cache["rows"] = []
        cache["fetched_at"] = datetime.datetime.utcnow()
    cache["rows"].append([str(v) for v in row])


def load_clients(force=False):
    """phone -> {"name", "row"} מתוך לשונית Clients (row = מספר השורה בגיליון)."""
    clients = {}
    rows = _read_tab_rows(_clients_rows_cache, CLIENTS_TAB, CLIENTS_SHEET_DATA_RANGE, force)
    for i, row in enumerate(rows):
        phone = normalize_phone(row[0] if row else "")
        name = normalize_treatment_name(row[1]) if len(row) > 1 else ""
        if phone and name:
            clients[phone] = {"name": name, "row": i + 2}
    return clients


def phone_for_name(name):
    for phone, c in load_clients().items():
        if c["name"] == name:
            return phone
    return ""


def upsert_client(phone, name, source):
    """שומר/מעדכן לקוח לפי טלפון. מחזיר (name, is_new)."""
    clients = load_clients()
    existing = clients.get(phone)
    if existing:
        if name and name != existing["name"]:
            if PRICES_SHEET_ID:
                get_sheets_service().spreadsheets().values().update(
                    spreadsheetId=PRICES_SHEET_ID, range=f"{CLIENTS_TAB}!B{existing['row']}",
                    valueInputOption="RAW", body={"values": [[name]]},
                ).execute()
            _clients_rows_cache["rows"][existing["row"] - 2][1:2] = [name]
            return name, False
        return existing["name"], False
    _append_row(_clients_rows_cache, CLIENTS_TAB, [phone, name, local_now().isoformat(timespec="seconds"), source])
    return name, True


def load_checkins_today():
    today = local_now().strftime("%Y-%m-%d")
    out = []
    for row in _read_tab_rows(_checkins_rows_cache, CHECKINS_TAB, CHECKINS_SHEET_DATA_RANGE):
        if len(row) < 3 or not row[0].startswith(today):
            continue
        out.append({"time": row[0], "phone": normalize_phone(row[1]), "name": row[2],
                    "is_new": len(row) > 3 and row[3] == "yes"})
    return out


def loyalty_for(visits):
    """visits = ביקורים שהסתיימו (לפי היומן). כל ביקור LOYALTY_VISITS הוא מתנה."""
    if LOYALTY_VISITS <= 0:
        return None
    stamps = visits % LOYALTY_VISITS
    return {
        "total": LOYALTY_VISITS,
        "visits": visits,
        "stamps": stamps,
        "next_is_gift": stamps == LOYALTY_VISITS - 1,
        "left": LOYALTY_VISITS - 1 - stamps,
    }


def visits_by_client(events, color_id_to_treatment):
    """שם -> מספר תורים שהסתיימו, תאריך אחרון."""
    now = datetime.datetime.now(datetime.timezone.utc)
    counts = defaultdict(lambda: {"visits": 0, "last": None})
    for ev in events:
        if ev.get("eventType") == "birthday":
            continue
        parsed = parse_event(ev, color_id_to_treatment)
        if not parsed or parsed[2] > now:
            continue
        c = counts[parsed[0]]
        c["visits"] += 1
        if c["last"] is None or parsed[2] > c["last"]:
            c["last"] = parsed[2]
    return counts


def base_url():
    return PUBLIC_BASE_URL or request.url_root.rstrip("/")


def card_path_for_phone(phone):
    return url_for("card_by_phone", phone=phone)


def checkin_wa_link():
    if not BUSINESS_WHATSAPP:
        return ""
    return f"https://wa.me/{BUSINESS_WHATSAPP}?text={urllib.parse.quote(CHECKIN_MESSAGE)}"


@app.route("/manifest.webmanifest")
def manifest():
    data = {
        "name": "חזי דרור - מספרה",
        "short_name": "חזי מספרה",
        "lang": "he",
        "dir": "rtl",
        "start_url": "/app",
        "scope": "/",
        "display": "standalone",
        "background_color": "#100e0c",
        "theme_color": "#100e0c",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    # Service worker מינימלי - מאפשר "התקנה" כאפליקציה. לא שומר נתוני לקוחות
    # במטמון: הכל נטען תמיד מהשרת.
    js = "self.addEventListener('install',e=>self.skipWaiting());" \
         "self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));" \
         "self.addEventListener('fetch',()=>{});"
    resp = Response(js, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/app")
@login_required
def app_home():
    events = get_cached_events()
    color_id_to_treatment = {cid: n for n, cid in load_price_colors().items()}
    counts = visits_by_client(events, color_id_to_treatment)
    clients = load_clients()
    by_name = {c["name"]: p for p, c in clients.items()}
    names = set(counts) | set(by_name)
    directory = []
    for n in sorted(names):
        phone = by_name.get(n, "")
        last = counts[n]["last"] if n in counts else None
        directory.append({
            "name": n,
            "phone": phone,
            "phone_display": format_phone(phone),
            "visits": counts[n]["visits"] if n in counts else 0,
            "last": last.astimezone(LOCAL_TZ).strftime("%d/%m/%y") if last else "",
            "url": url_for("client_card", name=n),
        })
    return render_template("app.html", directory=directory, loyalty_total=LOYALTY_VISITS,
                           has_checkin=bool(CHECKIN_SECRET), poster_ready=bool(BUSINESS_WHATSAPP))


@app.route("/api/today")
@login_required
def api_today():
    if request.args.get("refresh") == "1":
        _read_tab_rows(_checkins_rows_cache, CHECKINS_TAB, CHECKINS_SHEET_DATA_RANGE, force=True)
    events = get_cached_events(force=request.args.get("refresh") == "1")
    color_id_to_treatment = {cid: n for n, cid in load_price_colors().items()}
    counts = visits_by_client(events, color_id_to_treatment)
    clients = load_clients()
    phone_by_name = {c["name"]: p for p, c in clients.items()}
    today = local_now().date()
    checkins = load_checkins_today()
    arrived = {c["name"] for c in checkins}

    appts = []
    for ev in events:
        if ev.get("eventType") == "birthday":
            continue
        parsed = parse_event(ev, color_id_to_treatment)
        if not parsed:
            continue
        name, treatment, dt = parsed
        local = dt.astimezone(LOCAL_TZ)
        if local.date() != today:
            continue
        past = counts[name]["visits"] if name in counts else 0
        # תור של היום שכבר עבר נספר ב-past; הכרטיסייה מתייחסת לתור עצמו
        visit_no = past if dt <= datetime.datetime.now(datetime.timezone.utc) else past + 1
        appts.append({
            "time": local.strftime("%H:%M"),
            "name": name,
            "treatment": treatment,
            "arrived": name in arrived,
            "gift": bool(LOYALTY_VISITS) and visit_no > 0 and visit_no % LOYALTY_VISITS == 0,
            "url": url_for("client_card", name=name),
            "phone_display": format_phone(phone_by_name.get(name, "")),
        })
    appts.sort(key=lambda a: a["time"])

    out_checkins = []
    for c in reversed(checkins):
        n = c["name"]
        loyalty = loyalty_for(counts[n]["visits"] if n in counts else 0)
        out_checkins.append({
            "time": c["time"][11:16],
            "name": n,
            "phone_display": format_phone(c["phone"]),
            "is_new": c["is_new"],
            "next_is_gift": bool(loyalty and loyalty["next_is_gift"]),
            "stamps": loyalty["stamps"] if loyalty else None,
            "url": card_path_for_phone(c["phone"]),
        })
    return jsonify({"checkins": out_checkins, "appointments": appts,
                    "date": f"{HEB_DAYS[today.weekday()]} {today.strftime('%d/%m')}"})


@app.route("/api/latest-checkin")
@login_required
def api_latest_checkin():
    """הצ'ק-אין האחרון של היום - קריאה קלה מהזיכרון, שהמסך במספרה בודק כל כמה
    שניות כדי לפתוח לבד את הכרטסת של מי שסרק עכשיו."""
    checkins = load_checkins_today()
    if not checkins:
        return jsonify({"key": None})
    c = checkins[-1]
    return jsonify({
        "key": f"{c['time']}|{c['phone']}",
        "name": c["name"],
        "time": c["time"][11:16],
        "is_new": c["is_new"],
        "url": card_path_for_phone(c["phone"]),
        "age_sec": max(0, int((local_now() - datetime.datetime.fromisoformat(c["time"])).total_seconds())),
    })


@app.route("/c/<phone>")
@login_required
def card_by_phone(phone):
    phone = normalize_phone(phone)
    client = load_clients().get(phone)
    if client:
        return redirect(url_for("client_card", name=client["name"]))
    return redirect(url_for("new_client", phone=phone))


@app.route("/app/new", methods=["GET", "POST"])
@login_required
def new_client():
    error = ""
    phone_raw = request.values.get("phone", "")
    name = normalize_treatment_name(request.values.get("name", ""))
    if request.method == "POST":
        phone = normalize_phone(phone_raw)
        if not name:
            error = "צריך שם - בדיוק כמו שהוא נרשם בתיאור התור ביומן."
        elif not phone:
            error = "מספר הטלפון לא תקין. הקלידו 10 ספרות, לדוגמה 050-1234567."
        else:
            existing = load_clients().get(phone)
            if existing and existing["name"] != name:
                error = f"הטלפון הזה כבר שמור אצל {existing['name']}."
            else:
                upsert_client(phone, name, "app")
                flash(f"{name} נשמר/ה. רשמו את השם בדיוק כך בתיאור התור ביומן, כדי שההיסטוריה תתחבר לכרטסת.")
                return redirect(url_for("client_card", name=name))
    return render_template("new_client.html", phone=format_phone(normalize_phone(phone_raw)) or phone_raw,
                           name=name, error=error)


@app.route("/clients/<path:name>/phone", methods=["POST"])
@login_required
def set_client_phone(name):
    name = normalize_treatment_name(name)
    phone = normalize_phone(request.form.get("phone", ""))
    if not phone:
        flash("מספר הטלפון לא תקין.")
        return redirect(url_for("client_card", name=name))
    existing = load_clients().get(phone)
    if existing and existing["name"] != name:
        flash(f"הטלפון הזה כבר שמור אצל {existing['name']}.")
        return redirect(url_for("client_card", name=name))
    upsert_client(phone, name, "app")
    flash("הטלפון נשמר.")
    return redirect(url_for("client_card", name=name))


@app.route("/api/checkin", methods=["POST"])
def api_checkin():
    """נקרא מ-Make כשלקוח שולח את הודעת הברקוד בוואטסאפ. גוף JSON (או טופס):
    secret (או header X-Checkin-Secret), phone, ועוד:
    - name: השם מאנשי הקשר (כשנמצא איש קשר). הוא הקובע, ומעדכן את הגיליון.
    - whatsapp_name: השם של הלקוח בוואטסאפ - משמש רק ללקוח שלא מוכר בכלל.
    - fallback: true במסלול "לא נמצא באנשי הקשר". אם המסלול של "נמצא" כבר רשם
      את ההגעה הזו בדקות האחרונות, מחזירים skip=true ולא רושמים שוב.
    בתשובה: message (טקסט מוכן לשליחה לחזי), card_url, name, is_new, skip,
    create_contact (true = כדאי ליצור איש קשר בגוגל עם name ו-phone)."""
    data = request.get_json(silent=True) or request.form.to_dict()
    secret = request.headers.get("X-Checkin-Secret") or str(data.get("secret", ""))
    if not CHECKIN_SECRET or not hmac.compare_digest(secret.encode(), CHECKIN_SECRET.encode()):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    phone = normalize_phone(data.get("phone"))
    if not phone:
        return jsonify({"ok": False, "error": "bad_phone"}), 400
    contact_name = normalize_treatment_name(data.get("name"))
    whatsapp_name = normalize_treatment_name(data.get("whatsapp_name"))
    flag = lambda key: str(data.get(key, "")).lower() in ("1", "true", "yes")
    new_contact = flag("new_contact")
    fallback = flag("fallback")

    if fallback:
        cutoff = (local_now() - datetime.timedelta(minutes=3)).isoformat(timespec="seconds")
        recent = [r for r in _read_tab_rows(_checkins_rows_cache, CHECKINS_TAB, CHECKINS_SHEET_DATA_RANGE)
                  if len(r) > 1 and normalize_phone(r[1]) == phone and r[0] >= cutoff]
        if recent:
            return jsonify({"ok": True, "skip": True, "create_contact": False})

    existing = load_clients().get(phone)
    if contact_name:
        name, is_new = upsert_client(phone, contact_name, "contacts")
    elif existing and not (whatsapp_name and existing["name"].startswith("לקוח חדש")):
        name, is_new = existing["name"], False
    elif existing:
        # היה רק שם זמני ("לקוח חדש 05X-...") - מחליפים בשם שלו מהוואטסאפ
        name, is_new = upsert_client(phone, whatsapp_name, "checkin")
    else:
        name, is_new = upsert_client(phone, whatsapp_name or f"לקוח חדש {format_phone(phone)}", "checkin")
    # במסלול fallback הלקוח לא נמצא באנשי הקשר - מציעים ליצור אותו שם
    create_contact = fallback
    events = get_cached_events()
    color_id_to_treatment = {cid: n for n, cid in load_price_colors().items()}
    counts = visits_by_client(events, color_id_to_treatment)
    visits = counts[name]["visits"] if name in counts else 0
    loyalty = loyalty_for(visits)
    # "חדש" = איש קשר שנוצר עכשיו, או לקוח שלא מוכר לא בגיליון וגם לא ביומן.
    # לקוחה ותיקה עם היסטוריה ביומן שרק עכשיו נשמר לה טלפון אינה "חדשה".
    is_new = new_contact or (is_new and visits == 0)

    now = local_now()
    _append_row(_checkins_rows_cache, CHECKINS_TAB,
                [now.isoformat(timespec="seconds"), phone, name, "yes" if is_new else "no"])

    card_url = base_url() + card_path_for_phone(phone)
    lines = [f"✂️ {name} הגיע/ה למספרה ({now.strftime('%H:%M')})"]
    if is_new:
        lines.append("לקוח/ה חדש/ה - נוצרה כרטסת")
    elif loyalty:
        lines.append("🎁 הביקור הזה מתנה מהכרטיסייה!" if loyalty["next_is_gift"]
                     else f"כרטיסייה: {loyalty['stamps']}/{loyalty['total']}")
    lines.append(card_url)

    return jsonify({
        "ok": True, "skip": False, "create_contact": create_contact,
        "name": name, "phone": phone, "is_new": is_new, "visits": visits,
        "next_is_gift": bool(loyalty and loyalty["next_is_gift"]),
        "card_url": card_url, "app_url": base_url() + url_for("app_home"),
        "message": "\n".join(lines),
        # אותה הודעה, מוכנה להדבקה בתוך מחרוזת JSON ב-Make (שורות חדשות ומירכאות
        # מקודדות), כדי שגוף הבקשה ל-Green-API לא יישבר (ראו תקלה #10).
        "message_json": json.dumps("\n".join(lines), ensure_ascii=False)[1:-1],
    })


@app.route("/poster")
@login_required
def poster():
    link = checkin_wa_link()
    svg = ""
    if link:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, box_size=20, border=2)
        buf = io.BytesIO()
        img.save(buf)
        svg = buf.getvalue().decode("utf-8")
        svg = svg[svg.find("<svg"):]
    return render_template("poster.html", svg=svg, link=link, message=CHECKIN_MESSAGE)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
