import os
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
PRICES_FILE = os.path.join(os.path.dirname(__file__), "prices.json")

# scope הוא read-only בכוונה: גם אם מישהו ישנה קוד בטעות ויקרא ל-insert/update,
# בקשת הכתיבה תידחה ע"י גוגל ברמת ה-API - היומן לא יכול להשתנות מהאפליקציה הזו.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

_cache = {"data": None, "fetched_at": None}
CACHE_TTL_SECONDS = 300


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def load_prices():
    with open(PRICES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_prices(prices):
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2, sort_keys=True)


def get_calendar_service():
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_events():
    service = get_calendar_service()
    time_min = f"{HISTORY_START_DATE}T00:00:00Z"
    time_max = datetime.datetime.utcnow().isoformat() + "Z"
    events = []
    page_token = None
    while True:
        resp = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=2500,
            pageToken=page_token,
        ).execute()
        events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return events


def compute_stats(events, prices):
    normalized_prices = {name.strip(): price for name, price in prices.items()}

    monthly = defaultdict(lambda: defaultdict(lambda: {"visits": 0, "revenue": 0}))
    treatment_stats = defaultdict(lambda: {"count": 0, "revenue": 0})
    monthly_totals = defaultdict(float)
    yearly_totals = defaultdict(float)
    missing_prices = set()
    skipped = 0

    for ev in events:
        if ev.get("eventType") == "birthday":
            continue

        start = ev.get("start", {})
        start_str = start.get("dateTime") or start.get("date")
        if not start_str:
            continue
        try:
            dt = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        customer = (ev.get("description") or "").strip()
        treatment = (ev.get("location") or "").strip()
        if not customer or not treatment:
            skipped += 1
            continue

        price = normalized_prices.get(treatment)
        if price is None:
            missing_prices.add(treatment)
            price = 0

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
        events = fetch_events()
        prices = load_prices()
        _cache["data"] = compute_stats(events, prices)
        _cache["fetched_at"] = now

    payload = dict(_cache["data"])
    payload["fetched_at"] = _cache["fetched_at"].isoformat()
    return jsonify(payload)


@app.route("/prices", methods=["GET", "POST"])
@login_required
def prices_page():
    if request.method == "POST":
        names = request.form.getlist("name")
        values = request.form.getlist("price")
        new_prices = {}
        for name, value in zip(names, values):
            name = name.strip()
            if not name:
                continue
            try:
                new_prices[name] = float(value)
            except ValueError:
                continue

        new_name = request.form.get("new_name", "").strip()
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
