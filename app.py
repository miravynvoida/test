import os, hmac, hashlib, json, sqlite3, urllib.parse
from datetime import datetime, date, timedelta
from pathlib import Path

from aiohttp import web

BASE = Path(__file__).resolve().parent
DB_PATH = os.getenv("DB_PATH", str(BASE / "data" / "bot.db"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"
DEV_TELEGRAM_ID = int(os.getenv("DEV_TELEGRAM_ID", "0") or 0)

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def migrate():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS journal_settings (
        student_id INTEGER PRIMARY KEY,
        include_in_journal INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS marks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        discipline_id INTEGER NOT NULL,
        value TEXT NOT NULL,
        mark_date TEXT NOT NULL,
        lesson_no INTEGER,
        comment TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
        FOREIGN KEY(discipline_id) REFERENCES disciplines(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_marks_student_disc_date ON marks(student_id, discipline_id, mark_date);
    CREATE TABLE IF NOT EXISTS mini_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT DEFAULT '',
        student_id INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
    );
    """)
    c.execute("""INSERT OR IGNORE INTO journal_settings(student_id, include_in_journal)
                 SELECT id, 1 FROM students""")
    c.commit(); c.close()

def verify_init_data(init_data: str):
    if DEV_MODE and DEV_TELEGRAM_ID:
        c=db(); s=c.execute("SELECT * FROM students WHERE telegram_id=?", (DEV_TELEGRAM_ID,)).fetchone(); c.close()
        if s: return dict(s)
    if not BOT_TOKEN or not init_data:
        return None
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        recv = params.pop("hash", None)
        if not recv: return None
        data_check = "\n".join(f"{k}={params[k]}" for k in sorted(params))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, recv): return None
        user = json.loads(params.get("user", "{}"))
        tg_id = int(user.get("id", 0))
        c=db(); s=c.execute("SELECT * FROM students WHERE telegram_id=?", (tg_id,)).fetchone(); c.close()
        return dict(s) if s else None
    except Exception:
        return None

@web.middleware
async def auth_middleware(request, handler):
    if request.path.startswith("/api/"):
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        student = verify_init_data(init_data)
        if not student:
            return web.json_response({"error":"Не удалось подтвердить Telegram-пользователя."}, status=401)
        request["student"] = student
        request["is_admin"] = int(student["telegram_id"]) in ADMIN_IDS
    return await handler(request)

def nowstr(): return datetime.now().astimezone().isoformat(timespec="seconds")

def disciplines():
    c=db()
    rows=c.execute("SELECT id,name,emoji FROM disciplines WHERE active=1 ORDER BY name").fetchall()
    c.close()
    return [dict(x) for x in rows]

def marks_for(student_id, discipline_id=None, start=None, end=None):
    c=db()
    q="""SELECT m.*, d.name discipline_name, d.emoji
         FROM marks m JOIN disciplines d ON d.id=m.discipline_id
         WHERE m.student_id=?"""
    args=[student_id]
    if discipline_id:
        q += " AND m.discipline_id=?"; args.append(discipline_id)
    if start: q += " AND m.mark_date>=?"; args.append(start)
    if end: q += " AND m.mark_date<=?"; args.append(end)
    q += " ORDER BY m.mark_date DESC, m.id DESC"
    rows=[dict(x) for x in c.execute(q,args).fetchall()]
    c.close(); return rows

def avg(values):
    nums=[int(x["value"]) for x in values if x["value"] in {"2","3","4","5"}]
    return round(sum(nums)/len(nums),2) if nums else None

def mark_stats(rows):
    nums=[int(x["value"]) for x in rows if x["value"] in {"2","3","4","5"}]
    return {
        "average": round(sum(nums)/len(nums),2) if nums else None,
        "count": len(nums),
        "missed": sum(x["value"]=="Н" for x in rows),
        "sick": sum(x["value"]=="Б" for x in rows),
        "late": sum(x["value"]=="О" for x in rows),
        "total_attendance_events": sum(x["value"] in {"Н","Б","О"} for x in rows)
    }

async def index(request):
    return web.FileResponse(BASE/"static/index.html")

async def static_file(request):
    p=BASE/"static"/request.match_info["name"]
    if not p.exists() or not p.is_file(): raise web.HTTPNotFound()
    return web.FileResponse(p)

async def api_me(request):
    s=request["student"]
    c=db()
    include=c.execute("SELECT include_in_journal FROM journal_settings WHERE student_id=?", (s["id"],)).fetchone()
    c.close()
    allm=marks_for(s["id"])
    st=mark_stats(allm)
    return web.json_response({
        "student": s, "is_admin": request["is_admin"],
        "journal_enabled": bool(include["include_in_journal"]) if include else True,
        "stats": st
    })

async def api_disciplines(request):
    return web.json_response({"disciplines":disciplines()})

async def api_schedule(request):
    d=request.query.get("date", date.today().isoformat())
    c=db()
    rows=c.execute("""SELECT l.*, d.name discipline, d.emoji, COALESCE(t.name,'') teacher
        FROM schedule_days sd JOIN schedule_lessons l ON l.schedule_day_id=sd.id
        JOIN disciplines d ON d.id=l.discipline_id
        LEFT JOIN teachers t ON t.discipline_id=d.id
        WHERE sd.day=? ORDER BY l.lesson_no""",(d,)).fetchall()
    c.close()
    return web.json_response({"date":d,"lessons":[dict(x) for x in rows]})

async def api_homework(request):
    c=db()
    rows=c.execute("""SELECT h.id,h.discipline_id,h.text,h.explanation,h.published_date,h.due_date,
        h.hidden,h.archived,d.name discipline,d.emoji,
        he.title
        FROM homework h JOIN disciplines d ON d.id=h.discipline_id
        LEFT JOIN homework_extra he ON he.homework_id=h.id
        WHERE h.hidden=0 AND h.archived=0
        ORDER BY h.published_date ASC,h.id ASC""").fetchall()
    c.close()
    return web.json_response({"items":[dict(x) for x in rows]})

async def api_grades(request):
    s=request["student"]["id"]
    start=request.query.get("start"); end=request.query.get("end")
    rows=marks_for(s,start=start,end=end)
    ds=disciplines()
    out=[]
    for d in ds:
        r=[x for x in rows if x["discipline_id"]==d["id"]]
        if r:
            st=mark_stats(r); out.append({**d,**st})
    allst=mark_stats(rows)
    return web.json_response({"disciplines":out,"overall":allst,"marks":rows})

async def api_grade_detail(request):
    s=request["student"]["id"]; did=int(request.match_info["discipline_id"])
    start=request.query.get("start"); end=request.query.get("end")
    c=db(); d=c.execute("SELECT id,name,emoji FROM disciplines WHERE id=?", (did,)).fetchone(); c.close()
    if not d: raise web.HTTPNotFound()
    rows=marks_for(s,did,start,end)
    return web.json_response({"discipline":dict(d),"stats":mark_stats(rows),"marks":rows})

async def api_events(request):
    s=request["student"]["id"]
    c=db()
    rows=c.execute("""SELECT id,event_type,title,body,created_at FROM mini_events
        WHERE student_id IS NULL OR student_id=? ORDER BY created_at DESC LIMIT 50""",(s,)).fetchall()
    c.close()
    return web.json_response({"events":[dict(x) for x in rows]})

def require_admin(request):
    if not request["is_admin"]: raise web.HTTPForbidden(text="Только для администраторов")

async def api_admin_students(request):
    require_admin(request)
    c=db()
    rows=c.execute("""SELECT s.id,s.full_name,s.telegram_id,
        COALESCE(js.include_in_journal,1) include_in_journal
        FROM students s LEFT JOIN journal_settings js ON js.student_id=s.id
        ORDER BY s.full_name""").fetchall()
    c.close()
    return web.json_response({"students":[dict(x) for x in rows]})

async def api_admin_students_toggle(request):
    require_admin(request)
    sid=int(request.match_info["student_id"]); data=await request.json()
    c=db(); c.execute("""INSERT INTO journal_settings(student_id,include_in_journal) VALUES(?,?)
        ON CONFLICT(student_id) DO UPDATE SET include_in_journal=excluded.include_in_journal""",
        (sid,1 if data.get("include_in_journal") else 0)); c.commit(); c.close()
    return web.json_response({"ok":True})

async def api_admin_grade_book(request):
    require_admin(request)
    did=int(request.match_info["discipline_id"])
    start=request.query.get("start"); end=request.query.get("end")
    c=db()
    ds=c.execute("SELECT id,name,emoji FROM disciplines WHERE id=?",(did,)).fetchone()
    students=c.execute("""SELECT s.id,s.full_name,COALESCE(js.include_in_journal,1) include_in_journal
        FROM students s LEFT JOIN journal_settings js ON js.student_id=s.id
        WHERE COALESCE(js.include_in_journal,1)=1 ORDER BY s.full_name""").fetchall()
    marks={}
    q="""SELECT student_id,value,mark_date,lesson_no FROM marks WHERE discipline_id=?"""
    args=[did]
    if start: q+=" AND mark_date>=?"; args.append(start)
    if end: q+=" AND mark_date<=?"; args.append(end)
    for r in c.execute(q,args).fetchall(): marks.setdefault(r["student_id"],[]).append(dict(r))
    c.close()
    data=[]
    for s in students:
        r=marks.get(s["id"],[])
        data.append({**dict(s),"marks":r,"stats":mark_stats(r)})
    return web.json_response({"discipline":dict(ds) if ds else None,"students":data})

async def api_admin_add_mark(request):
    require_admin(request)
    data=await request.json()
    sid=int(data["student_id"]); did=int(data["discipline_id"]); value=str(data["value"]).strip().upper()
    if value not in {"2","3","4","5","Н","Б","О"}: raise web.HTTPBadRequest(text="Недопустимая отметка")
    md=data.get("mark_date") or date.today().isoformat()
    c=db()
    c.execute("""INSERT INTO marks(student_id,discipline_id,value,mark_date,lesson_no,comment,created_at)
                 VALUES(?,?,?,?,?,?,?)""",(sid,did,value,md,data.get("lesson_no"),data.get("comment",""),nowstr()))
    c.commit(); c.close()
    c=db(); name=c.execute("SELECT full_name FROM students WHERE id=?",(sid,)).fetchone(); dn=c.execute("SELECT name FROM disciplines WHERE id=?",(did,)).fetchone(); c.close()
    c=db(); c.execute("INSERT INTO mini_events(event_type,title,body,created_at) VALUES(?,?,?,?)",
                      ("grade",f"Новая отметка: {dn['name']}",f"{name['full_name']} — {value}",nowstr())); c.commit(); c.close()
    return web.json_response({"ok":True})

async def api_admin_schedule_day(request):
    require_admin(request)
    d=request.query.get("date",date.today().isoformat())
    c=db()
    rows=c.execute("""SELECT l.id,l.lesson_no,l.start,l.end,l.discipline_id,l.lesson_type,l.room,d.name discipline,d.emoji
        FROM schedule_days sd JOIN schedule_lessons l ON l.schedule_day_id=sd.id
        JOIN disciplines d ON d.id=l.discipline_id WHERE sd.day=? ORDER BY l.lesson_no""",(d,)).fetchall()
    c.close(); return web.json_response({"date":d,"lessons":[dict(x) for x in rows]})

async def api_admin_schedule_save(request):
    require_admin(request)
    data=await request.json(); d=data["date"]; lessons=data.get("lessons",[])
    c=db(); now=nowstr()
    row=c.execute("SELECT id FROM schedule_days WHERE day=?",(d,)).fetchone()
    if row: day_id=row["id"]; c.execute("UPDATE schedule_days SET updated_at=? WHERE id=?",(now,day_id))
    else:
        cur=c.execute("INSERT INTO schedule_days(day,created_at,updated_at) VALUES(?,?,?)",(d,now,now)); day_id=cur.lastrowid
    c.execute("DELETE FROM schedule_lessons WHERE schedule_day_id=?",(day_id,))
    for i,x in enumerate(lessons,1):
        c.execute("""INSERT INTO schedule_lessons(schedule_day_id,lesson_no,start,end,discipline_id,lesson_type,room)
                     VALUES(?,?,?,?,?,?,?)""",(day_id,i,x["start"],x["end"],int(x["discipline_id"]),x.get("lesson_type",""),x.get("room","")))
    c.commit(); c.close()
    c=db(); c.execute("INSERT INTO mini_events(event_type,title,body,created_at) VALUES(?,?,?,?)",
                      ("schedule","Изменено расписание",f"Изменения на {d}",nowstr())); c.commit(); c.close()
    return web.json_response({"ok":True})

async def api_admin_overview(request):
    require_admin(request)
    c=db()
    students=c.execute("SELECT COUNT(*) n FROM students").fetchone()["n"]
    included=c.execute("SELECT COUNT(*) n FROM students s LEFT JOIN journal_settings j ON j.student_id=s.id WHERE COALESCE(j.include_in_journal,1)=1").fetchone()["n"]
    marks=c.execute("SELECT COUNT(*) n FROM marks").fetchone()["n"]
    c.close()
    return web.json_response({"students":students,"included":included,"marks":marks})

routes=[
    web.get("/",index),
    web.get("/static/{name}",static_file),
    web.get("/api/me",api_me),
    web.get("/api/disciplines",api_disciplines),
    web.get("/api/schedule",api_schedule),
    web.get("/api/homework",api_homework),
    web.get("/api/grades",api_grades),
    web.get("/api/grades/{discipline_id}",api_grade_detail),
    web.get("/api/events",api_events),
    web.get("/api/admin/students",api_admin_students),
    web.post("/api/admin/students/{student_id}/toggle",api_admin_students_toggle),
    web.get("/api/admin/grades/{discipline_id}",api_admin_grade_book),
    web.post("/api/admin/grades",api_admin_add_mark),
    web.get("/api/admin/schedule",api_admin_schedule_day),
    web.post("/api/admin/schedule",api_admin_schedule_save),
    web.get("/api/admin/overview",api_admin_overview),
]

migrate()
app=web.Application(middlewares=[auth_middleware])
app.add_routes(routes)

if __name__=="__main__":
    web.run_app(app, host=os.getenv("HOST","0.0.0.0"), port=int(os.getenv("PORT","8080")))
