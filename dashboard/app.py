import os
import re
import json
import datetime
from collections import defaultdict
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
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
    ],
    "פסים": [
        {"key": "amount", "label": "כמות החומר"},
        {"key": "oxidant_type", "label": "סוג חמצן"},
        {"key": "oxidant_amount", "label": "כמות חמצן"},
    ],
}
TREATMENT_FIELD_SET_DEFAULT = [{"key": "type_detail", "label": "הערה"}]
# סוגי טיפול שמאפשרים כמה שורות פרטים (כפתור "הוסף שורה") - בעיקר צבע, כשיש
# ערבוב של כמה מספרי צבע באותו ביקור.
MULTI_ROW_TREATMENT_TYPES = {"צבע"}

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


def _read_price_rows():
    if not PRICES_SHEET_ID:
        return []
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=PRICES_SHEET_ID, range=PRICES_SHEET_DATA_RANGE
    ).execute()
    return result.get("values", [])


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
        return

    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2, sort_keys=True)


def _read_client_note_rows():
    if not PRICES_SHEET_ID:
        return []
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=PRICES_SHEET_ID, range=CLIENT_NOTES_SHEET_DATA_RANGE
    ).execute()
    return result.get("values", [])


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


def fetch_events():
    service = get_calendar_service()
    time_min = f"{HISTORY_START_DATE}T00:00:00Z"
    time_max = (datetime.datetime.utcnow() + datetime.timedelta(days=FUTURE_HORIZON_DAYS)).isoformat() + "Z"
    events = []
    page_token = None
    # רק השדות שבאמת בשימוש (ב-parse_event/compute_stats) - גוגל מחזיר ברירת
    # מחדל גם attendees/reminders/extendedProperties וכו' שלא נחוצים כאן בכלל
    # ומנפחים מאוד את כמות הנתונים בזיכרון עבור יומן עם הרבה היסטוריה.
    fields = "items(description,location,colorId,eventType,start),nextPageToken"
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

    customer = (ev.get("description") or "").strip()
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

        monthly_totals[month_key] += price
        yearly_totals[year_key] += price

    return {
        "monthly": monthly,
        "treatment_stats": treatment_stats,
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
            next_url = request.args.get("next") or url_for("dashboard")
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

    history = []
    for ev in events:
        if ev.get("eventType") == "birthday":
            continue
        parsed = parse_event(ev, color_id_to_treatment)
        if not parsed:
            continue
        customer, treatment, dt = parsed
        if customer != name or dt > now:
            continue
        date_key = dt.strftime("%Y-%m-%d")
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
    history.sort(key=lambda h: h["date"], reverse=True)

    return render_template(
        "client_card.html",
        name=name,
        history=history,
        treatment_types=sorted(prices.keys()),
        field_sets=TREATMENT_FIELD_SETS,
        field_set_default=TREATMENT_FIELD_SET_DEFAULT,
        multi_row_types=sorted(MULTI_ROW_TREATMENT_TYPES),
        sheet_configured=bool(PRICES_SHEET_ID),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
