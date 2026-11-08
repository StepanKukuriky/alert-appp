#!/usr/bin/env python3
"""Міні-програма «Тривоги». Один файл, лише Python 3.8+, нічого встановлювати не треба.

Що робить:
  - сама опитує API alerts.in.ua і пише в чат «тривога» / «відбій» (канал «Тривоги»);
  - має канал «Новини», де адмін пише вручну; адмін також може швидко давати тривогу кнопками;
  - показує все людям у браузері в реальному часі, зі звуком; сторінку можна встановити
    на телефон як застосунок («На головний екран»); людям не потрібні ні Claude, ні акаунти;
  - за бажанням дублює у ваш Telegram-канал (так приходять сповіщення, коли сторінка закрита).

Спробувати без токена:   python3 alert_server.py --demo
По-справжньому:          ALERTS_TOKEN=токен ADMIN_PASSWORD=пароль python3 alert_server.py
Змінні (необов'язково): TG_BOT_TOKEN, TG_CHAT_ID, PORT (8080), POLL_SECONDS (45), STORE_FILE
"""
import os, sys, json, time, random, threading, queue, hmac, zlib, struct, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("ALERTS_TOKEN", "")
ADMIN = os.environ.get("ADMIN_PASSWORD", "")
TG_BOT = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
PORT = int(os.environ.get("PORT", "8080"))
POLL = max(30, int(os.environ.get("POLL_SECONDS", "45")))
STORE = os.environ.get("STORE_FILE", "messages.json")
DEMO = "--demo" in sys.argv
DEMO_EVERY = float(os.environ.get("DEMO_SECONDS", "12"))
API = "https://api.alerts.in.ua/v1/alerts/active.json"
TYPES = {"air_raid": "Повітряна тривога", "chemical": "Хімічна загроза",
         "radiation": "Радіаційна загроза", "other": "Інша загроза"}

lock = threading.Lock()
messages, clients, active, icons = [], [], {}, {}
seq = 0
posts = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load():
    global messages, seq
    try:
        with open(STORE, encoding="utf-8") as f:
            messages = json.load(f)
        seq = max([m["id"] for m in messages] + [0])
    except Exception:
        messages = []


def tg_send(text):
    if not (TG_BOT and TG_CHAT):
        return
    try:
        body = json.dumps({"chat_id": TG_CHAT, "text": text}).encode()
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % TG_BOT,
                                     body, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log("Telegram помилка:", e)


def add(kind, region, text, ch="alerts"):
    global seq
    with lock:
        seq += 1
        m = {"id": seq, "ts": int(time.time()), "ch": ch, "kind": kind, "region": region, "t": text}
        messages.append(m)
        del messages[:-400]
        try:
            with open(STORE, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False)
        except Exception as e:
            log("Не вдалося зберегти:", e)
        for q in list(clients):
            q.put(m)
    icon = {"alert": "🚨", "clear": "✅"}.get(kind, "📰" if ch == "news" else "ℹ️")
    log(icon, region, text)
    if ch == "news" or kind != "msg" or region == "Україна":
        threading.Thread(target=tg_send, args=("%s %s: %s" % (icon, region, text) if region else "%s %s" % (icon, text),),
                         daemon=True).start()
    return m


def fetch_active():
    req = urllib.request.Request(API, headers={"Authorization": "Bearer " + TOKEN, "User-Agent": "alert-server/2.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    out = {}
    for a in data.get("alerts", []):
        if a.get("finished_at"):
            continue
        region = a.get("location_title") or a.get("location_oblast") or "?"
        out[str(a.get("id"))] = (region, TYPES.get(a.get("alert_type"), "Тривога"))
    return out


def apply(now, first=False):
    global active
    if first:
        names = sorted(set(v[0] for v in now.values()))
        add("msg", "Україна", "Зараз активно: " + (", ".join(names) if names else "тривог немає"))
    else:
        for k, (reg, typ) in now.items():
            if k not in active:
                add("alert", reg, typ)
        for k, (reg, typ) in active.items():
            if k not in now:
                add("clear", reg, "Відбій тривоги")
    active = now


def poll_loop():
    first, delay = True, POLL
    while True:
        try:
            apply(fetch_active(), first)
            first, delay = False, POLL
        except Exception as e:
            log("Помилка API:", e)
            delay = min(delay * 2, 600)
        time.sleep(delay)


def demo_loop():
    regs = ["Київ", "Львівська", "Харківська", "Одеська", "Дніпропетровська", "Сумська", "Полтавська", "Чернігівська"]
    time.sleep(2)
    apply({}, True)
    n = 0
    while True:
        time.sleep(DEMO_EVERY)
        now = dict(active)
        if now and random.random() < .45:
            now.pop(random.choice(list(now)))
        else:
            n += 1
            now["d%d" % n] = (random.choice(regs), "Повітряна тривога (демо)")
        apply(now)


def png(size):
    if size in icons:
        return icons[size]
    rows = []
    c, bw, dr = size / 2, size * .06, size * .075
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            white = (abs(x - c) < bw and size * .2 < y < size * .58) or ((x - c) ** 2 + (y - size * .72) ** 2 < dr * dr)
            row += b"\xff\xff\xff" if white else b"\xd9\x3a\x3a"
        rows.append(bytes(row))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    icons[size] = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) +
                   chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))
    return icons[size]


MANIFEST = json.dumps({"name": "Тривоги", "short_name": "Тривоги", "start_url": "/", "display": "standalone",
                       "background_color": "#0e1621", "theme_color": "#17212b", "lang": "uk",
                       "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                                 {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]})
SW = """self.addEventListener('install',e=>{e.waitUntil(caches.open('t1').then(c=>c.add('/')));self.skipWaiting()});
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{var u=new URL(e.request.url);if(e.request.method!='GET'||u.pathname=='/events'||u.pathname.indexOf('/api/')==0)return;
e.respondWith(fetch(e.request).then(r=>{var c=r.clone();caches.open('t1').then(x=>x.put(e.request,c));return r}).catch(()=>caches.match(e.request)))});"""

PAGE = r"""<!DOCTYPE html><html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="manifest" href="/manifest.json"><link rel="apple-touch-icon" href="/icon-192.png"><meta name="theme-color" content="#17212b">
<meta name="apple-mobile-web-app-capable" content="yes"><title>Тривоги</title><style>
:root{--bg:#0e1621;--pn:#17212b;--in:#182533;--tx:#e8eef4;--mu:#7d8e9e;--ac:#5eb5f7;--r:#ff5a5a;--g:#5fd07e}
*{box-sizing:border-box}html,body{height:100%;margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,sans-serif}
body{display:flex;flex-direction:column;max-width:720px;margin:0 auto;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}
header{display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--pn)}header b{flex:1;font-size:18px}
.tabs{display:flex;background:var(--pn)}.tab{flex:1;text-align:center;padding:10px;font-weight:700;color:var(--mu);border-bottom:3px solid transparent;cursor:pointer}.tab.on{color:var(--ac);border-color:var(--ac)}.tab i{background:var(--r);color:#fff;border-radius:10px;padding:0 6px;font-style:normal;font-size:12px;margin-left:4px}
#pill{padding:10px 14px;font-weight:800;background:var(--pn)}#pill.a{background:var(--r);color:#fff}#pill.c{background:var(--g);color:#06210f}
#msgs{flex:1;overflow:auto;padding:10px;display:flex;flex-direction:column;gap:6px}
.m{max-width:88%;background:var(--in);border-radius:12px;padding:7px 10px;align-self:flex-start;line-height:1.35;overflow-wrap:anywhere}
.m.alert{border-left:5px solid var(--r)}.m.clear{border-left:5px solid var(--g)}.hd{font-weight:800;font-size:13px}.alert .hd{color:var(--r)}.clear .hd{color:var(--g)}
.tm{color:var(--mu);font-size:11px;text-align:right}button,.chip{background:var(--ac);color:#fff;border:0;border-radius:18px;padding:8px 14px;font-size:14px;cursor:pointer;font-family:inherit}
#adm{background:var(--pn);padding:8px;display:none}.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}.chips.sc{max-height:112px;overflow:auto}
.chip{background:var(--in);padding:6px 10px;font-size:13px}.chip.sel{background:var(--ac)}.chip.sel.a{background:var(--r)}.chip.sel.c{background:var(--g);color:#06210f}
#nt{display:flex;gap:6px}#tx{flex:1;border:0;border-radius:18px;background:var(--in);color:var(--tx);padding:9px 12px;font:inherit}
#toast{position:fixed;left:12px;right:12px;top:calc(12px + env(safe-area-inset-top));background:var(--r);color:#fff;padding:12px;border-radius:12px;font-weight:700;display:none;z-index:9}
#foot{font-size:12px;color:var(--mu);padding:6px 12px;background:var(--pn)}
</style></head><body><header><b>🚨 Тривоги</b><span id="st" style="font-size:12px;color:var(--mu)">підключення…</span><button id="snd">🔔 Звук</button><button id="ad" style="background:var(--in)">⚙</button></header>
<div class="tabs"><div class="tab on" data-c="alerts">🚨 Тривоги<i id="b1" hidden></i></div><div class="tab" data-c="news">📰 Новини<i id="b2" hidden></i></div></div>
<div id="pill"></div><div id="msgs"></div><div id="adm"></div><div id="toast"></div>
<div id="foot">Не замінює офіційні сповіщення й сирену. <span id="inst"></span></div><script>
var $=function(i){return document.getElementById(i)},seen={},act={},AC,sound=false,all=[],cur="alerts",un={alerts:0,news:0},pw=sessionStorage.getItem("pw")||"",QK="alert",QT="Загальна",ready=false;
var OBL=["Київ","Київська","Вінницька","Волинська","Дніпропетровська","Донецька","Житомирська","Закарпатська","Запорізька","Івано-Франківська","Кіровоградська","Луганська","Львівська","Миколаївська","Одеська","Полтавська","Рівненська","Сумська","Тернопільська","Харківська","Херсонська","Хмельницька","Черкаська","Чернівецька","Чернігівська","Крим","Уся Україна"],THR=["Загальна","Дрони","Ракети","Балістика","КАБ"];
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function hm(t){return new Date(t*1000).toLocaleTimeString("uk-UA",{hour:"2-digit",minute:"2-digit"})}
function siren(){if(!sound)return;try{AC=AC||new(window.AudioContext||window.webkitAudioContext)();var o=AC.createOscillator(),g=AC.createGain();o.type="sawtooth";o.frequency.setValueAtTime(500,AC.currentTime);o.frequency.linearRampToValueAtTime(850,AC.currentTime+.6);o.frequency.linearRampToValueAtTime(500,AC.currentTime+1.2);g.gain.value=.08;o.connect(g);g.connect(AC.destination);o.start();o.stop(AC.currentTime+1.25)}catch(e){}try{navigator.vibrate([300,150,300])}catch(e){}}
function toast(s){var t=$("toast");t.textContent=s;t.style.display="block";clearTimeout(toast.h);toast.h=setTimeout(function(){t.style.display="none"},6000)}
function pill(){var r=Object.keys(act);$("pill").className=r.length?"a":"c";$("pill").textContent=r.length?"🚨 Тривога: "+r.join(", "):"✅ Тривог немає"}
function node(m){var d=document.createElement("div");d.className="m "+m.kind;d.innerHTML=(m.kind=="alert"?'<div class="hd">🚨 ТРИВОГА · '+esc(m.region)+"</div>":m.kind=="clear"?'<div class="hd">✅ ВІДБІЙ · '+esc(m.region)+"</div>":m.region?'<div class="hd">'+esc(m.region)+"</div>":"")+esc(m.t)+'<div class="tm">'+hm(m.ts)+"</div>";return d}
function show(){var b=$("msgs");b.innerHTML="";all.forEach(function(m){if(m.ch==cur)b.appendChild(node(m))});b.scrollTop=1e9;
 document.querySelectorAll(".tab").forEach(function(t){t.classList.toggle("on",t.dataset.c==cur)});un[cur]=0;badges();$("pill").style.display=cur=="alerts"?"block":"none";drawAdm()}
function badges(){[["alerts","b1"],["news","b2"]].forEach(function(x){var e=$(x[1]);e.hidden=!un[x[0]];e.textContent=un[x[0]]})}
function take(m,live){if(seen[m.id])return;seen[m.id]=1;all.push(m);
 if(m.kind=="alert")act[m.region]=1;else if(m.kind=="clear")delete act[m.region];pill();
 if(m.ch==cur){$("msgs").appendChild(node(m));$("msgs").scrollTop=1e9}else if(live){un[m.ch]=(un[m.ch]||0)+1;badges()}
 if(live){if(m.kind=="alert"){siren();toast("🚨 "+m.region+": "+m.t);try{if(Notification.permission=="granted"&&document.hidden)new Notification("🚨 Тривога: "+m.region,{body:m.t})}catch(e){}}else if(m.kind=="clear")toast("✅ Відбій: "+m.region);else if(m.ch=="news")toast("📰 "+m.t)}}
function post(o){return fetch("/api/post",{method:"POST",headers:{"Content-Type":"application/json","X-Admin":pw},body:JSON.stringify(o)}).then(function(r){if(!r.ok)throw 1;toast("Опубліковано")}).catch(function(){toast("Не вдалося: невірний пароль?")})}
function drawAdm(){var a=$("adm");if(!pw){a.style.display="none";return}a.style.display="block";
 if(cur=="news"){a.innerHTML='<div id="nt"><input id="tx" placeholder="Новина…"><button id="go">➤</button></div>';$("go").onclick=function(){var t=$("tx").value.trim();if(t){post({ch:"news",kind:"msg",t:t});$("tx").value=""}};return}
 var h='<div class="chips"><button class="chip'+(QK=="alert"?" sel a":"")+'" data-k="alert">🚨 Тривога</button><button class="chip'+(QK=="clear"?" sel c":"")+'" data-k="clear">✅ Відбій</button></div>';
 if(QK=="alert"){h+='<div class="chips">';THR.forEach(function(x){h+='<button class="chip'+(QT==x?" sel":"")+'" data-th="'+x+'">'+x+"</button>"});h+="</div>"}
 h+='<div class="chips sc">';OBL.forEach(function(x){h+='<button class="chip" data-r="'+x+'">'+x+"</button>"});a.innerHTML=h+"</div>"}
$("adm").onclick=function(e){var b=e.target.closest(".chip");if(!b)return;if(b.dataset.k){QK=b.dataset.k;drawAdm()}else if(b.dataset.th){QT=b.dataset.th;drawAdm()}else if(b.dataset.r){post({ch:"alerts",kind:QK,region:b.dataset.r,t:QK=="clear"?"Відбій тривоги":"Повітряна тривога"+(QK=="alert"&&QT!="Загальна"?" · "+QT:"")})}};
document.querySelectorAll(".tab").forEach(function(t){t.onclick=function(){cur=t.dataset.c;show()}});
$("snd").onclick=function(){sound=true;$("snd").textContent="🔔 Звук увімкнено";siren();try{Notification.requestPermission()}catch(e){}};
$("ad").onclick=function(){var p=prompt("Пароль адміна");if(!p)return;fetch("/api/auth",{method:"POST",headers:{"X-Admin":p}}).then(function(r){if(r.ok){pw=p;sessionStorage.setItem("pw",p);show()}else toast("Невірний пароль")})};
var dp;window.addEventListener("beforeinstallprompt",function(e){e.preventDefault();dp=e;$("inst").innerHTML='<a href="#" id="ib" style="color:var(--ac)">Встановити застосунок</a>';$("ib").onclick=function(x){x.preventDefault();dp.prompt()}});
if(/iphone|ipad/i.test(navigator.userAgent))$("inst").textContent="iPhone: Поділитися → На початковий екран.";
if("serviceWorker"in navigator)navigator.serviceWorker.register("/sw.js").catch(function(){});
fetch("/api/messages").then(function(r){return r.json()}).then(function(a){a.forEach(function(m){m.ch=m.ch||"alerts";take(m,false)});show();
 var es=new EventSource("/events");es.onopen=function(){$("st").textContent="онлайн"};es.onerror=function(){$("st").textContent="перепідключення…"};es.onmessage=function(e){take(JSON.parse(e.data),true)}});
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ct="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def ok_admin(self):
        p = self.headers.get("X-Admin", "")
        ip = self.client_address[0]
        if time.time() - posts.get(ip, 0) < .5:
            return False
        posts[ip] = time.time()
        return bool(ADMIN) and hmac.compare_digest(p.encode(), ADMIN.encode())

    def do_POST(self):
        if not self.ok_admin():
            return self.send(403, b"no", "text/plain")
        if self.path == "/api/auth":
            return self.send(200, b"ok", "text/plain")
        if self.path == "/api/post":
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 4000)
                d = json.loads(self.rfile.read(n))
                ch = d.get("ch") if d.get("ch") in ("alerts", "news") else "alerts"
                kind = d.get("kind") if d.get("kind") in ("alert", "clear", "msg") else "msg"
                m = add(kind, str(d.get("region", ""))[:60], str(d.get("t", ""))[:500], ch)
                if kind == "alert":
                    active["m:" + m["region"]] = (m["region"], m["t"])
                elif kind == "clear":
                    active.pop("m:" + m["region"], None)
                return self.send(200, b"ok", "text/plain")
            except Exception:
                return self.send(400, b"bad", "text/plain")
        self.send(404, b"", "text/plain")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = queue.Queue()
            clients.append(q)
            try:
                while True:
                    try:
                        self.wfile.write(("data: %s\n\n" % json.dumps(q.get(timeout=20), ensure_ascii=False)).encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                if q in clients:
                    clients.remove(q)
            return
        if p == "/api/messages":
            return self.send(200, json.dumps(messages, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        if p == "/manifest.json":
            return self.send(200, MANIFEST.encode(), "application/manifest+json")
        if p == "/sw.js":
            return self.send(200, SW.encode(), "application/javascript")
        if p in ("/icon-192.png", "/icon-512.png"):
            return self.send(200, png(192 if "192" in p else 512), "image/png")
        self.send(200, PAGE.encode())


if __name__ == "__main__":
    if not DEMO and not TOKEN:
        sys.exit("Потрібен токен: ALERTS_TOKEN=... python3 alert_server.py  (або спробуйте --demo)")
    if not ADMIN:
        log("Увага: ADMIN_PASSWORD не задано, ручна публікація вимкнена.")
    load()
    threading.Thread(target=demo_loop if DEMO else poll_loop, daemon=True).start()
    log("Відкрийте http://localhost:%d  (режим: %s)" % (PORT, "ДЕМО" if DEMO else "API"))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
