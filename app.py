import os, hmac, hashlib, json, sqlite3, urllib.parse, asyncio
from datetime import datetime, date, timedelta
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE / "data" / "bot.db")))
if not DB_PATH.is_absolute():
    DB_PATH = BASE / DB_PATH

def ensure_base_database():
    """Ensure Bothost's persistent DB starts from the supplied test database.
    The bundled seed is kept separate from the persistent DB so a pre-created
    empty SQLite file cannot hide the real schema.
    """
    import shutil
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    seed = BASE / "seed.db"
    if not seed.exists():
        raise RuntimeError("seed.db is missing from the deployment package")
    try:
        conn = sqlite3.connect(DB_PATH)
        has_students = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='students'"
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        has_students = None
    if not has_students:
        if DB_PATH.exists():
            DB_PATH.unlink()
        shutil.copy2(seed, DB_PATH)
        print(f"Seed database copied to {DB_PATH}")

ensure_base_database()
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
    CREATE TABLE IF NOT EXISTS journal_columns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline_id INTEGER NOT NULL,
        column_date TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(discipline_id, column_date),
        FOREIGN KEY(discipline_id) REFERENCES disciplines(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_journal_columns_disc ON journal_columns(discipline_id, column_date);
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
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='students'").fetchone():
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
async def access_log_middleware(request, handler):
    try:
        response = await handler(request)
        print(f"HTTP {request.method} {request.path} -> {response.status}")
        return response
    except Exception as exc:
        print(f"HTTP {request.method} {request.path} -> ERROR {type(exc).__name__}: {exc}")
        raise

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

async def health(request):
    return web.json_response({"status": "ok", "service": "r261-mini-app"})

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
        WHERE student_id=? OR (student_id IS NULL AND event_type IN ('schedule','homework','system'))
        ORDER BY created_at DESC LIMIT 50""",(s,)).fetchall()
    c.close()
    return web.json_response({"events":[dict(x) for x in rows]})

async def api_homework_material(request):
    hid=int(request.match_info['homework_id']); uid=int(request['student']['telegram_id'])
    c=db(); media=c.execute("SELECT kind,file_id,caption FROM homework_media WHERE homework_id=? ORDER BY id",(hid,)).fetchall(); h=c.execute("SELECT id FROM homework WHERE id=? AND hidden=0 AND archived=0",(hid,)).fetchone(); c.close()
    if not h: raise web.HTTPNotFound()
    if not media: return web.json_response({'ok':False,'message':'У этого задания нет прикреплённых материалов.'})
    if not BOT_TOKEN: raise web.HTTPInternalServerError(text='BOT_TOKEN не настроен')
    import aiohttp
    sent=0
    async with aiohttp.ClientSession() as session:
        for x in media:
            if x['kind']=='photo': method='sendPhoto'; field='photo'
            elif x['kind']=='video': method='sendVideo'; field='video'
            else: method='sendDocument'; field='document'
            payload={'chat_id':str(uid),field:x['file_id']}
            if x['caption']: payload['caption']=x['caption']
            async with session.post(f'https://api.telegram.org/bot{BOT_TOKEN}/{method}',json=payload) as resp:
                if resp.status==200: sent+=1
    return web.json_response({'ok':sent>0,'sent':sent})

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
    did=int(request.match_info['discipline_id'])
    c=db()
    ds=c.execute("SELECT id,name,emoji FROM disciplines WHERE id=?",(did,)).fetchone()
    students=c.execute("""SELECT s.id,s.full_name,COALESCE(js.include_in_journal,1) include_in_journal
        FROM students s LEFT JOIN journal_settings js ON js.student_id=s.id
        WHERE COALESCE(js.include_in_journal,1)=1 ORDER BY s.full_name""").fetchall()
    columns=c.execute("SELECT column_date FROM journal_columns WHERE discipline_id=? ORDER BY column_date",(did,)).fetchall()
    marks={}
    for r in c.execute("SELECT student_id,value,mark_date,lesson_no FROM marks WHERE discipline_id=? ORDER BY mark_date,id",(did,)).fetchall():
        marks.setdefault(r['student_id'],{}).setdefault(r['mark_date'],[]).append(dict(r))
    c.close()
    cols=[r['column_date'] for r in columns]
    # Existing marks automatically become journal columns, so old data is visible immediately.
    for bydate in marks.values():
        for md in bydate:
            if md not in cols: cols.append(md)
    cols.sort()
    data=[]
    for st in students:
        bydate=marks.get(st['id'],{})
        flat=[x for arr in bydate.values() for x in arr]
        data.append({**dict(st),'cells':{md:(bydate.get(md,[])[:1] or [None])[0] for md in cols},'stats':mark_stats(flat)})
    return web.json_response({'discipline':dict(ds) if ds else None,'columns':cols,'students':data})

async def api_admin_grade_column(request):
    require_admin(request)
    did=int(request.match_info['discipline_id']); data=await request.json()
    md=str(data.get('date','')).strip()
    try: datetime.strptime(md,'%Y-%m-%d')
    except ValueError: raise web.HTTPBadRequest(text='Дата должна быть в формате YYYY-MM-DD')
    c=db(); c.execute('INSERT OR IGNORE INTO journal_columns(discipline_id,column_date,created_at) VALUES(?,?,?)',(did,md,nowstr())); c.commit(); c.close()
    return web.json_response({'ok':True,'date':md})

async def api_admin_add_mark(request):
    require_admin(request)
    data=await request.json()
    sid=int(data['student_id']); did=int(data['discipline_id']); value=str(data.get('value','')).strip().upper()
    if value not in {'','2','3','4','5','Н','Б','О'}: raise web.HTTPBadRequest(text='Недопустимая отметка')
    md=data.get('mark_date') or date.today().isoformat()
    try: datetime.strptime(md,'%Y-%m-%d')
    except ValueError: raise web.HTTPBadRequest(text='Неверная дата. Используйте YYYY-MM-DD')
    c=db()
    old=c.execute('SELECT id FROM marks WHERE student_id=? AND discipline_id=? AND mark_date=? ORDER BY id DESC LIMIT 1',(sid,did,md)).fetchone()
    if not value:
        if old: c.execute('DELETE FROM marks WHERE id=?',(old['id'],))
        c.commit(); c.close(); return web.json_response({'ok':True,'deleted':bool(old)})
    c.execute('INSERT OR IGNORE INTO journal_columns(discipline_id,column_date,created_at) VALUES(?,?,?)',(did,md,nowstr()))
    if old:
        c.execute('UPDATE marks SET value=?,comment=?,created_at=? WHERE id=?',(value,data.get('comment',''),nowstr(),old['id']))
    else:
        c.execute('INSERT INTO marks(student_id,discipline_id,value,mark_date,lesson_no,comment,created_at) VALUES(?,?,?,?,?,?,?)',(sid,did,value,md,data.get('lesson_no'),data.get('comment',''),nowstr()))
    name=c.execute('SELECT full_name FROM students WHERE id=?',(sid,)).fetchone()
    dn=c.execute('SELECT name FROM disciplines WHERE id=?',(did,)).fetchone()
    c.execute('INSERT INTO mini_events(event_type,title,body,student_id,created_at) VALUES(?,?,?,?,?)',('grade',f'Новая отметка: {dn["name"]}',f'{value} · {md}',sid,nowstr()))
    c.commit(); c.close()
    return web.json_response({'ok':True})

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
    web.get("/health",health),
    web.get("/static/{name}",static_file),
    web.get("/api/me",api_me),
    web.get("/api/disciplines",api_disciplines),
    web.get("/api/schedule",api_schedule),
    web.get("/api/homework",api_homework),
    web.post("/api/homework/{homework_id}/material",api_homework_material),
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

app=web.Application(middlewares=[access_log_middleware, auth_middleware])
app.add_routes(routes)

async def run_bot():
    # Запускаем существующий aiogram-бот в том же asyncio-процессе,
    # чтобы Bothost мог обслуживать и Telegram-бота, и Mini App одним сервисом.
    from bot_main import Bot, Dispatcher, DefaultBotProperties, ParseMode, AsyncIOScheduler
    from bot_main import TOKEN, router as bot_router, notification_job, TZ, migrate as bot_migrate

    bot_migrate()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(bot_router)
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(notification_job, 'interval', args=[bot], minutes=1, coalesce=True, max_instances=1)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()

async def start_all():
    # Сначала запускаем миграцию основной БД бота: Mini App использует
    # те же таблицы students/disciplines/schedule/homework и т.д.
    from bot_main import migrate as bot_migrate
    bot_migrate()
    migrate()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "3000")))
    await site.start()
    print(f"Mini App started on {os.getenv('HOST', '0.0.0.0')}:{os.getenv('PORT', '3000')}")
    await run_bot()

if __name__=="__main__":
    asyncio.run(start_all())
