# דשבורד הכנסות - חזי עיצוב שיער

דף דשבורד פרטי ומעוצב, מתעדכן אוטומטית, שקורא נתונים מיומן Google Calendar
ומחשב הכנסות לפי טיפול ולפי לקוח - **בלי לכתוב שום דבר חזרה ליומן**, ועם
כניסה בסיסמה כך שרק אתה יכול לגשת אליו.

זהו פרויקט נפרד לגמרי מדף הנחיתה שבשורש הריפו - כל הפקודות למטה מריצים
מתוך התיקייה `dashboard/` (`cd dashboard` קודם).

## איך זה עומד בשתי הדרישות שלך

1. **שום דבר לא נכתב ליומן** - האפליקציה מתחברת ל-Google Calendar עם
   הרשאת `calendar.readonly` בלבד. זו לא רק בחירת קוד - ברמת ה-API של
   גוגל, כל ניסיון כתיבה (אפילו בטעות) פשוט יידחה, כי ההרשאה שקיבלנו
   מגוגל היא קריאה בלבד. בנוסף מומלץ גם לשתף את היומן עם חשבון השירות
   בהרשאת "כל פרטי האירועים" (לא "עריכת אירועים") - כך יש הגנה כפולה.

2. **גישה רק לך** - הדשבורד מוגן בסיסמה שרק אתה קובע ויודע (`DASHBOARD_PASSWORD`
   בקובץ `.env`). בלי הסיסמה אי אפשר לראות שום נתון.

## מבנה הנתונים ביומן (כמו שכבר עובד אצלך)

- **תיאור (Description)** של האירוע = שם הלקוח
- **מיקום (Location)** של האירוע = סוג הטיפול

זה בדיוק אותו מוסכם שכבר בנוי אצלך ב-Make עבור הודעות הוואטסאפ, כך שאין
צורך לשנות שום הרגל עבודה - ממשיכים לרשום בדיוק כמו היום.

## מחירון

מחיר לכל סוג טיפול, ניתן לעריכה נוחה מתוך עמוד "מחירון" בדשבורד עצמו.
חשוב שהשם יהיה זהה בדיוק לטקסט שנכתב בשדה Location ביומן. אם מופיע
ביומן טיפול שאין לו מחיר מוגדר - הדשבורד יזהיר על כך למעלה, ויספור אותו
כ-0 עד שתשלים את המחיר.

**חשוב על Render (תוכנית חינמית):** קובץ מקומי (`prices.json`) לא שורד
הפעלה-מחדש של השרת (Render "מוחק" קבצים מקומיים בכל פעם שהשירות נרדם
ומתעורר, לא רק בדחיפות קוד). לכן כשמריצים על Render **חובה** להגדיר
גיליון Google Sheets למחירון (ראו שלב 3 בהקמה למטה) - כך העריכות נשמרות
לצמיתות. בהרצה מקומית בלבד (על המחשב שלך) אפשר להסתפק בקובץ המקומי.

## הקמה (פעם אחת)

### 1. יצירת Service Account בגוגל (לקריאה בלבד מהיומן)

1. כנסו ל-https://console.cloud.google.com ופתחו פרויקט חדש (או קיים).
2. תפריט "APIs & Services" > "Library" > חפשו "Google Calendar API" > Enable.
3. תפריט "APIs & Services" > "Credentials" > "Create Credentials" >
   "Service Account". תנו שם (למשל `hezi-dashboard-reader`) וסיימו.
4. בתוך חשבון השירות שנוצר: לשונית "Keys" > "Add Key" > "Create new key"
   > JSON. יורד קובץ - שמרו אותו בתיקיית הפרויקט בשם `service_account.json`
   (אל תעלו את הקובץ הזה לשום מקום ציבורי!).
5. העתיקו את כתובת המייל של חשבון השירות (נראית כמו
   `hezi-dashboard-reader@your-project.iam.gserviceaccount.com`).

### 2. שיתוף היומן עם חשבון השירות (קריאה בלבד)

1. פתחו את Google Calendar בדפדפן > הגדרות היומן (hairstylinghd@gmail.com).
2. "Share with specific people" > הוסיפו את כתובת המייל של חשבון השירות
   מהשלב הקודם.
3. הרשאה: **"See all event details"** (לא "Make changes to events"!).
   ככה גם ברמת גוגל אין שום אפשרות כתיבה, לא רק בקוד.

### 3. גיליון Google Sheets למחירון (נדרש להרצה על Render)

1. הפעילו גם את **Google Sheets API** (כמו שהפעלתם את Calendar API בשלב
   1: "APIs & Services" > "Library" > חפשו "Google Sheets API" > Enable).
2. צרו גיליון חדש ב-sheets.google.com. שנו את שם הלשונית התחתונה
   (הכרטיסייה) מ-"Sheet1" ל-**"Prices"** (בדיוק ככה, אנגלית).
3. בתא A1 כתבו `טיפול` ובתא B1 כתבו `מחיר` (שורת כותרת - לא חובה אבל
   נוח). משורה 2 ואילך אפשר כבר להזין טיפולים ומחירים בעצמכם, או
   להשאיר ריק ולמלא מתוך הדשבורד.
4. שתפו את הגיליון (כפתור "Share" למעלה מימין) עם כתובת המייל של אותו
   חשבון שירות מהשלב הקודם (`hezi-dashboard-reader@...`), הפעם עם
   הרשאת **"Editor"** (לא Viewer - כאן כן צריך כתיבה, זה גיליון נפרד
   מהיומן ולא נוגע אליו).
5. העתיקו את ה-ID של הגיליון מתוך כתובת הדפדפן - החלק הארוך שבין
   `/d/` ל-`/edit`, למשל בכתובת
   `docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit` ה-ID הוא
   `1AbCdEfGhIjKlMnOp`.

כעת בחרו אחת משתי הדרכים להרצה בפועל: פריסה ל-Render.com (מומלץ, בלי
טרמינל) או הרצה מקומית על המחשב.

## פריסה ל-Render.com (בלי טרמינל, הכל דרך האתר)

זו הדרך המומלצת אם לא נוח לך עם שורת פקודה במחשב. כל השלבים דרך דפדפן:

1. כנסו ל-https://render.com > "Get Started" > התחברות עם חשבון GitHub
   (אותו חשבון שבו נמצא הריפו `hezidror/hezidror`).
2. "New +" > **"Web Service"**.
3. בחרו את הריפו `hezidror/hezidror` מהרשימה (אם לא מופיע - "Configure
   account" ותנו ל-Render גישה לריפו).
4. במסך ההגדרות:
   - **Name**: `hezi-dashboard` (או כל שם)
   - **Root Directory**: `dashboard`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Instance Type**: Free
5. גללו למטה ל-**"Environment Variables"** > "Add Environment Variable",
   והוסיפו אחד-אחד:
   - `DASHBOARD_PASSWORD` = סיסמה שרק אתה תדע
   - `SECRET_KEY` = מחרוזת אקראית ארוכה (אפשר להמציא לבד)
   - `GOOGLE_CALENDAR_ID` = `hairstylinghd@gmail.com`
   - `HISTORY_START_DATE` = `2024-01-01`
   - `GOOGLE_SERVICE_ACCOUNT_FILE` = `/etc/secrets/service_account.json`
   - `GOOGLE_PRICES_SHEET_ID` = ה-ID מהגיליון בשלב 3 למעלה (**חובה על
     Render**, אחרת עריכות מחירון יימחקו כשהשירות נרדם ומתעורר)
6. גללו ל-**"Secret Files"** > "Add Secret File":
   - **Filename**: `service_account.json`
   - **Contents**: פתחו את קובץ ה-JSON שהורדתם משלב 1 בעורך טקסט, והדביקו
     את כל התוכן שלו כאן.
7. לחצו **"Create Web Service"** למטה. Render יתחיל לבנות ולהריץ את
   הדשבורד - לוקח כ-2-3 דקות. בסיום תקבלו כתובת קבועה בסגנון
   `https://hezi-dashboard.onrender.com` - זו הכתובת של הדשבורד הפרטי
   שלך, נגישה מכל מקום (מחשב או נייד) עם HTTPS, עדיין מוגנת בסיסמה בלבד.

הערה: בתוכנית החינמית של Render השירות "נרדם" אחרי 15 דקות ללא שימוש,
וטעינה ראשונה אחרי תקופת שינה לוקחת כ-30 שניות - זה תקין, לא תקלה.

## הרצה מקומית (חלופה, דורשת טרמינל)

```bash
cd dashboard
cp .env.example .env
```

ואז עורכים את `.env`: `DASHBOARD_PASSWORD`, `SECRET_KEY` (מחרוזת אקראית -
אפשר להריץ `python3 -c "import secrets; print(secrets.token_hex(32))"`),
`GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_FILE` (נתיב לקובץ ה-JSON
מהשלב הקודם), `HISTORY_START_DATE`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

הדשבורד ירוץ בכתובת http://127.0.0.1:5000 - נגיש רק מהמחשב שלך.

## הערה על "הכנסות"

הדשבורד סופר הכנסה רק מתורים שכבר **קרו** (הזמן שלהם כבר עבר), לא תורים
עתידיים שרק נקבעו - כדי שהמספרים ישקפו הכנסה בפועל ולא הבטחות עתידיות.
