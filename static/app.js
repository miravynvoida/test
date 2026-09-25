const tg=window.Telegram?.WebApp;
if(tg){tg.ready();tg.expand();}
const headers=()=>({'X-Telegram-Init-Data':tg?.initData||''});
async function api(url,opt={}){opt.headers={...(opt.headers||{}),...headers(),'Content-Type':'application/json'};let r=await fetch(url,opt);if(!r.ok){let t=await r.text();throw Error(t||'Ошибка');}return r.json();}
const app=document.getElementById('app');let me=null, scheduleDate=new Date(), gradePeriod=0;
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}
function card(x){return `<section class="card">${x}</section>`}
function avg(v){return v==null?'—':Number(v).toFixed(2)}
function iso(d){return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10)}
function ruDate(s){let [y,m,d]=s.split('-');return `${d}.${m}.${y}`}
function shell(title,body){app.innerHTML=`<header><button class="icon" onclick="go('home')">‹</button><div><div class="brand">Р-26-1</div><h1>${title}</h1></div></header><main>${body}</main>${nav()}`}
function nav(){return `<nav><button onclick="go('home')">⌂<span>Главная</span></button><button onclick="go('schedule')">▣<span>Расписание</span></button><button onclick="go('hw')">☷<span>ДЗ</span></button><button onclick="go('grades')">★<span>Оценки</span></button><button onclick="go('more')">☰<span>Ещё</span></button></nav>`}
async function boot(){try{me=await api('/api/me');renderHome();}catch(e){app.innerHTML=`<div class="error">Не удалось войти в дневник.<br><small>${esc(e.message)}</small></div>`}}
function renderHome(){app.innerHTML=`<header class="homehead"><div><div class="brand">Р-26-1</div><h1>Привет, ${esc(me.student.full_name.split(' ')[1]||me.student.full_name)}!</h1></div><div class="avatar">👤</div></header><main>
${card(`<div class="muted">Средний балл</div><div class="big">${avg(me.stats.average)}</div><div class="muted">${me.stats.count} оценок · Н: ${me.stats.missed} · Б: ${me.stats.sick} · О: ${me.stats.late}</div>`)}
<div class="grid2"><button class="tile" onclick="go('schedule')">📅<b>Расписание</b><span>По дням и датам</span></button><button class="tile" onclick="go('hw')">📝<b>Домашнее задание</b><span>По дисциплинам</span></button><button class="tile" onclick="go('grades')">📊<b>Мои оценки</b><span>По предметам</span></button><button class="tile" onclick="go('events')">🔔<b>Лента событий</b><span>Только мои события</span></button></div>
${me.is_admin?`<button class="wide admin" onclick="go('admin')">⚙️ Админ-панель</button>`:''}
${card(`<b>📈 Личная статистика</b><div class="stats"><div><strong>${avg(me.stats.average)}</strong><span>средний балл</span></div><div><strong>${me.stats.count}</strong><span>оценок</span></div><div><strong>${me.stats.missed}</strong><span>Н</span></div><div><strong>${me.stats.late}</strong><span>О</span></div></div>`)}</main>${nav()}`}
async function schedule(){let d=iso(scheduleDate), data=await api('/api/schedule?date='+d);let title=new Date(d+'T12:00:00').toLocaleDateString('ru-RU',{weekday:'long',day:'numeric',month:'long'});shell('Расписание',`${card(`<div class="calnav"><button onclick="shiftSchedule(-1)">‹</button><div><b>${title}</b><small>${ruDate(d)}</small></div><button onclick="shiftSchedule(1)">›</button></div><input class="dateinput" type="date" value="${d}" onchange="pickSchedule(this.value)"><button class="wide" onclick="scheduleToday()">Сегодня</button>`)}${data.lessons.map(x=>card(`<div class="lesson"><b>${x.lesson_no}. ${x.start}–${x.end}</b><strong>${x.emoji} ${esc(x.discipline)}</strong><span>${esc(x.teacher)} · ауд. ${esc(x.room)}</span><small>${esc(x.lesson_type)}</small></div>`)).join('')||card('ℹ️ На этот день расписания нет.')}`)}
function shiftSchedule(n){scheduleDate=new Date(scheduleDate);scheduleDate.setDate(scheduleDate.getDate()+n);schedule()}
function pickSchedule(v){scheduleDate=new Date(v+'T12:00:00');schedule()}
function scheduleToday(){scheduleDate=new Date();schedule()}
async function hw(){let d=await api('/api/homework');let groups={};d.items.forEach(x=>(groups[x.discipline_id]??=[]).push(x));let html=Object.values(groups).map(items=>{let x=items[0];return card(`<button class="rowbtn" onclick="hwDisc(${x.discipline_id})"><span>${x.emoji} ${esc(x.discipline)}</span><b>${items.length}</b><small>Заданий: ${items.length}</small></button>`)}).join('');shell('Домашнее задание',html||card('Домашних заданий нет.'))}
async function hwDisc(id){
  let d=await api('/api/homework');
  let items=d.items.filter(x=>x.discipline_id==id);
  let x=items[0];
  let body=items.map(h=>{
    let title=h.title||((h.text||'').slice(0,70));
    let task=esc(h.text||'').replace(/\n/g,'<br>');
    let explanation=h.explanation?`<div class="note">${esc(h.explanation)}</div>`:'';
    return card(`<div class="muted">до ${esc(h.due_date)} · опубликовано ${esc(h.published_date)}</div><h3>${esc(title)}</h3><p>${task}</p>${explanation}<button class="wide" onclick="sendMaterial(${h.id})">📎 Показать материал</button>`);
  }).join('');
  shell(`${x?.emoji||'📝'} ${esc(x?.discipline||'Дисциплина')}`,body||card('Заданий нет.'));
}
async function sendMaterial(id){try{let r=await api('/api/homework/'+id+'/material',{method:'POST'});alert(r.ok?`Материал отправлен в Telegram (${r.sent}).`:(r.message||'Материалов нет.'));}catch(e){alert('Не удалось отправить материал.')}}
async function grades(){let q=gradePeriod?`?start=${iso(new Date(Date.now()-gradePeriod*864e5))}&end=${iso(new Date())}`:'';let d=await api('/api/grades'+q);shell('Мои оценки',`${card(`<div class="big">${avg(d.overall.average)}</div><div class="muted">${d.overall.count} оценок · Н ${d.overall.missed} · Б ${d.overall.sick} · О ${d.overall.late}</div>`)}<div class="filter"><button onclick="setGradePeriod(30)">30 дней</button><button onclick="setGradePeriod(90)">90 дней</button><button onclick="setGradePeriod(0)">Весь период</button></div>`+d.disciplines.map(x=>`<button class="rowbtn" onclick="gradeDetail(${x.id})"><span>${x.emoji} ${esc(x.name)}</span><b>${avg(x.average)}</b><small>Н ${x.missed} · Б ${x.sick} · О ${x.late}</small></button>`).join(''))}
function setGradePeriod(n){gradePeriod=n;grades()}
async function gradeDetail(id){let d=await api('/api/grades/'+id);shell(`${d.discipline.emoji} ${esc(d.discipline.name)}`,`${card(`<div class="big">${avg(d.stats.average)}</div><div class="muted">${d.stats.count} оценок · Н ${d.stats.missed} · Б ${d.stats.sick} · О ${d.stats.late}</div>`)}${d.marks.map(x=>`<div class="mark"><span>${ruDate(x.mark_date)}</span><b class="v">${esc(x.value)}</b><small>${x.lesson_no?x.lesson_no+' пара':''}</small></div>`).join('')||card('Оценок пока нет.')}`)}
async function events(){let d=await api('/api/events');shell('Лента событий',d.events.map(x=>card(`<div class="muted">${esc(x.created_at.replace('T',' ').slice(0,16))}</div><b>${esc(x.title)}</b><p>${esc(x.body)}</p>`)).join('')||card('Событий пока нет.'))}
function more(){shell('Ещё',`${card(`<h3>👤 Личный кабинет</h3><p>${esc(me.student.full_name)}</p><p class="muted">Группа Р-26-1</p><div class="stats"><div><strong>${avg(me.stats.average)}</strong><span>средний балл</span></div><div><strong>${me.stats.count}</strong><span>оценок</span></div><div><strong>${me.stats.missed}</strong><span>Н</span></div><div><strong>${me.stats.late}</strong><span>О</span></div></div>`)}${me.is_admin?`<button class="wide admin" onclick="go('admin')">⚙️ Админ-панель</button>`:''}`)}
async function admin(){let o=await api('/api/admin/overview');shell('Админ-панель',`${card(`<div class="stats"><div><strong>${o.students}</strong><span>студентов</span></div><div><strong>${o.included}</strong><span>в журнале</span></div><div><strong>${o.marks}</strong><span>отметок</span></div></div>`)}<div class="grid2"><button class="tile" onclick="adminGrades()">📊<b>Журнал оценок</b><span>Дата + отметка</span></button><button class="tile" onclick="adminStudents()">👥<b>Студенты</b><span>Состав журнала</span></button><button class="tile" onclick="adminSchedule()">📅<b>Расписание</b><span>По датам</span></button><button class="tile" onclick="go('events')">🔔<b>Лента</b><span>События</span></button></div>`)}
async function adminStudents(){let d=await api('/api/admin/students');shell('Студенты в журнале',d.students.map(x=>`<div class="studentrow"><span>${esc(x.full_name)}</span><label><input type="checkbox" ${x.include_in_journal?'checked':''} onchange="toggleStudent(${x.id},this.checked)"> журнал</label></div>`).join(''))}
async function toggleStudent(id,v){await api('/api/admin/students/'+id+'/toggle',{method:'POST',body:JSON.stringify({include_in_journal:v})})}
async function adminGrades(){let ds=await api('/api/disciplines');shell('Журнал оценок',ds.disciplines.map(d=>`<button class="rowbtn" onclick="gradeBook(${d.id})"><span>${d.emoji} ${esc(d.name)}</span><b>→</b></button>`).join(''))}
async function gradeBook(id){
  let d=await api('/api/admin/grades/'+id);
  let heads=d.columns.map(md=>`<th class="datehead"><span>${ruDate(md)}</span></th>`).join('');
  let rows=d.students.map(s=>`<tr>
    <th class="namecell">${esc(s.full_name)}</th>
    ${d.columns.map(md=>{let c=s.cells[md];let val=c?String(c.value):'';return `<td class="markcell"><select onchange="setCell(${s.id},${id},'${md}',this.value)" aria-label="${esc(s.full_name)} ${ruDate(md)}"><option value="" ${!val?'selected':''}>·</option><option value="2" ${val==='2'?'selected':''}>2</option><option value="3" ${val==='3'?'selected':''}>3</option><option value="4" ${val==='4'?'selected':''}>4</option><option value="5" ${val==='5'?'selected':''}>5</option><option value="Н" ${val==='Н'?'selected':''}>Н</option><option value="Б" ${val==='Б'?'selected':''}>Б</option><option value="О" ${val==='О'?'selected':''}>О</option></select></td>`}).join('')}
    <td class="avgcell"><b>${avg(s.stats.average)}</b></td>
  </tr>`).join('');
  shell(`Журнал · ${esc(d.discipline.name)}`,`<div class="booktools"><button class="excelbtn" onclick="addColumn(${id})">➕ Добавить дату</button><span>2–5 — среднее · Н/Б/О не учитываются</span></div><div class="tablewrap"><table class="gradebook"><thead><tr><th class="namehead">Фамилия и Имя</th>${heads}<th>Среднее</th></tr></thead><tbody>${rows}</tbody></table></div>`)
}
async function addColumn(id){
  let v=prompt('Введите дату столбца в формате ДД.ММ.ГГГГ');
  if(!v)return;
  v=v.trim();
  let md='';
  if(/^\d{4}-\d{2}-\d{2}$/.test(v)) md=v;
  else if(/^\d{2}\.\d{2}\.\d{4}$/.test(v)){let [dd,mm,yyyy]=v.split('.');md=`${yyyy}-${mm}-${dd}`;}
  else return alert('Неверная дата. Пример: 25.09.2026');
  try{await api('/api/admin/grades/'+id+'/columns',{method:'POST',body:JSON.stringify({date:md})});await gradeBook(id)}catch(e){alert('Не удалось добавить дату: '+e.message)}
}
async function setCell(sid,did,md,v){
  v=(v||'').trim().toUpperCase();
  if(!['','2','3','4','5','Н','Б','О'].includes(v))return alert('Допустимо: 2, 3, 4, 5, Н, Б, О');
  try{await api('/api/admin/grades',{method:'POST',body:JSON.stringify({student_id:sid,discipline_id:did,value:v,mark_date:md})});await gradeBook(did)}catch(e){alert('Не удалось сохранить: '+e.message)}
}
async function adminSchedule(){let d=iso(new Date());let x=await api('/api/admin/schedule?date='+d);let ds=await api('/api/disciplines');shell('Редактор расписания',`${card(`<div class="calnav"><button onclick="adminScheduleShift(-1)">‹</button><div><b>${ruDate(d)}</b><small>Выберите дату</small></div><button onclick="adminScheduleShift(1)">›</button></div><input id="admDate" class="dateinput" type="date" value="${d}" onchange="adminSchedulePick(this.value)">`)}<div id="scheduleEditor">${x.lessons.map(l=>`<div class="editlesson"><b>${l.lesson_no}</b><input value="${l.start}" data-k="start"><input value="${l.end}" data-k="end"><select data-k="discipline_id">${ds.disciplines.map(z=>`<option value="${z.id}" ${z.id==l.discipline_id?'selected':''}>${esc(z.name)}</option>`).join('')}</select><input value="${esc(l.room)}" data-k="room"><input value="${esc(l.lesson_type)}" data-k="lesson_type"></div>`).join('')||card('На этот день пар нет.')}</div><button class="wide" onclick="saveSchedule('${d}')">💾 Сохранить</button>`)}
let admDate=iso(new Date());
function adminScheduleShift(n){let d=new Date(admDate+'T12:00:00');d.setDate(d.getDate()+n);admDate=iso(d);adminSchedule()}
function adminSchedulePick(v){admDate=v;adminSchedule()}
async function saveSchedule(d){let rows=[...document.querySelectorAll('.editlesson')].map(r=>{let o={};[...r.querySelectorAll('[data-k]')].forEach(e=>o[e.dataset.k]=e.value);return o});await api('/api/admin/schedule',{method:'POST',body:JSON.stringify({date:d,lessons:rows})});alert('Сохранено');adminSchedule()}
function go(v){({home:renderHome,schedule,hw,grades,events,more,admin}[v]||renderHome)()}
boot();
