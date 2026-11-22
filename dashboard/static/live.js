// מסך קבלה: כשלקוח סורק את הברקוד בכניסה, הכרטסת שלו נפתחת לבד על המסך.
// בודק כל 5 שניות את /api/latest-checkin (קריאה קלה מהזיכרון של השרת).
// בכרטסת לקוח עם שינויים שלא נשמרו - לא עוברים לבד, רק מציגים פס עם כפתור.
(function () {
  var MODE = (document.currentScript && document.currentScript.dataset.mode) || "app";
  var KEY_SEEN = "hezi-last-checkin";
  var KEY_AUTO = "hezi-auto-open";
  var POLL_MS = 5000;

  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  var auto = get(KEY_AUTO) !== "off";
  var dirty = false;
  var baseline = get(KEY_SEEN); // null = עוד לא ראינו כלום במכשיר הזה
  var first = true;

  document.addEventListener("input", function (e) {
    if (e.target && e.target.closest && e.target.closest("form")) dirty = true;
  });
  document.addEventListener("submit", function () { dirty = false; });

  // ---- פס התראה ----
  var bar = document.createElement("div");
  bar.id = "live-bar";
  bar.hidden = true;
  bar.setAttribute("role", "status");
  bar.style.cssText = "position:fixed;left:0;right:0;top:0;z-index:50;" +
    "padding:calc(12px + env(safe-area-inset-top,0px)) max(16px,env(safe-area-inset-right,0px)) 12px max(16px,env(safe-area-inset-left,0px));" +
    "background:var(--gold-grad,#e8bb5a);color:var(--on-accent,#1a1209);font-weight:800;font-size:17px;" +
    "display:none;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;box-shadow:0 4px 20px rgba(0,0,0,.4)";
  document.body.appendChild(bar);

  function showBar(c) {
    bar.innerHTML = "";
    var t = document.createElement("span");
    t.textContent = "✂️ " + c.name + " הגיע/ה (" + c.time + ")";
    var go = document.createElement("a");
    go.href = c.url;
    go.textContent = "פתח כרטסת";
    go.style.cssText = "background:#1a1209;color:#e8bb5a;padding:8px 16px;border-radius:10px;text-decoration:none";
    var x = document.createElement("button");
    x.type = "button";
    x.textContent = "✕";
    x.setAttribute("aria-label", "סגירה");
    x.style.cssText = "background:none;border:0;font-size:20px;cursor:pointer;color:inherit";
    x.onclick = function () { bar.hidden = true; bar.style.display = "none"; };
    var right = document.createElement("span");
    right.style.cssText = "display:flex;gap:8px;align-items:center";
    right.appendChild(go); right.appendChild(x);
    bar.appendChild(t); bar.appendChild(right);
    bar.hidden = false;
    bar.style.display = "flex";
  }

  function arrived(c) {
    set(KEY_SEEN, c.key);
    document.title = "✂️ " + c.name + " הגיע/ה";
    if (navigator.vibrate) { try { navigator.vibrate([150, 80, 150]); } catch (e) {} }
    var onThisCard = MODE === "card" && location.pathname === decodeURIComponent(c.url);
    if (onThisCard) return;
    if (auto && !(MODE === "card" && dirty)) {
      location.href = c.url;
    } else {
      showBar(c);
    }
  }

  function poll() {
    // בודקים גם כשהלשונית ברקע (מחשב עם הרבה לשוניות פתוחות). הדפדפן מאט
    // טיימרים ברקע, אז שם זה יכול לקחת עד דקה - בלשונית שבחזית זה 5 שניות.
    fetch("/api/latest-checkin", { headers: { "Accept": "application/json" }, cache: "no-store" })
      .then(function (r) { return r.redirected ? null : r.json(); })
      .then(function (c) {
        if (!c || !c.key) { first = false; return; }
        if (first && baseline === null) { set(KEY_SEEN, c.key); baseline = c.key; first = false; return; }
        first = false;
        if (c.key === get(KEY_SEEN)) return;
        // הגעה ישנה (המסך היה סגור כשהיא קרתה) - רק מסמנים שראינו, לא קופצים אליה
        if (c.age_sec > 180) { set(KEY_SEEN, c.key); return; }
        arrived(c);
      })
      .catch(function () {});
  }

  // ---- מתג "פתיחה אוטומטית" במסך הבית ----
  var toggle = document.getElementById("auto-open");
  if (toggle) {
    toggle.checked = auto;
    toggle.addEventListener("change", function () {
      auto = toggle.checked;
      set(KEY_AUTO, auto ? "on" : "off");
      keepAwake();
    });
  }

  // מסך האייפד לא נכבה כל עוד הפתיחה האוטומטית פעילה והדף פתוח
  var lock = null;
  function keepAwake() {
    if (!auto || !("wakeLock" in navigator) || document.hidden) return;
    navigator.wakeLock.request("screen").then(function (l) { lock = l; }).catch(function () {});
  }
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { keepAwake(); poll(); }
  });

  keepAwake();
  poll();
  setInterval(poll, POLL_MS);
})();
