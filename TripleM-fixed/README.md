# TripleM v13 – Student Mission Manager

## 🚀 הפעלה מקומית (Windows)

```bat
start.bat
```
פתח: http://localhost:5000

---

## ☁️ Deploy לענן — Render.com (חינם, ללא כרטיס אשראי)

### שלב 1: GitHub (חינם)
1. פתח חשבון ב-[github.com](https://github.com) אם אין לך
2. לחץ **New repository** → שם: `triplem` → Public → Create
3. העלה את כל הקבצים (drag & drop לאתר GitHub, או דרך git)

### שלב 2: Render (חינם)
1. פתח חשבון ב-[render.com](https://render.com) → **Sign up with GitHub**
2. לחץ **New +** → **Web Service**
3. בחר את ה-repo `triplem` שיצרת
4. Render מזהה את ה-Dockerfile אוטומטית
5. **Environment Variables** (חשוב!) → לחץ **Add Environment Variable**:
   - `ICAL_URL` = הלינק שלך מ-Moodle
6. לחץ **Create Web Service**
7. המתן 3-5 דקות לבנייה ← פעם ראשונה לוקח זמן (מתקין tesseract)
8. ✅ האפליקציה שלך חיה ב-`https://triplem-XXXX.onrender.com`

### ⚠️ מגבלת Free Tier
- האפליקציה "נרדמת" אחרי 15 דקות ללא שימוש → מתעוררת ב-30 שניות
- הנתונים (tasks שהוספת ידנית) יאבדו אחרי שינה → לחץ **סנכרן** להחזרת המשימות מ-Moodle

---

## 📱 גישה מהטלפון (ללא ענן, אותו WiFi)

1. **PC**: פתח cmd → הקלד `ipconfig` → מצא "IPv4 Address" → לדוגמה `192.168.1.50`
2. **טלפון**: פתח דפדפן → הקלד `http://192.168.1.50:5000`
3. ✅ עובד! (כל עוד ה-PC פועל ואותו WiFi)

---

## 🔢 WebWork — ייבוא מסך (שתי שיטות)

### שיטה א': OCR חינמי (ללא API key)
- מתקין אוטומטית עם האפליקציה (Tesseract)
- עובד ישירות בלי שום הגדרה
- טוב לצילומי מסך ברורים

### שיטה ב': AI חכם (עם Anthropic API key)
- יותר מדויק, מתמודד עם תמונות מטושטשות
- הגדרות ⚙️ → **Anthropic API Key** → הכנס מפתח
- מפתח חינמי + $5 קרדיט ב-[console.anthropic.com](https://console.anthropic.com)

**איך להשתמש:**
1. ב-WebWork → צלם מסך של רשימת ה-HW
2. TripleM → לחץ **WW** → בחר קורס → העלה תמונה → חלץ

---

## 📅 iCal מ-Moodle — איך מוצאים?

1. Moodle → **לוח שנה** (צד שמאל)
2. גלול למטה → **ייצא לוח שנה**
3. בחר "כל אירועים" + תאריכים → **קבל כתובת URL**
4. הכנס ב-TripleM → הגדרות ⚙️ → iCal URL → שמור → סנכרן

**האם מתעדכן?** כן! הלינק חי — האפליקציה מסנכרנת **כל 6 שעות** אוטומטית.
