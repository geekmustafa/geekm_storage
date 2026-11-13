#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
geekm_knob.py v1.0 - الرازبيري باي = دماغ شاشة الريوتري الدائرية لمخزن المجسمات

الفكرة:
  * السكنر USB على الرازبيري  -> هذا البرنامج يقرأه (بدون ما "يحجزه"، فباقي الأنظمة تبقى تشتغل)
  * البرنامج يتكلم مع إضافة Geekm Figurines بموقعك (نفس مفتاح الـ API)
  * البرنامج يرسم شاشة 466x466 كصورة (عربي سليم + صور المنتجات) ويرسلها للشاشة عبر الـ WiFi
  * الشاشة (ESP32) مجرد "طرفية": تعرض الصورة وترسل حركة الويل/الضغط/اللمس لهذا البرنامج

تشغيل يدوي للتجربة:   python3 geekm_knob.py
فحص الإعدادات:        python3 geekm_knob.py --check
أسماء قارئات الباركود: python3 geekm_knob.py --list
صور تجريبية بدون موقع: python3 geekm_knob.py --demo ~/knob_demo
المعاينة من المتصفح:   http://<ip-الرازبيري>:8787/preview?t=<KNOB_TOKEN>
"""
import io
import json
import os
import collections
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import math
import wave
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.8"

# ============================ الإعدادات (متغيرات بيئة) ============================
URL = os.environ.get("GEEKM_URL", "https://geekm.store").rstrip("/")
KEY = os.environ.get("GEEKM_KEY", "")                       # مفتاح API من ووكومرس > Geekm Figurines
TOKEN = os.environ.get("KNOB_TOKEN", "")                    # كلمة سر بين الرازبيري والشاشة (اخترها أنت)
PORT = int(os.environ.get("KNOB_PORT", "8787"))
SCANNER_NAME = os.environ.get("GEEKM_SCANNER", "")          # جزء من اسم القارئ (من --list) - فارغ = أول قارئ
SCAN_ADDS = int(os.environ.get("KNOB_SCAN_ADDS", "1"))      # كل مسحة = +كم على الشاشة (بدون حفظ حتى تضغط)
JPEG_QUALITY = int(os.environ.get("KNOB_JPEG_QUALITY", "78"))   # 70-90. اقل = اسرع وحجم اصغر
SLOW_LOG_MS = int(os.environ.get("KNOB_SLOW_LOG_MS", "120"))     # يسجّل بالـ journal اي رسم يأخذ اكثر من هالقيمة
AUTOSAVE_ON_SWITCH = os.environ.get("KNOB_AUTOSAVE_ON_SWITCH", "1") != "0"   # مسحت منتج ثاني؟ يحفظ الأول تلقائياً
IDLE_HOME_SEC = int(os.environ.get("KNOB_IDLE_HOME", "45"))  # رجوع للقائمة الرئيسية بعد خمول (إذا ماكو تغيير معلّق)
SLEEP_SEC = int(os.environ.get("KNOB_SLEEP", "300"))        # شاشة سوداء بعد خمول (يحمي الـ AMOLED)
INVERT_WHEEL = os.environ.get("KNOB_INVERT", "0") == "1"    # اعكس اتجاه الويل
LOCAL_QTY = os.environ.get("KNOB_LOCAL_QTY", "1") != "0"    # 1 = الشاشة ترسم رقم التغيير بنفسها (فوري). 0 = الرازبيري يرسمه (للمعاينة بالمتصفح)
BEEP = os.environ.get("KNOB_BEEP", "1") != "0"
DEBOUNCE_SEC = 0.6                                          # نفس الباركود خلال هذي المدة = تجاهل
SZ = 466                       # مساحة التصميم (الدائرة)
OUT = 480                      # الصورة اللي تنرسل للشاشة (كامل الشاشة بدون حدود سودة)
OFF = (OUT - SZ) // 2
LOW_STOCK = int(os.environ.get("KNOB_LOW", "2"))             # مخزن <= هذا الرقم = حلقة حمراء (والضعف = برتقالي)
LOCK_ON = os.environ.get("KNOB_LOCK", "0") == "1"            # 1 = بعد السبات لازم ضغطة طويلة للفتح
LIST_BLUR = os.environ.get("KNOB_LIST_BLUR", "0") == "1"      # 1 = خلفية مغوّشة بالقوائم أيضاً (أثقل على الشاشة)
CLOCK_SECS = float(os.environ.get("KNOB_CLOCK_SECS", "4"))   # كم ثانية تظهر الساعة بعد اللمس قبل ما يبدأ النظام
STORE = "GEEKM STORE"
HOME = os.path.expanduser("~")
OUTBOX_FILE = os.path.join(HOME, ".geekm_knob_outbox.json")
STATS_FILE = os.path.join(HOME, ".geekm_knob_stats.json")
HIST_FILE = os.path.join(HOME, ".geekm_knob_history.json")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# ================================== ألوان ==================================
WHITE = (255, 255, 255)
MUTED = (170, 175, 185)
ACCENT = (255, 106, 26)      # برتقالي (مثل أزرار العرض التجريبي)
GREEN = (46, 204, 113)
RED = (235, 64, 52)
AMBER = (255, 176, 32)
SOFTRED = (255, 120, 110)

# ألوان: لون المتجر (السمائي) فقط، بدون توهّج وبدون ألوان مختلفة
BRAND = (76, 168, 193)       # نفس سمائي الموقع
BRAND_DARK = (28, 62, 74)        # نفس اللون بس غامق (للشاشات اللي فيها صور/ألوان حالة)
INK = (14, 46, 62)           # كبسولات وكتابة غامقة
LIGHT = (236, 247, 251)      # كتابة ثانوية فاتحة
MENU_ICON = ["scan", "need", "all", "count", "hist", "orders", "top"]


def mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def stock_color(it):
    """لون حلقة المنتج: أحمر = ناقص/قليل، برتقالي = متوسط، أخضر = متوفر"""
    st_, need = it.get("stock", 0), it.get("need", 0)
    if need > 0 or st_ <= LOW_STOCK:
        return RED
    if st_ <= LOW_STOCK * 2:
        return AMBER
    return GREEN
DARK = (14, 16, 20)

LOCK = threading.RLock()


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ================================ ووكومرس API ================================
class WooError(Exception):
    def __init__(self, code, msg):
        Exception.__init__(self, "%s %s" % (code, msg))
        self.code, self.msg = code, msg


def woo(method, path, data=None, timeout=15):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(URL + "/wp-json/geekm-fig/v1" + path, data=body, method=method, headers={
        "X-Geekm-Key": KEY, "Content-Type": "application/json", "User-Agent": "GeekmScan/2.0 (knob)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            pass
        raise WooError(e.code, msg or "HTTP %d" % e.code)
    except Exception as e:
        raise WooError(0, str(e))


ITEMS = {"list": [], "t": 0.0}


def fetch_items():
    d = woo("GET", "/items")
    with LOCK:
        ITEMS["list"], ITEMS["t"] = d["items"], time.time()
    return d["items"]


def lookup_item(code):
    """يرجع المنتج أو None. إذا الموقع طايح يحاول من الكاش المحلي"""
    try:
        r = woo("GET", "/lookup?code=" + urllib.parse.quote(code))
        return r.get("item") if r.get("found") else None
    except WooError as e:
        if e.code != 0:
            raise
        with LOCK:
            for it in ITEMS["list"]:
                if code in (it.get("sku"), it.get("barcode")):
                    return it
        raise


# ================================ الصوت ================================
_TMP = tempfile.mkdtemp()
_WAV = {}


def _make_wav(path, freq, ms, vol=0.6):
    rate = 22050
    n = int(rate * ms / 1000)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(vol * 32767 * math.sin(2 * math.pi * freq * t / rate))) for t in range(n)))


def beep(kind="ok"):
    if not BEEP:
        return
    try:
        if not _WAV:
            _WAV["ok"] = os.path.join(_TMP, "ok.wav")
            _WAV["err"] = os.path.join(_TMP, "err.wav")
            _make_wav(_WAV["ok"], 1800, 110)
            _make_wav(_WAV["err"], 400, 400)
            _WAV["warn"] = os.path.join(_TMP, "warn.wav")
            _make_wav(_WAV["warn"], 950, 220)
            _WAV["order"] = os.path.join(_TMP, "order.wav")
            _make_wav(_WAV["order"], 1300, 320)
        subprocess.Popen(["aplay", "-q", _WAV.get(kind, _WAV["ok"])],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ============================ الطابور (إرسال مضمون) ============================
OUTBOX = []


def outbox_load():
    global OUTBOX
    try:
        with open(OUTBOX_FILE, "r", encoding="utf-8") as f:
            OUTBOX = json.load(f)
    except Exception:
        OUTBOX = []


def outbox_save():
    try:
        with open(OUTBOX_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(OUTBOX, f)
        os.replace(OUTBOX_FILE + ".tmp", OUTBOX_FILE)
    except OSError as e:
        log("WARN outbox save: %s" % e)


def outbox_add(pid, delta):
    with LOCK:
        OUTBOX.append({"product_id": int(pid), "delta": int(delta), "t": time.time()})
        outbox_save()
    log("outbox +%s -> product %s (%d waiting)" % (delta, pid, len(OUTBOX)))


def outbox_worker():
    while True:
        time.sleep(10)
        with LOCK:
            jobs = list(OUTBOX)
        for j in jobs:
            act = "stock" if j["delta"] > 0 else "remove"
            try:
                woo("POST", "/adjust", {"product_id": j["product_id"], "action": act, "qty": abs(j["delta"])})
                log("outbox sent: product %s %+d" % (j["product_id"], j["delta"]))
            except WooError as e:
                if e.code == 0:
                    break                       # لا اتصال: نحاول بعد 10 ثواني
                log("outbox DROP (%s): %s" % (e, j))
            with LOCK:
                if j in OUTBOX:
                    OUTBOX.remove(j)
                outbox_save()


# ================================ الصور ================================
IMG = OrderedDict()
IMG_PENDING = set()


def img_get(url):
    """صورة المنتج من الكاش (PIL). إذا مو جاهزة ينزّلها بالخلفية ويرجع None"""
    if not url:
        return None
    with LOCK:
        if url in IMG:
            IMG.move_to_end(url)
            return IMG[url]
        if url in IMG_PENDING:
            return None
        IMG_PENDING.add(url)
    threading.Thread(target=_img_load, args=(url,), daemon=True).start()
    return None


def _img_load(url):
    im = None
    try:
        from PIL import Image, ImageOps
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "image/*,*/*"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        im = Image.open(io.BytesIO(data))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        im = ImageOps.fit(im.convert("RGB"), (SZ, SZ), Image.LANCZOS)
    except Exception as e:
        log("image failed (%r): %s" % (e, url[:80]))
    with LOCK:
        IMG_PENDING.discard(url)
        IMG[url] = im
        while len(IMG) > 60:
            IMG.popitem(last=False)
        bump()


BLUR = collections.OrderedDict()   # كاش الخلفيات المغوّشة


# ============================ احصائيات اليوم + سجل العمليات ============================
STATS = {"day": "", "add": 0, "rem": 0, "saves": 0}
HIST = []                    # آخر 20 عملية (الأحدث أولاً)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _jload(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _jsave(path, data):
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError as e:
        log("WARN save %s: %s" % (path, e))


def stats_load():
    global HIST
    STATS.update(_jload(STATS_FILE, {}))
    h = _jload(HIST_FILE, [])
    HIST = h if isinstance(h, list) else []


def stats_roll():
    if STATS.get("day") != _today():
        STATS.update({"day": _today(), "add": 0, "rem": 0, "saves": 0})


def stats_today():
    with LOCK:
        stats_roll()
        return STATS["add"], STATS["rem"]


def stats_add(delta, undo=False):
    with LOCK:
        stats_roll()
        k = "add" if delta > 0 else "rem"
        if undo:
            STATS[k] = max(0, STATS[k] - abs(delta))
        else:
            STATS[k] += abs(delta)
            STATS["saves"] += 1
        _jsave(STATS_FILE, STATS)


def hist_add(item, delta):
    with LOCK:
        HIST.insert(0, {"id": item.get("id"), "title": item.get("title", ""), "image": item.get("image", ""),
                        "delta": int(delta), "t": time.time()})
        del HIST[20:]
        _jsave(HIST_FILE, HIST)


def ago(t):
    s_ = max(0, int(time.time() - t))
    if s_ < 60:
        return "الآن"
    if s_ < 3600:
        return "قبل %d دقيقة" % (s_ // 60)
    if s_ < 86400:
        return "قبل %d ساعة" % (s_ // 3600)
    return "قبل %d يوم" % (s_ // 86400)


# ============================ احصائيات الموقع (طلبات + الأكثر مبيعاً) ============================
SAPI = {"data": None, "t": 0.0, "err": ""}


def notify_order(n):
    """طلب جديد وصل: صوت + اشعار (الا اذا انت وسط عملية)"""
    beep("order")
    if st.mode in ("busy", "toast", "list", "history", "confirm", "product", "link_wait"):
        return
    if st.asleep:
        st.asleep = False
    st.last_input = time.time()
    show_toast("ok", "طلب جديد", "%d طلب ينتظر التجهيز" % n, "count_wait" if st.mode == "count_wait" else "home", 6)


def fetch_sapi():
    try:
        d = woo("GET", "/stats", timeout=12)
    except WooError as e:
        with LOCK:
            SAPI["err"] = "404" if e.code == 404 else str(e.msg)[:30]
            SAPI["t"] = time.time()
        return None
    for t_ in d.get("top", []):
        img_get(t_.get("image"))
    with LOCK:
        prev = SAPI["data"]
        SAPI.update({"data": d, "t": time.time(), "err": ""})
        if prev is not None and d.get("new", 0) > prev.get("new", 0):
            notify_order(d.get("new", 0))
        if prev != d and st.mode in ("home", "orders", "top", "clock"):
            bump()
    return d


def orders_list():
    return ((SAPI["data"] or {}).get("orders")) or []


def refresh_sapi():
    threading.Thread(target=lambda: _safe(fetch_sapi), daemon=True).start()


# ================================ الحالة ================================
class State:
    def __init__(self):
        self.ver = 1
        self.mode = "home"            # home | product | list | confirm | toast | busy | link_wait
        self.prev_mode = "home"
        self.menu = 0
        self.item = None
        self.delta = 0
        self.origin = "scan"          # scan | need | all
        self.list = []
        self.list_kind = "need"       # need | all | link
        self.list_idx = 0
        self.link_code = ""
        self.toast = None             # dict(kind,title,sub,until,after)
        self.busy_text = ""
        self.confirm = None           # dict(total,count,jobs)
        self.last_input = time.time()
        self.asleep = False
        self.last_seen = 0.0
        self.clock_until = 0.0
        self.clock_min = -1
        self.count_n = 0
        self.hist_idx = 0
        self.ord_idx = 0
        self.undo_armed = 0.0


st = State()


def bump():
    st.ver += 1


def touch_input():
    """يسجل نشاط. يرجع True اذا كانت الشاشة نايمة وهذا الحدث فقط أيقظها (يظهر الساعة ثم يبدأ النظام)"""
    st.last_input = time.time()
    if st.asleep:
        st.asleep = False
        if not (st.mode == "product" and st.delta):
            st.mode, st.item, st.delta, st.toast = "clock", None, 0, None
            st.clock_until = time.time() + CLOCK_SECS
            st.clock_min = datetime.now().minute
        bump()
        return True
    return False


MENU = [
    ("امسح باركود", "جاهز للمسح"),
    ("ناقص للطباعة", ""),
    ("كل المنتجات", ""),
    ("جرد المخزن", "عدّ الرف وصحّح الفروقات"),
    ("سجل العمليات", ""),
    ("الطلبات", ""),
    ("الأكثر مبيعاً", "هذا الأسبوع"),
]


def need_stats():
    need = [i for i in ITEMS["list"] if i["need"] > 0]
    return len(need), sum(i["need"] for i in need)


def bg(fn, done, busy_text=None):
    """ينفذ fn بخيط خلفي (شبكة) ثم done(result, err) تحت القفل"""
    if busy_text:
        st.prev_mode, st.mode, st.busy_text = st.mode, "busy", busy_text
        bump()

    def run():
        try:
            r, err = fn(), None
        except Exception as e:
            r, err = None, e
        with LOCK:
            try:
                done(r, err)
            except Exception as e:
                log("ERROR in done(): %r" % (e,))
                st.mode = "home"
                bump()
    threading.Thread(target=run, daemon=True).start()


def go_home():
    st.mode, st.item, st.delta, st.toast = "home", None, 0, None
    bump()


def show_toast(kind, title, sub="", after="home", secs=1.7):
    st.mode = "toast"
    st.toast = {"kind": kind, "title": title, "sub": sub, "until": time.time() + secs, "after": after}
    bump()
    beep("err" if kind == "err" else "ok")


def finish_toast():
    after = (st.toast or {}).get("after", "home")
    st.toast = None
    if after == "list" and st.list:
        st.mode = "list"
        bump()
    elif after == "count_wait":
        st.mode, st.item, st.delta = "count_wait", None, 0
        bump()
    elif after == "product" and st.item:
        st.mode = "product"
        bump()
    else:
        go_home()


def open_product(item, delta, origin):
    st.mode, st.item, st.delta, st.origin = "product", item, delta, origin
    img_get(item.get("image"))
    bump()


def build_list(kind):
    items = list(ITEMS["list"])
    if kind == "need":
        out = sorted([i for i in items if i["need"] > 0], key=lambda i: (-i["need"], i["title"]))
        if out:
            out.append({"special": "addall", "total": sum(i["need"] for i in out), "count": len(out)})
        return out
    if kind == "link":
        return sorted(items, key=lambda i: (bool(i.get("sku") or i.get("barcode")), i["title"]))
    return sorted(items, key=lambda i: i["title"])


def enter_list(kind):
    st.list_kind = kind

    def done(r, err):
        if err is not None:
            show_toast("err", "تعذر الاتصال", "بالموقع", "home", 2.2)
            return
        st.list = build_list(kind)
        st.list_idx = 0
        if not st.list:
            show_toast("ok", "لا يوجد ناقص", "كل شي متوفر", "home")
            return
        st.mode = "list"
        for it in st.list[:3]:
            img_get(it.get("image"))
        bump()
    bg(fetch_items, done, "جارٍ التحميل")


def refresh_list_local():
    """يعيد بناء القائمة من الكاش مع الحفاظ على الموقع"""
    st.list = build_list(st.list_kind)
    st.list_idx = max(0, min(st.list_idx, len(st.list) - 1))


# ---------------------------------- الحفظ ----------------------------------
def after_save_mode(origin):
    return "list" if origin in ("need", "all") else ("count_wait" if origin == "count" else "home")


def do_save():
    it, delta, origin = st.item, st.delta, st.origin
    if not it:
        return go_home()
    if delta == 0:
        return finish_back(origin)
    act = "stock" if delta > 0 else "remove"
    qty = abs(delta)
    show_toast("ok", "%+d" % delta, "جارٍ الحفظ...", after_save_mode(origin), 8)   # فوري - ما ننتظر الموقع
    tref = st.toast

    def upd(sub, kind=None, secs=1.6):
        if st.mode == "toast" and st.toast is tref:
            if kind:
                tref["kind"] = kind
            tref["sub"] = sub
            tref["until"] = time.time() + secs
            bump()

    def fn():
        return woo("POST", "/adjust", {"product_id": it["id"], "action": act, "qty": qty})

    def done(r, err):
        if err is None:
            for i, x in enumerate(ITEMS["list"]):
                if x["id"] == r["id"]:
                    ITEMS["list"][i] = r
            if origin in ("need", "all"):
                refresh_list_local()
            stats_add(delta)
            hist_add(it, delta)
            if origin == "count":
                st.count_n += 1
            log("saved: %s %+d -> stock %s need %s" % (r["title"], delta, r["stock"], r["need"]))
            if r["stock"] <= LOW_STOCK:
                upd("تنبيه: المخزن واطي (%d)" % r["stock"], "warn", 2.6)
                beep("warn")
            else:
                upd("المخزن الآن %d%s" % (r["stock"], ("  |  ناقص %d" % r["need"]) if r["need"] else ""))
        else:
            outbox_add(it["id"], delta)
            stats_add(delta)
            hist_add(it, delta)
            log("save queued after error: %s" % (err,))
            if getattr(err, "code", 0) == 0:
                upd("محفوظ بالطابور - لا يوجد اتصال", "warn", 2.4)
            else:
                upd("تعذر الحفظ - سيُعاد المحاولة", "warn", 2.6)
    bg(fn, done)


def enter_history():
    if not HIST:
        return show_toast("warn", "لا يوجد سجل", "يظهر بعد أول حفظ", "home", 2.0)
    st.mode, st.hist_idx, st.undo_armed = "history", 0, 0.0
    bump()


def hist_press():
    """ضغطة اولى = تجهيز التراجع، ثانية (خلال 4 ثواني) = تنفيذ"""
    if not HIST:
        return go_home()
    now = time.time()
    if now - st.undo_armed > 4:
        st.undo_armed = now
        return bump()
    st.undo_armed = 0.0
    e = HIST[max(0, min(st.hist_idx, len(HIST) - 1))]
    do_undo(e)


def do_undo(e):
    delta = -int(e["delta"])
    act = "stock" if delta > 0 else "remove"
    show_toast("ok", "%+d" % delta, "تراجع... جارٍ الحفظ", "home", 8)
    tref = st.toast
    if e in HIST:
        HIST.remove(e)
        _jsave(HIST_FILE, HIST)

    def upd(sub, kind=None, secs=1.8):
        if st.mode == "toast" and st.toast is tref:
            if kind:
                tref["kind"] = kind
            tref["sub"] = sub
            tref["until"] = time.time() + secs
            bump()

    def fn():
        return woo("POST", "/adjust", {"product_id": e["id"], "action": act, "qty": abs(delta)})

    def done(r, err):
        stats_add(e["delta"], undo=True)
        if err is None:
            for i, x in enumerate(ITEMS["list"]):
                if x["id"] == r["id"]:
                    ITEMS["list"][i] = r
            log("undo: %s %+d -> stock %s" % (r["title"], delta, r["stock"]))
            upd("تم التراجع - المخزن الآن %d" % r["stock"])
        else:
            outbox_add(e["id"], delta)
            upd("التراجع بالطابور", "warn", 2.4)
    bg(fn, done)


def finish_back(origin):
    if origin in ("need", "all") and st.list:
        st.mode, st.item, st.delta = "list", None, 0
        bump()
    elif origin == "count":
        st.mode, st.item, st.delta = "count_wait", None, 0
        bump()
    else:
        go_home()


def do_cancel():
    finish_back(st.origin)


def do_addall():
    jobs = [(i["id"], i["need"]) for i in st.list if not i.get("special")]
    total = sum(q for _, q in jobs)

    def fn():
        ok = 0
        for pid, q in jobs:
            try:
                woo("POST", "/adjust", {"product_id": pid, "action": "stock", "qty": q})
            except WooError as e:
                if e.code == 0:
                    outbox_add(pid, q)
                else:
                    raise
            ok += 1
        return ok

    def done(r, err):
        if err is not None:
            show_toast("err", "فشل جزئي", str(err)[:40], "home", 2.4)
            return
        log("add-all: %d products, %d pieces" % (len(jobs), total))
        show_toast("ok", "+%d" % total, "تمت إضافة %d مجسم" % len(jobs), "home", 2.2)
        bg(fetch_items, lambda r2, e2: None)
    bg(fn, done, "جارٍ الحفظ")


def do_link(item):
    code = st.link_code

    def fn():
        return woo("POST", "/link", {"product_id": item["id"], "code": code})

    def done(r, err):
        if err is None:
            for i, x in enumerate(ITEMS["list"]):
                if x["id"] == r["item"]["id"]:
                    ITEMS["list"][i] = r["item"]
            log("linked %s -> %s" % (code, item["title"]))
            show_toast("ok", "تم الربط", item["title"][:30], "home", 2.0)
        else:
            show_toast("err", "فشل الربط", err.msg[:40], "home", 2.6)
    bg(fn, done, "جارٍ الربط")


# ---------------------------------- الأحداث ----------------------------------
def list_open():
    if not st.list:
        return go_home()
    it = st.list[st.list_idx]
    if it.get("special") == "addall":
        st.mode, st.confirm = "confirm", {"total": it["total"], "count": it["count"]}
        bump()
    elif st.list_kind == "link":
        do_link(it)
    else:
        open_product(it, it["need"] if it["need"] > 0 else 1, st.list_kind)


def menu_enter():
    m = st.menu
    if m == 1:
        enter_list("need")
    elif m == 2:
        enter_list("all")
    elif m == 3:
        st.mode, st.count_n = "count_wait", 0
        bump()
    elif m == 4:
        enter_history()
    elif m == 5:
        st.mode, st.ord_idx = "orders", 0
        bump()
        refresh_sapi()
    elif m == 6:
        st.mode = "top"
        bump()
        refresh_sapi()


def on_wheel(d):
    nav(-d if INVERT_WHEEL else d)


def nav(d):
    if abs(d) >= 4:
        d *= 2                                      # لفّ سريع = قفزات أكبر
    if st.mode == "home":
        st.menu = (st.menu + d) % len(MENU)
    elif st.mode == "product":
        st.delta = max(-999, min(999, st.delta + d))
        if LOCAL_QTY:
            return                                  # الشاشة ترسم الرقم محلياً: ما نحتاج اعادة رسم
    elif st.mode == "list" and st.list:
        st.list_idx = (st.list_idx + d) % len(st.list)
        for k in (0, 1, -1):
            img_get(st.list[(st.list_idx + k) % len(st.list)].get("image"))
    elif st.mode == "history" and HIST:
        st.hist_idx = (st.hist_idx + d) % len(HIST)
        st.undo_armed = 0.0
    elif st.mode == "orders":
        st.ord_idx = (st.ord_idx + d) % (1 + len(orders_list()))
    else:
        return
    bump()


def on_button(kind):
    """kind: 1 ضغطة قصيرة، 2 ضغطة طويلة"""
    m = st.mode
    if m == "toast":
        return finish_toast()
    if m == "clock":
        if LOCK_ON and kind != 2:
            return
        return go_home()
    if m == "count_wait":
        return go_home() if kind == 2 else None
    if m in ("orders", "top"):
        return go_home() if kind == 2 else refresh_sapi()
    if m == "history":
        return go_home() if kind == 2 else hist_press()
    if m == "home":
        return menu_enter() if kind == 1 else None
    if m == "product":
        return do_save() if kind == 1 else do_cancel()
    if m == "list":
        return list_open() if kind == 1 else go_home()
    if m == "confirm":
        if kind == 1:
            return do_addall()
        st.mode = "list"
        return bump()
    if m == "link_wait":
        return go_home() if kind == 2 else None


def on_tap(x, y):
    m = st.mode
    if m == "toast":
        return finish_toast()
    if m == "clock":
        return None if LOCK_ON else go_home()
    if m == "count_wait":
        return go_home() if y < 75 else None
    if m == "orders":
        if y < 75:
            return go_home()
        if x < 110:
            return nav(-1)
        if x > SZ - 110:
            return nav(1)
        return refresh_sapi()
    if m == "top":
        return go_home() if y < 75 else refresh_sapi()
    if m == "history":
        if y < 75:
            return go_home()
        if x < 110:
            return nav(-1)
        if x > SZ - 110:
            return nav(1)
        return hist_press()
    if m == "home":
        if x < 110:
            return nav(-1)
        if x > SZ - 110:
            return nav(1)
        return menu_enter()
    if m == "product":
        if y > 335:
            return do_save() if x >= SZ // 2 else do_cancel()
        if 215 <= y <= 335:
            if x < 150:
                st.delta = max(-999, st.delta - 1)
                return bump()
            if x > 316:
                st.delta = min(999, st.delta + 1)
                return bump()
        if y < 75:
            return do_cancel()
        return
    if m == "list":
        if y < 75:
            return go_home()
        if x < 100:
            return nav(-1)
        if x > SZ - 100:
            return nav(1)
        return list_open()
    if m == "confirm":
        if y > 335:
            return do_addall() if x >= SZ // 2 else (setattr(st, "mode", "list") or bump())
    if m == "link_wait" and y < 75:
        return go_home()


def on_scan(code):
    code = code.strip()
    if not code:
        return
    with LOCK:
        touch_input()
        mode = st.mode
    log("scan: %s (mode %s)" % (code, mode))

    if mode == "link_wait":
        def done(item, err):
            if err is not None:
                return show_toast("err", "تعذر الاتصال", "", "home")
            if item:
                return show_toast("warn", "مربوط مسبقاً", item["title"][:30], "home", 2.4)
            st.link_code = code
            st.list_kind = "link"

            def d2(r, e2):
                st.list = build_list("link")
                st.list_idx = 0
                st.mode = "list"
                bump()
            if time.time() - ITEMS["t"] > 20:
                bg(fetch_items, d2)
            else:
                d2(None, None)
        bg(lambda: lookup_item(code), done)
        return

    def done(item, err):
        if err is not None:
            log("lookup failed: %s" % err)
            return show_toast("err", "تعذر الاتصال", "بالموقع", st.mode if st.mode != "toast" else "home")
        if not item:
            log("  (ليس مجسم - تجاهل)")
            return
        beep("ok")
        if st.mode == "product" and st.item and st.item["id"] == item["id"]:
            st.item = item
            if st.origin != "count":
                st.delta = min(999, st.delta + SCAN_ADDS)
            bump()
            return
        if st.mode == "product" and st.item and st.delta and AUTOSAVE_ON_SWITCH:
            outbox_add(st.item["id"], st.delta)
            log("auto-saved previous: %s %+d" % (st.item["title"], st.delta))
        if st.mode == "count_wait" or (st.mode == "product" and st.origin == "count"):
            open_product(item, 0, "count")
        else:
            open_product(item, SCAN_ADDS, "scan")
    bg(lambda: lookup_item(code), done)


# ================================ الرسم ================================
class Draw:
    def __init__(self):
        from PIL import Image, ImageDraw, ImageFont, features
        self.Image, self.ImageDraw, self.ImageFont = Image, ImageDraw, ImageFont
        self.raqm = bool(features.check("raqm"))
        self.shaper = None
        if not self.raqm:
            try:
                import arabic_reshaper
                from bidi.algorithm import get_display
                self.shaper = lambda t: get_display(arabic_reshaper.reshape(t))
            except Exception:
                pass
        self.fontpath = self._find_font()
        if not self.fontpath:
            raise RuntimeError("no TTF font (sudo apt install fonts-dejavu-core)")
        self._f = {}
        self._fa = {}
        self.arpath, self.ar_cov = self._find_ar_font()
        log("font: %s | arabic font: %s" % (os.path.basename(self.fontpath), os.path.basename(self.arpath) if self.arpath else "-"))
        self.black = Image.new("RGB", (SZ, SZ), (0, 0, 0))
        self.TB, self.RM = {}, {}
        self.bg_plain = self.theme_bg("dark")
        self.teal = (23, 126, 163)
        self.bg_teal = self.theme_bg("brand")
        self.logo = self._load_logo()

    @staticmethod
    def _find_font():
        for c in (os.environ.get("KNOB_FONT", ""),
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
                  "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
            if c and os.path.exists(c):
                return c

    def _find_ar_font(self):
        """خط عربي أجمل (Noto) اذا موجود. نتأكد أي أرقام/رموز يغطيها حتى ما تطلع مربعات"""
        import glob
        cands = [os.environ.get("KNOB_FONT_AR", "")]
        for pat in ("/usr/share/fonts/**/NotoSansArabic-Bold.ttf", "/usr/share/fonts/**/NotoKufiArabic-Bold.ttf",
                    "/usr/share/fonts/**/NotoSansArabicUI-Bold.ttf", "/usr/share/fonts/**/NotoNaskhArabic-Bold.ttf"):
            cands += sorted(glob.glob(pat, recursive=True))
        for c in cands:
            if c and os.path.exists(c):
                try:
                    f = self.ImageFont.truetype(c, 40)
                    nd = (f.getlength("\ue000"), f.getbbox("\ue000"))
                    cov = set(ch for ch in "0123456789+-:/%.()| " if (f.getlength(ch), f.getbbox(ch)) != nd or ch == " ")
                    return c, cov
                except Exception:
                    continue
        return None, set()

    def _ar_ok(self, text):
        if not self.arpath or not self.has_ar(text):
            return False
        for ch in str(text):
            if ord(ch) < 128 and not ch.isalpha() and ch in self.ar_cov:
                continue
            if ord(ch) < 128:
                return False
            if not (0x0600 <= ord(ch) <= 0x06FF or 0x0750 <= ord(ch) <= 0x077F or 0xFB50 <= ord(ch) <= 0xFEFF):
                return False
        return True

    def _gradient(self, c1=(18, 20, 30), c2=(32, 34, 52)):
        im = self.Image.new("RGB", (SZ, SZ))
        d = self.ImageDraw.Draw(im)
        for y in range(SZ):
            k = y / float(SZ)
            d.line([(0, y), (SZ, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(c1, c2)))
        return im

    def _load_logo(self):
        """شعار المتجر (أبيض على أزرق) -> صورة بيضاء بشفافية، حتى نرسمه فوق أي خلفية"""
        path = os.environ.get("KNOB_LOGO", os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png"))
        try:
            src = self.Image.open(path).convert("RGB")
            bgc = src.getpixel((4, 4))
            r = src.getchannel("R").point(lambda v: max(0, min(255, int((v - bgc[0]) * 255.0 / max(1, 255 - bgc[0])))))
            bbox = r.point(lambda v: 255 if v > 40 else 0).getbbox()
            if bbox:
                r = r.crop(bbox)
            white = self.Image.new("RGB", r.size, (255, 255, 255))
            white.putalpha(r)
            return white
        except Exception as e:
            log("logo not loaded (%s): %s" % (path, e))
            return None

    def draw_logo(self, im, cx, cy, size):
        """يلصق الشعار مركزه (cx,cy) وأكبر بُعد = size"""
        if self.logo is None:
            return
        lg = self.logo
        k = size / float(max(lg.size))
        lg = lg.resize((max(1, int(lg.width * k)), max(1, int(lg.height * k))), self.Image.LANCZOS)
        im.paste(lg, (int(cx - lg.width / 2), int(cy - lg.height / 2)), lg)

    def circle_image(self, im, url, cx, cy, r, ring=WHITE):
        """صورة المنتج بشكل دائري (للتأكد من المنتج الصحيح)"""
        Image, ImageDraw = self.Image, self.ImageDraw
        src = img_get(url) if url else None
        D = int(r * 2)
        if src is None:
            tile = Image.new("RGB", (D, D), (60, 64, 76))
        else:
            tile = src.resize((D, D), Image.LANCZOS)
        big = Image.new("L", (D * 3, D * 3), 0)
        ImageDraw.Draw(big).ellipse((0, 0, D * 3 - 1, D * 3 - 1), fill=255)
        mask = big.resize((D, D), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        d.ellipse([cx - r - 5, cy - r - 5, cx + r + 5, cy + r + 5], fill=ring)
        im.paste(tile, (int(cx - r), int(cy - r)), mask)

    def font(self, size, text=None):
        size = max(8, int(size))
        if text is not None and self._ar_ok(text):
            if size not in self._fa:
                try:
                    kw = {"layout_engine": self.ImageFont.Layout.RAQM} if self.raqm else {}
                    self._fa[size] = self.ImageFont.truetype(self.arpath, size, **kw)
                except Exception:
                    self._fa[size] = self.ImageFont.truetype(self.arpath, size)
            return self._fa[size]
        if size not in self._f:
            try:
                kw = {"layout_engine": self.ImageFont.Layout.RAQM} if self.raqm else {}
                self._f[size] = self.ImageFont.truetype(self.fontpath, size, **kw)
            except Exception:
                self._f[size] = self.ImageFont.truetype(self.fontpath, size)
        return self._f[size]

    @staticmethod
    def has_ar(t):
        return any("؀" <= c <= "ۿ" for c in t)

    def prep(self, text):
        text = str(text)
        if not self.has_ar(text):
            return text, {}
        if self.raqm:
            return text, {"direction": "rtl", "language": "ar"}
        if self.shaper:
            return self.shaper(text), {}
        return text, {}

    def width(self, d, text, font):
        t, kw = self.prep(text)
        try:
            return d.textlength(t, font=font, **kw)
        except Exception:
            return d.textlength(t, font=font)

    def text(self, d, x, y, text, size, fill=WHITE, align="center", maxw=None):
        """يرسم نص (x = المركز / اليمين / اليسار). maxw = يصغّر الخط ليتسع. يرجع الارتفاع المستعمل"""
        size = int(size)
        font = self.font(size, text)
        if maxw:
            while size > 14 and self.width(d, text, font) > maxw:
                size -= 2
                font = self.font(size, text)
        t, kw = self.prep(text)
        w = self.width(d, text, font)
        x = x - w / 2.0 if align == "center" else (x - w if align == "right" else x)
        try:
            d.text((x, y), t, font=font, fill=fill, **kw)
        except Exception:
            d.text((x, y), t, font=font, fill=fill)
        return int(size * 1.25)

    def wrap(self, d, text, size, maxw, max_lines=2):
        font = self.font(size, text)
        words, lines, cur = str(text).split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if self.width(d, trial, font) <= maxw or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip() + " …"
        return lines

    def title(self, d, y, text, size, maxw=340, max_lines=2, fill=WHITE):
        """عنوان متوسّط بسطرين كحد أقصى؛ يصغّر الخط إذا لزم. يرجع y بعد العنوان"""
        s = size
        while True:
            lines = self.wrap(d, text, s, maxw, max_lines)
            if all(self.width(d, l, self.font(s, l)) <= maxw for l in lines) or s <= 22:
                break
            s -= 2
        for l in lines:
            self.text(d, SZ / 2, y, l, s, fill)
            y += int(s * 1.25)
        return y

    def title_box(self, d, top, box_h, text, size, maxw=330, fill=WHITE):
        """يكتب النص داخل مربع ارتفاعه box_h: يصغّر الخط ويلفّ بسطرين حتى يتسع، ويوسّطه عمودياً"""
        s = size
        while True:
            lines = self.wrap(d, text, s, maxw, 2)
            h = len(lines) * int(s * 1.25)
            ok = h <= box_h and all(self.width(d, l, self.font(s, l)) <= maxw for l in lines)
            if ok or s <= 18:
                break
            s -= 2
        y = top + max(0, (box_h - h) // 2)
        for l in lines:
            self.text(d, SZ / 2, y, l, s, fill, maxw=maxw)
            y += int(s * 1.25)
        return top + box_h

    # ---------------- عناصر مشتركة ----------------
    def background(self, url=None, dim=0.36):
        from PIL import ImageEnhance
        im = img_get(url) if url else None
        if im is None:
            return self.bg_plain.copy()
        return ImageEnhance.Brightness(im).enhance(dim)

    def blur_bg(self, url, dim=0.38):
        """خلفية = صورة المنتج مغوّشة ومعتّمة. اذا الصورة مو جاهزة يرجع الخلفية العادية"""
        from PIL import ImageFilter, ImageEnhance
        im = img_get(url) if url else None
        if im is None:
            return self.bg_plain.copy()
        bl = BLUR.get(url)
        if bl is None:
            small = im.resize((80, 80), self.Image.BILINEAR).filter(ImageFilter.GaussianBlur(4))
            bl = ImageEnhance.Brightness(small.resize((SZ, SZ), self.Image.BICUBIC)).enhance(dim)
            BLUR[url] = bl
            while len(BLUR) > 40:
                BLUR.popitem(last=False)
        return bl.copy()

    def theme_bg(self, key):
        """خلفية لون واحد واضح (لون المتجر) بدون توهّج"""
        c = self.TB.get(key)
        if c is None:
            c = self.Image.new("RGB", (SZ, SZ), BRAND_DARK if key == "dark" else BRAND)
            self.TB[key] = c
        return c.copy()

    def glass(self, im, cx, cy, r, alpha=40, ring_alpha=110):
        """دائرة شبه شفافة بيضاء (خلفية الأيقونات)"""
        Image, ImageDraw = self.Image, self.ImageDraw
        S, D = 3, 2 * r + 4
        big = Image.new("L", (D * S, D * S), 0)
        dr = ImageDraw.Draw(big)
        box = (2 * S, 2 * S, (D - 2) * S, (D - 2) * S)
        dr.ellipse(box, fill=alpha)
        dr.ellipse(box, outline=ring_alpha, width=3 * S)
        m = big.resize((D, D), Image.LANCZOS)
        im.paste((255, 255, 255), (int(cx - D / 2), int(cy - D / 2)), m)

    def icon(self, im, kind, cx, cy, size=84):
        """ايقونات بيضاء مرسومة بالكود (مع تنعيم)"""
        Image, ImageDraw = self.Image, self.ImageDraw
        K = 4
        N = size * K
        m = Image.new("L", (N, N), 0)
        d = ImageDraw.Draw(m)
        c, u = N / 2.0, N / 100.0

        def R(x0, y0, x1, y1):
            return [c + x0 * u, c + y0 * u, c + x1 * u, c + y1 * u]

        def L(*pts, w=6):
            d.line([(c + x * u, c + y * u) for x, y in pts], fill=255, width=int(w * u), joint="curve")
        if kind == "scan":
            for sx in (-1, 1):
                for sy in (-1, 1):
                    L((sx * 44, sy * 26), (sx * 44, sy * 44), (sx * 26, sy * 44), w=7)
            x = -20
            for i, w in enumerate([4, 3, 8, 3, 4, 3, 3, 8, 4]):
                if i % 2 == 0:
                    d.rectangle(R(x, -22, x + w, 22), fill=255)
                x += w
        elif kind == "need":
            d.rounded_rectangle(R(-40, -6, 40, 26), radius=int(8 * u), fill=255)
            d.rectangle(R(-24, -36, 24, -4), fill=255)
            d.rectangle(R(-18, -30, 18, -4), fill=0)
            d.rectangle(R(-22, 12, 22, 42), fill=255)
            d.rectangle(R(-14, 21, 14, 24), fill=0)
            d.rectangle(R(-14, 30, 14, 33), fill=0)
            d.ellipse(R(26, 1, 34, 9), fill=0)
        elif kind == "all":
            for x0, y0 in ((-40, -40), (4, -40), (-40, 4), (4, 4)):
                d.rounded_rectangle(R(x0, y0, x0 + 36, y0 + 36), radius=int(9 * u), fill=255)
        elif kind == "count":
            d.rounded_rectangle(R(-30, -38, 30, 42), radius=int(9 * u), fill=255)
            d.rounded_rectangle(R(-22, -28, 22, 34), radius=int(5 * u), fill=0)
            d.rounded_rectangle(R(-12, -46, 12, -30), radius=int(5 * u), fill=255)
            L((-13, 5), (-3, 16), (14, -8), w=8)
        elif kind == "hist":
            d.ellipse(R(-38, -38, 38, 38), fill=255)
            d.ellipse(R(-30, -30, 30, 30), fill=0)
            L((0, 0), (0, -20), w=6)
            L((0, 0), (16, 8), w=6)
        elif kind == "orders":
            d.ellipse(R(-18, -44, 18, -6), fill=255)
            d.ellipse(R(-11, -37, 11, -6), fill=0)
            d.rounded_rectangle(R(-36, -22, 36, 40), radius=int(9 * u), fill=255)
            L((-12, 4), (-3, 14), (14, -6), w=7)
            d.line([(c + x * u, c + y * u) for x, y in ((-12, 4), (-3, 14), (14, -6))], fill=0, width=int(7 * u), joint="curve")
        elif kind == "top":
            pts = []
            for i in range(10):
                ang = -math.pi / 2 + i * math.pi / 5
                r_ = 44 if i % 2 == 0 else 19
                pts.append((c + r_ * u * math.cos(ang), c + (r_ * math.sin(ang) + 3) * u))
            d.polygon(pts, fill=255)
        else:   # link
            d.rounded_rectangle(R(-44, -16, 8, 16), radius=int(16 * u), fill=255)
            d.rounded_rectangle(R(-34, -8, -2, 8), radius=int(8 * u), fill=0)
            d.rounded_rectangle(R(-8, -16, 44, 16), radius=int(16 * u), fill=255)
            d.rounded_rectangle(R(2, -8, 34, 8), radius=int(8 * u), fill=0)
            d.rounded_rectangle(R(-8, -16, 8, 16), radius=int(10 * u), fill=255)
        m = m.resize((size, size), Image.LANCZOS)
        im.paste((255, 255, 255), (int(cx - size / 2), int(cy - size / 2)), m)

    def pad(self, im):
        """466 -> 480: نمدّد الحواف 7 بكسل حتى الخلفية تغطي الشاشة كلها"""
        Image = self.Image
        c = Image.new("RGB", (OUT, OUT))
        c.paste(im, (OFF, OFF))
        c.paste(im.crop((0, 0, SZ, 1)).resize((SZ, OFF)), (OFF, 0))
        c.paste(im.crop((0, SZ - 1, SZ, SZ)).resize((SZ, OFF)), (OFF, OFF + SZ))
        c.paste(c.crop((OFF, 0, OFF + 1, OUT)).resize((OFF, OUT)), (0, 0))
        c.paste(c.crop((OFF + SZ - 1, 0, OFF + SZ, OUT)).resize((OFF, OUT)), (OFF + SZ, 0))
        return c

    def ring_mask(self, w=10):
        """حلقة على حافة الدائرة (قطرها 480) بحواف ناعمة"""
        m = self.RM.get(w)
        if m is None:
            Image, ImageDraw = self.Image, self.ImageDraw
            S = 4
            big = Image.new("L", (OUT * S, OUT * S), 0)
            ImageDraw.Draw(big).ellipse((0, 0, OUT * S - 1, OUT * S - 1), outline=255, width=w * S)
            m = big.resize((OUT, OUT), Image.LANCZOS)
            self.RM[w] = m
        return m

    def disc_mask(self):
        """قناع دائرة كاملة بحواف ناعمة: خارجها أسود (الشاشة مربعة وراها عدسة دائرية)"""
        m = self.RM.get("disc")
        if m is None:
            Image, ImageDraw = self.Image, self.ImageDraw
            S = 4
            big = Image.new("L", (OUT * S, OUT * S), 0)
            ImageDraw.Draw(big).ellipse((0, 0, OUT * S - 1, OUT * S - 1), fill=255)
            m = big.resize((OUT, OUT), Image.LANCZOS)
            self.RM["disc"] = m
        return m

    def button(self, d, box, label, color, size=26):
        d.rounded_rectangle(box, radius=18, fill=color)
        bw = box[2] - box[0]
        self.text(d, box[0] + bw / 2.0, box[1] + (box[3] - box[1] - size * 1.25) / 2.0, label, size, WHITE, maxw=bw - 12)

    def ring(self, d, frac, color=ACCENT):
        d.arc([8, 8, SZ - 9, SZ - 9], start=-90, end=-90 + 360 * max(0.0, min(1.0, frac)), fill=color, width=6)

    def finish(self, im, ring=None):
        canvas = self.pad(im)
        if ring:
            canvas.paste(ring, (0, 0), self.ring_mask(10))
        else:
            canvas.paste(mix(BRAND, WHITE, 0.55), (0, 0), self.ring_mask(5))   # اطار رفيع يحدد الحافة
        canvas = self.Image.composite(canvas, self.Image.new("RGB", (OUT, OUT), (0, 0, 0)), self.disc_mask())
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=JPEG_QUALITY, progressive=False, subsampling=0)   # 4:4:4 = حواف وخط أوضح
        return buf.getvalue()

    def view_home(self):
        k = st.menu
        im = self.theme_bg("brand")
        d = self.ImageDraw.Draw(im)
        self.draw_logo(im, SZ / 2, 52, 54)
        self.icon(im, MENU_ICON[k], SZ / 2, 150, 92)
        title, sub = MENU[k]
        n, pieces = need_stats()
        sd = SAPI["data"]
        if k == 1:
            sub = "%d مجسم  |  %d قطعة" % (n, pieces) if ITEMS["t"] else "..."
        elif k == 2:
            sub = "%d منتج" % len(ITEMS["list"]) if ITEMS["t"] else "..."
        elif k == 4:
            sub = "%d عملية" % len(HIST) if HIST else "ماكو عمليات بعد"
        elif k == 5:
            sub = ("%d طلب جديد" % sd.get("new", 0)) if sd else ("حدّث الإضافة" if SAPI["err"] == "404" else "...")
            if sd and sd.get("new", 0) > 0:
                cx, cy = SZ / 2 + 50, 150 - 46
                d.ellipse([cx - 19, cy - 19, cx + 19, cy + 19], fill=RED, outline=WHITE, width=3)
                self.text(d, cx, cy - 13, str(sd["new"]), 22, WHITE, maxw=30)
        self.text(d, SZ / 2, 212, title, 46, WHITE, maxw=340)
        self.text(d, SZ / 2, 282, sub, 24, LIGHT, maxw=320)
        if k == 0:
            a, r = stats_today()
            self.text(d, SZ / 2, 322, "اليوم", 15, LIGHT)
            d.rounded_rectangle([SZ / 2 - 96, 342, SZ / 2 + 96, 384], radius=21, fill=INK)
            self.text(d, SZ / 2 - 46, 347, "▲%d" % a, 27, GREEN)
            self.text(d, SZ / 2 + 46, 347, "▼%d" % r, 27, SOFTRED)
        for i in range(len(MENU)):
            cx = SZ / 2 + (i - (len(MENU) - 1) / 2.0) * 22
            d.ellipse([cx - 5, 404 - 5, cx + 5, 404 + 5], fill=WHITE if i == k else mix(BRAND, WHITE, 0.45))
        self.text(d, SZ / 2, 422, "دوّر أو المس · اضغط للدخول", 15, LIGHT, maxw=200)
        return self.finish(im)

    def view_product(self):
        it = st.item or {}
        im = self.blur_bg(it.get("image"))
        d = self.ImageDraw.Draw(im)
        need = it.get("need", 0)
        cnt = st.origin == "count"
        self.circle_image(im, it.get("image"), SZ // 2, 96, 60)
        chip, col = ("ناقص %d" % need, RED) if need > 0 else (("قيد الطباعة" if it.get("in_print") else "متوفر"), GREEN if it.get("stock", 0) > 0 else (110, 114, 124))
        if cnt:
            chip, col = "وضع الجرد", (120, 90, 230)
        cw = max(100, self.width(d, chip, self.font(18, chip)) + 30)
        d.rounded_rectangle([SZ / 2 - cw / 2, 142, SZ / 2 + cw / 2, 172], radius=15, fill=col)
        self.text(d, SZ / 2, 145, chip, 18, WHITE)
        self.title_box(d, 176, 68, it.get("title", ""), 26, 330)

        stock, delta = it.get("stock", 0), st.delta
        self.text(d, 160, 252, "المخزن", 17, MUTED)
        self.text(d, 160, 268, str(stock), 56, WHITE)
        self.text(d, 312, 252, "العدد الفعلي" if cnt else "التغيير", 17, MUTED)
        if not LOCAL_QTY:
            dcol = GREEN if delta > 0 else (RED if delta < 0 else MUTED)
            if cnt:
                self.text(d, 312, 268, str(stock + delta), 56, dcol if delta else WHITE)
            else:
                self.text(d, 312, 268, "%+d" % delta if delta else "0", 56, dcol)
        for cx, sym in ((52, "−"), (414, "+")):
            d.ellipse([cx - 26, 290 - 26, cx + 26, 290 + 26], outline=(120, 124, 134), width=3)
            self.text(d, cx, 290 - 22, sym, 40, WHITE)
        self.button(d, (106, 350, 226, 404), "إلغاء", (90, 94, 104), 24)
        self.button(d, (240, 350, 360, 404), "حفظ", GREEN, 26)
        if not LOCAL_QTY:
            hint = "سيصبح المخزن: %d" % max(0, stock + delta) if delta else "اضغط الريوتري للحفظ"
            self.text(d, SZ / 2, 414, hint, 18, WHITE if delta else MUTED)
        return self.finish(im, stock_color(it))

    def view_list(self):
        n = len(st.list)
        it = st.list[st.list_idx] if n else {}
        sp = it.get("special")
        im = self.bg_plain.copy() if (sp or not LIST_BLUR) else self.blur_bg(it.get("image"))
        d = self.ImageDraw.Draw(im)
        if not sp:
            self.circle_image(im, it.get("image"), SZ // 2, 140, 66)
        rc = None if sp else (ACCENT if st.list_kind == "link" else stock_color(it))
        self.text(d, SZ / 2, 28, "%d/%d" % (st.list_idx + 1, n), 22, WHITE)
        if st.list_kind == "link":
            self.text(d, SZ / 2, 64, "اربط الباركود  %s" % st.link_code[:18], 18, AMBER, maxw=300)
        if sp == "addall":
            self.title(d, 120, "أضف كل الناقص", 50, 340, 2)
            self.text(d, SZ / 2, 250, "+%d" % it["total"], 80, GREEN)
            self.text(d, SZ / 2, 345, "%d مجسم" % it["count"], 26, WHITE)
            self.text(d, SZ / 2, 405, "اضغط للمتابعة", 17, MUTED)
            return self.finish(im, rc)
        y = self.title_box(d, 196, 70, it.get("title", ""), 30, 330)
        if st.list_kind == "need":
            self.text(d, SZ / 2, 284, "ناقص %d" % it["need"], 50, ACCENT, maxw=300)
            sub = "متوفر %d  |  طلبات %d" % (it["stock"], it["pending"])
        elif st.list_kind == "link":
            lk = it.get("barcode") or it.get("sku")
            self.text(d, SZ / 2, 290, "مربوط" if lk else "بدون باركود", 36, GREEN if lk else AMBER)
            sub = "متوفر %d" % it["stock"]
        else:
            self.text(d, SZ / 2, 276, str(it["stock"]), 56, WHITE)
            sub = "متوفر" + ("  |  ناقص %d" % it["need"] if it["need"] else "")
        self.text(d, SZ / 2, 346, sub, 22, MUTED, maxw=320)
        self.text(d, SZ / 2, 392, "اضغط للفتح  ·  طويلة = رجوع", 16, MUTED, maxw=250)
        return self.finish(im, rc)

    def view_orders(self):
        """صفحة 0 = ملخص، وبعدها كل طلب جديد/معلّق بصفحة (دوّر للتنقل)"""
        sd = SAPI["data"]
        orders = orders_list()
        pages = 1 + len(orders)
        i = max(0, min(st.ord_idx, pages - 1))
        im = self.theme_bg("brand")
        d = self.ImageDraw.Draw(im)
        if i == 0 or not sd:
            self.draw_logo(im, SZ / 2, 52, 54)
            self.text(d, SZ / 2, 96, "الطلبات", 34, WHITE)
            if not sd:
                self.text(d, SZ / 2, 200, "حدّث الإضافة (1.5)" if SAPI["err"] == "404" else "...", 34, WHITE, maxw=320)
                return self.finish(im)
            self.text(d, SZ / 2, 150, str(sd.get("new", 0)), 124, WHITE, maxw=300)
            self.text(d, SZ / 2, 292, "طلب جديد ينتظر التجهيز", 24, LIGHT, maxw=320)
            d.rounded_rectangle([SZ / 2 - 150, 336, SZ / 2 + 150, 384], radius=24, fill=INK)
            tot = "{:,}".format(int(sd.get("today_total", 0)))
            self.text(d, SZ / 2, 344, "اليوم: %d طلب  |  %s %s" % (sd.get("today_orders", 0), tot, sd.get("currency", "")), 20, WHITE, maxw=280)
            self.text(d, SZ / 2, 394, ("دوّر لعرض الطلبات  ›" if orders else ("ماكو طلبات معلّقة" if "orders" in sd else "حدّث الإضافة (1.5)")), 16, LIGHT, maxw=240)
            return self.finish(im)
        o = orders[i - 1]
        self.text(d, SZ / 2, 24, "%d/%d" % (i, len(orders)), 20, WHITE)
        self.text(d, SZ / 2, 54, "طلب #%s" % o.get("id", ""), 34, WHITE, maxw=300)
        hold = o.get("status") == "on-hold"
        chip, col = ("قيد الانتظار", AMBER) if hold else ("جديد", GREEN)
        cw = max(96, self.width(d, chip, self.font(18, chip)) + 34)
        d.rounded_rectangle([SZ / 2 - cw / 2, 100, SZ / 2 + cw / 2, 130], radius=15, fill=col)
        self.text(d, SZ / 2, 103, chip, 18, INK if hold else WHITE)
        self.text(d, SZ / 2, 142, o.get("name") or "زبون", 30, WHITE, maxw=320)
        meta = " · ".join([x for x in (o.get("city"), ago(o["ts"]) if o.get("ts") else "") if x])
        self.text(d, SZ / 2, 184, meta, 19, LIGHT, maxw=320)
        items = o.get("items", [])[:3]
        y = 212
        for it in items:
            d.rounded_rectangle([64, y, SZ - 64, y + 33], radius=16, fill=INK)
            self.text(d, SZ - 84, y + 4, it.get("n", ""), 20, WHITE, align="right", maxw=250)
            self.text(d, 84, y + 3, "×%d" % it.get("q", 1), 22, AMBER, align="left")
            y += 37
        more = o.get("more", 0) + max(0, len(o.get("items", [])) - 3)
        if more:
            self.text(d, SZ / 2, y - 1, "ومنتجات أخرى (%d)" % more, 15, LIGHT)
        tot = "{:,} {}".format(int(float(o.get("total", 0))), sd.get("currency", ""))
        self.text(d, SZ / 2, 356, tot, 36, WHITE, maxw=280)
        self.text(d, SZ / 2, 406, "دوّر للتالي · طويلة = رجوع", 15, LIGHT, maxw=190)
        return self.finish(im)

    def view_top(self):
        sd = SAPI["data"]
        im = self.theme_bg("brand")
        d = self.ImageDraw.Draw(im)
        self.text(d, SZ / 2, 40, "الأكثر مبيعاً هذا الأسبوع", 24, WHITE, maxw=250)
        top = (sd or {}).get("top", [])
        if not top:
            self.text(d, SZ / 2, 200, ("حدّث الإضافة (1.4)" if SAPI["err"] == "404" else ("لا توجد مبيعات" if sd else "...")), 32, WHITE, maxw=320)
            return self.finish(im)
        for i, t in enumerate(top[:3]):
            y = 128 + i * 98
            d.rounded_rectangle([46, y - 40, SZ - 46, y + 40], radius=40, fill=INK)
            self.circle_image(im, t.get("image"), 86, y, 32, ring=WHITE)
            self.text(d, 138, y - 30, "%d" % (i + 1), 22, mix(BRAND, WHITE, 0.4), align="left")
            self.text(d, SZ - 66, y - 26, t.get("title", ""), 22, WHITE, align="right", maxw=210)
            self.text(d, SZ - 66, y + 6, "%d قطعة" % t.get("qty", 0), 20, GREEN, align="right")
        return self.finish(im)


    def view_confirm(self):
        c = st.confirm or {}
        im = self.bg_plain.copy()
        d = self.ImageDraw.Draw(im)
        self.title(d, 70, "تأكيد الإضافة", 44, 340, 1)
        self.text(d, SZ / 2, 150, "+%d" % c.get("total", 0), 90, GREEN)
        self.text(d, SZ / 2, 270, "قطعة لـ %d مجسم" % c.get("count", 0), 28, WHITE)
        self.button(d, (106, 350, 226, 404), "إلغاء", (90, 94, 104), 24)
        self.button(d, (240, 350, 360, 404), "تأكيد", GREEN, 26)
        return self.finish(im)

    def view_toast(self):
        t = st.toast or {}
        kind = t.get("kind", "ok")
        col = {"ok": GREEN, "err": RED, "warn": AMBER}.get(kind, GREEN)
        im = self.bg_plain.copy()
        d = self.ImageDraw.Draw(im)
        cx, cy, r = SZ // 2, 130, 56
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
        if kind == "ok":
            d.line([(cx - 26, cy + 2), (cx - 8, cy + 22), (cx + 28, cy - 20)], fill=WHITE, width=12, joint="curve")
        elif kind == "err":
            d.line([(cx - 22, cy - 22), (cx + 22, cy + 22)], fill=WHITE, width=12)
            d.line([(cx - 22, cy + 22), (cx + 22, cy - 22)], fill=WHITE, width=12)
        else:
            d.line([(cx, cy - 26), (cx, cy + 8)], fill=WHITE, width=12)
            d.ellipse([cx - 7, cy + 18, cx + 7, cy + 32], fill=WHITE)
        self.text(d, SZ / 2, 215, t.get("title", ""), 58, WHITE, maxw=340)
        self.text(d, SZ / 2, 305, t.get("sub", ""), 26, MUTED, maxw=340)
        return self.finish(im)

    def view_busy(self):
        im = self.bg_teal.copy()
        d = self.ImageDraw.Draw(im)
        self.draw_logo(im, SZ / 2, 150, 150)
        a = int(time.time() * 300) % 360
        d.arc([SZ / 2 - 110, 40, SZ / 2 + 110, 260], start=a, end=a + 70, fill=WHITE, width=6)
        self.text(d, SZ / 2, 290, st.busy_text or "جارٍ التحميل", 34, WHITE, maxw=340)
        return self.finish(im)

    def view_link_wait(self):
        im = self.bg_plain.copy()
        d = self.ImageDraw.Draw(im)
        self.title(d, 110, "امسح الباركود الجديد", 46, 340, 2)
        self.text(d, SZ / 2, 250, "ثم اختر المنتج", 28, MUTED)
        self.text(d, SZ / 2, 400, "ضغطة طويلة = رجوع", 17, MUTED)
        return self.finish(im)

    def view_sleep(self):
        """سبات: شعار المتجر الأبيض بالوسط على لون المتجر"""
        im = self.bg_teal.copy()
        self.draw_logo(im, SZ / 2, SZ / 2, 250)
        return self.finish(im)

    AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    AR_MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول",
                 "تشرين الأول", "تشرين الثاني", "كانون الأول"]

    def view_clock(self):
        """ساعة تظهر بعد اللمس لثواني ثم يبدأ النظام"""
        now = datetime.now()
        im = self.bg_teal.copy()
        d = self.ImageDraw.Draw(im)
        self.draw_logo(im, SZ / 2, 54, 54)
        h12 = now.hour % 12 or 12
        self.text(d, SZ / 2, 106, "%d:%02d" % (h12, now.minute), 118, WHITE, maxw=380)
        self.text(d, SZ / 2, 250, "ص" if now.hour < 12 else "م", 34, LIGHT)
        date = "%s  %d  %s" % (self.AR_DAYS[now.weekday()], now.day, self.AR_MONTHS[now.month - 1])
        self.text(d, SZ / 2, 304, date, 30, WHITE, maxw=360)
        n, pieces = need_stats()
        if n and ITEMS["t"]:
            self.text(d, SZ / 2, 356, "ناقص للطباعة: %d قطعة" % pieces, 22, WHITE, maxw=300)
        if LOCK_ON:
            self.text(d, SZ / 2, 404, "اضغط مطولاً للفتح", 18, LIGHT)
        return self.finish(im)

    def view_count_wait(self):
        im = self.theme_bg("brand")
        d = self.ImageDraw.Draw(im)
        self.draw_logo(im, SZ / 2, 52, 54)
        self.icon(im, "count", SZ / 2, 150, 92)
        self.text(d, SZ / 2, 220, "جرد المخزن", 48, WHITE, maxw=360)
        self.text(d, SZ / 2, 282, "امسح باركود المنتج", 26, LIGHT, maxw=340)
        if st.count_n:
            d.rounded_rectangle([SZ / 2 - 110, 336, SZ / 2 + 110, 380], radius=22, fill=INK)
            self.text(d, SZ / 2, 343, "تم جرد %d منتج" % st.count_n, 24, WHITE, maxw=200)
        self.text(d, SZ / 2, 416, "ضغطة طويلة = رجوع", 16, LIGHT)
        return self.finish(im)

    def view_history(self):
        n = len(HIST)
        if not n:
            return self.view_home()
        i = max(0, min(st.hist_idx, n - 1))
        e = HIST[i]
        url = e.get("image")
        src = img_get(url) if url else None
        im = self.blur_bg(url) if (src is not None and LIST_BLUR) else self.theme_bg("dark")
        d = self.ImageDraw.Draw(im)
        self.text(d, SZ / 2, 22, "%d/%d" % (i + 1, n), 20, WHITE)
        self.circle_image(im, url, SZ // 2, 106, 52)
        self.title_box(d, 168, 68, e.get("title", ""), 26, 330)
        dl = e.get("delta", 0)
        self.text(d, SZ / 2, 244, "%+d" % dl, 64, GREEN if dl > 0 else RED)
        self.text(d, SZ / 2, 326, ago(e.get("t", time.time())), 20, LIGHT)
        if time.time() - st.undo_armed <= 4:
            d.rounded_rectangle([SZ / 2 - 130, 366, SZ / 2 + 130, 408], radius=21, fill=RED)
            self.text(d, SZ / 2, 372, "اضغط مرة ثانية للتأكيد", 21, WHITE, maxw=240)
        else:
            self.text(d, SZ / 2, 372, "اضغط للتراجع عن العملية", 20, WHITE, maxw=300)
            self.text(d, SZ / 2, 402, "طويلة = رجوع", 15, LIGHT)
        return self.finish(im, mix(BRAND, WHITE, 0.25))

    def render(self):
        if st.asleep:
            return self.view_sleep()
        return {"home": self.view_home, "clock": self.view_clock, "count_wait": self.view_count_wait,
                "history": self.view_history, "orders": self.view_orders, "top": self.view_top, "product": self.view_product, "list": self.view_list, "confirm": self.view_confirm,
                "toast": self.view_toast, "busy": self.view_busy, "link_wait": self.view_link_wait}.get(st.mode, self.view_home)()


DRAW = None
_FRAME = {"ver": -1, "mode": "", "data": b""}


def frame_bytes():
    with LOCK:
        # شاشة التحميل متحركة، فلا نخزنها بالكاش
        if _FRAME["ver"] != st.ver or st.mode == "busy":
            _t0 = time.time()
            _FRAME["data"] = DRAW.render()
            _FRAME["ver"] = st.ver
            _ms = (time.time() - _t0) * 1000
            if _ms > SLOW_LOG_MS:
                log("render %.0f ms, %d KB, mode=%s" % (_ms, len(_FRAME["data"]) // 1024, st.mode))
        return st.ver, _FRAME["data"]


# ================================ HTTP ================================
PREVIEW = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Knob preview</title><body style="margin:0;background:#111;color:#eee;font:16px sans-serif;text-align:center">
<p>معاينة الشاشة - اضغط على الصورة للمس</p>
<img id=f style="width:min(92vw,480px);border-radius:50%;background:#000" onclick="tap(event)">
<p><button onclick="s('d=-1')">◀ ويل</button> <button onclick="s('b=1')">ضغط</button> <button onclick="s('b=2')">ضغط طويل</button> <button onclick="s('d=1')">ويل ▶</button></p>
<p><input id=c placeholder="باركود" style="padding:8px"><button onclick="s('scan='+encodeURIComponent(c.value))">مسح</button></p>
<script>const T=new URLSearchParams(location.search).get('t')||'';
function s(q){fetch('/sim?t='+T+'&'+q).then(r)}function r(){document.getElementById('f').src='/frame.jpg?t='+T+'&_='+Date.now()}
function tap(e){const b=e.target.getBoundingClientRect();s('tx='+Math.max(0,Math.round((e.clientX-b.left)/b.width*480)-7)+'&ty='+Math.max(0,Math.round((e.clientY-b.top)/b.height*480)-7))}
setInterval(r,800);r()</script>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _auth(self, q):
        if not TOKEN:
            return True
        return self.headers.get("X-Token") == TOKEN or q.get("t", [""])[0] == TOKEN

    def _apply(self, q):
        def num(k, default=0):
            try:
                return int(q.get(k, [default])[0])
            except (ValueError, TypeError):
                return default
        d, b, tx, ty = num("d"), num("b"), num("tx", -1), num("ty", -1)
        qv = num("q", None)
        scan = q.get("scan", [""])[0]
        if d or b or tx >= 0 or qv is not None:
            with LOCK:
                woke = touch_input()
                if not woke:
                    if qv is not None and st.mode == "product":
                        st.delta = max(-999, min(999, qv))      # الشاشة هي المرجع للكمية
                    if d:
                        on_wheel(d)
                    if b:
                        on_button(b)
                    if tx >= 0 and ty >= 0:
                        on_tap(tx, ty)
        if scan:
            on_scan(scan)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/health":
            return self._send(200, json.dumps({"ok": True, "v": VERSION, "ver": st.ver, "mode": st.mode,
                                               "outbox": len(OUTBOX)}).encode(), "application/json")
        if not self._auth(q):
            return self._send(401, b"bad token", "text/plain")
        try:
            if u.path == "/sync":
                st.last_seen = time.time()
                self._apply(q)
                try:
                    cv = int(q.get("v", ["-1"])[0])
                except ValueError:
                    cv = -1
                with LOCK:
                    ver, data = frame_bytes()
                    stock_ = (st.item or {}).get("stock", 0)
                    cnt_ = st.mode == "product" and st.origin == "count"
                    hdr = {"X-Ver": ver, "X-Mode": st.mode, "X-Delta": st.delta, "X-Stock": stock_,
                           "X-Cnt": 1 if cnt_ else 0, "X-Base": stock_ if cnt_ else 0}
                    same = cv == ver and st.mode != "busy"
                if same:
                    return self._send(204, b"", extra=hdr)
                return self._send(200, data, "image/jpeg", hdr)
            if u.path == "/frame.jpg":
                ver, data = frame_bytes()
                return self._send(200, data, "image/jpeg", {"X-Ver": ver})
            if u.path == "/sim":
                self._apply(q)
                return self._send(200, b"ok", "text/plain")
            if u.path == "/preview":
                return self._send(200, PREVIEW.encode("utf-8"), "text/html; charset=utf-8")
        except Exception as e:
            log("HTTP error: %r" % (e,))
            return self._send(500, str(e).encode("utf-8", "replace"), "text/plain")
        self._send(404, b"not found", "text/plain")


# ================================ السكنر ================================
def scanner_loop():
    try:
        from evdev import InputDevice, list_devices, ecodes
    except ImportError:
        log("evdev غير مثبت -> السكنر معطّل (sudo apt install python3-evdev). الشاشة تشتغل بدونه.")
        return
    keys = {}
    for i in range(10):
        keys["KEY_%d" % i] = (str(i), ")!@#$%^&*("[i])
        keys["KEY_KP%d" % i] = (str(i), str(i))
    for c in "abcdefghijklmnopqrstuvwxyz":
        keys["KEY_" + c.upper()] = (c, c.upper())
    keys.update({"KEY_MINUS": ("-", "_"), "KEY_DOT": (".", ">"), "KEY_SLASH": ("/", "?"), "KEY_SPACE": (" ", " "),
                 "KEY_KPMINUS": ("-", "-"), "KEY_KPDOT": (".", "."), "KEY_EQUAL": ("=", "+"), "KEY_SEMICOLON": (";", ":")})
    prev, prev_t = None, 0.0
    while True:
        dev = None
        for path in list_devices():
            d = InputDevice(path)
            if SCANNER_NAME.lower() in d.name.lower() and ecodes.EV_KEY in d.capabilities():
                dev = d
                break
        if not dev:
            log("ماكو قارئ باركود... أحاول بعد 5 ثواني")
            time.sleep(5)
            continue
        log("القارئ: %s" % dev.name)
        buf, shift = "", False
        try:
            for ev in dev.read_loop():                       # بدون grab: الأنظمة الثانية تبقى تشتغل
                if ev.type != ecodes.EV_KEY:
                    continue
                name = ecodes.KEY.get(ev.code)
                if isinstance(name, list):
                    name = name[0]
                if name in ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"):
                    shift = ev.value != 0
                    continue
                if ev.value != 1:
                    continue
                if name in ("KEY_ENTER", "KEY_KPENTER"):
                    code, buf = buf, ""
                    now = time.time()
                    if code and not (code == prev and now - prev_t < DEBOUNCE_SEC):
                        on_scan(code)
                    prev, prev_t = code, now
                elif name in keys:
                    buf += keys[name][1 if shift else 0]
        except OSError:
            log("انفصل القارئ، أعيد الاتصال...")
            time.sleep(2)


def ticker():
    last_refresh = 0.0
    last_stats = 0.0
    while True:
        time.sleep(0.25)
        with LOCK:
            now = time.time()
            if st.mode == "toast" and st.toast and now >= st.toast["until"]:
                finish_toast()
            elif st.mode == "clock":
                if not LOCK_ON and now >= st.clock_until:
                    go_home()
                elif datetime.now().minute != st.clock_min:
                    st.clock_min = datetime.now().minute
                    bump()
            elif st.mode in ("product", "list", "confirm", "link_wait", "count_wait", "history", "orders", "top") and now - st.last_input > IDLE_HOME_SEC \
                    and not (st.mode == "product" and st.delta):
                go_home()
            if not st.asleep and now - st.last_input > SLEEP_SEC:
                st.asleep = True
                bump()
            due = st.mode == "home" and not st.asleep and now - last_refresh > 60
        if due:
            last_refresh = time.time()
            threading.Thread(target=lambda: _safe(fetch_items), daemon=True).start()
        if time.time() - last_stats > 40:
            last_stats = time.time()
            refresh_sapi()


def _safe(fn):
    try:
        fn()
        with LOCK:
            bump()
    except Exception as e:
        log("refresh failed: %s" % e)


# ================================ أوامر ================================
def cmd_check():
    print("--- geekm_knob %s ---" % VERSION)
    print("Site        :", URL)
    print("API key     :", ("SET (%d chars)" % len(KEY)) if KEY else "NOT SET  (GEEKM_KEY)")
    print("Knob token  :", ("SET" if TOKEN else "NOT SET (KNOB_TOKEN) - مفتوح لكل من على الشبكة!"))
    try:
        its = fetch_items()
        print("Website     : OK - %d products (%d need printing)" % (len(its), sum(1 for i in its if i["need"] > 0)))
        print("Plugin v1.3 :", "OK" if its and "sku" in its[0] else "قديم! حدّث إضافة Geekm Figurines إلى 1.3.0")
        try:
            woo("GET", "/lookup?code=__test__")
            print("lookup API  : OK")
        except WooError as e:
            print("lookup API  : FAILED -", e)
    except WooError as e:
        print("Website     : FAILED -", e)
    try:
        from PIL import features
        import PIL
        print("Pillow      : OK %s | raqm(Arabic): %s" % (PIL.__version__, features.check("raqm")))
    except Exception:
        print("Pillow      : MISSING -> sudo apt install python3-pil libraqm0")
    print("Font        :", Draw._find_font() or "MISSING (sudo apt install fonts-dejavu-core)")
    try:
        import evdev
        print("evdev       : OK")
    except ImportError:
        print("evdev       : MISSING -> sudo apt install python3-evdev")
    print("aplay       :", "OK" if subprocess.call(["which", "aplay"], stdout=subprocess.DEVNULL) == 0 else "missing (اختياري: alsa-utils)")


def cmd_list():
    try:
        from evdev import InputDevice, list_devices
    except ImportError:
        return print("sudo apt install python3-evdev")
    for p in list_devices():
        print(p, "|", InputDevice(p).name)


def cmd_demo(outdir):
    global DRAW
    os.makedirs(outdir, exist_ok=True)
    DRAW = Draw()
    items = [{"id": 1, "title": "مجسم ستيتش - ليلو وستيتش الكبير مع قاعدة اضاءة ملونة خاص", "image": "", "stock": 1, "pending": 7, "in_print": 0, "need": 4,
              "sku": "", "barcode": "", "status": "print"},
             {"id": 2, "title": "كوب القهوة المتكئ", "image": "", "stock": 4, "pending": 2, "in_print": 0, "need": 0,
              "sku": "GF-002", "barcode": "", "status": "ok"},
             {"id": 3, "title": "Baby Groot", "image": "", "stock": 24, "pending": 3, "in_print": 0, "need": 0, "sku": "", "barcode": "", "status": "ok"}]
    ITEMS["list"], ITEMS["t"] = items, time.time()
    HIST[:] = [{"id": 1, "title": "مجسم ستيتش - ليلو وستيتش", "image": "", "delta": 3, "t": time.time() - 400},
               {"id": 3, "title": "Baby Groot", "image": "", "delta": -2, "t": time.time() - 4000}]
    STATS.update({"day": _today(), "add": 12, "rem": 3, "saves": 5})
    SAPI["data"] = {"new": 4, "hold": 1, "today_orders": 6, "today_total": 185000, "currency": "د.ع",
                    "orders": [
                        {"id": 4812, "name": "علي حسين", "city": "بغداد", "ts": time.time() - 900, "status": "processing", "total": 45000,
                         "items": [{"n": "مجسم ستيتش - ليلو وستيتش", "q": 2}, {"n": "Baby Groot", "q": 1}], "more": 0},
                        {"id": 4811, "name": "مصطفى عبد الرحمن الكناني", "city": "البصرة", "ts": time.time() - 9000, "status": "on-hold", "total": 128500,
                         "items": [{"n": "كوب القهوة المتكئ", "q": 1}, {"n": "Baby Groot", "q": 3}, {"n": "مجسم ستيتش - ليلو وستيتش", "q": 1}, {"n": "منتج رابع", "q": 1}], "more": 1}],
                    "top": [{"id": 1, "title": "مجسم ستيتش - ليلو وستيتش", "qty": 9, "image": ""},
                            {"id": 2, "title": "كوب القهوة المتكئ", "qty": 6, "image": ""},
                            {"id": 3, "title": "Baby Groot", "qty": 4, "image": ""}]}

    def save(name):
        open(os.path.join(outdir, name + ".jpg"), "wb").write(DRAW.render())
    for k in range(len(MENU)):
        st.mode, st.menu = "home", k
        save("1_home_%d" % k)
    st.asleep = True
    save("2_sleep")
    st.asleep = False
    st.mode = "clock"
    save("3_clock")
    st.mode, st.count_n = "count_wait", 2
    save("4_count_wait")
    st.mode = "orders"
    for oi in range(3):
        st.ord_idx = oi
        save("4b_orders_%d" % oi)
    st.ord_idx = 0
    st.mode = "top"
    save("4c_top")
    st.mode, st.hist_idx, st.undo_armed = "history", 0, 0.0
    save("5_history")
    st.undo_armed = time.time()
    save("5b_history_armed")
    for j, it in enumerate(items):
        open_product(it, 3, "scan")
        save("6_product_%d" % j)
    open_product(items[1], 0, "count")
    save("6_product_count")
    st.list, st.list_kind, st.list_idx, st.mode = build_list("need"), "need", 0, "list"
    save("7_list_need")
    st.list_idx = len(st.list) - 1
    save("8_list_addall")
    st.mode, st.confirm = "confirm", {"total": 7, "count": 2}
    save("9_confirm")
    show_toast("ok", "+3", "المخزن الآن 6  |  ناقص 1")
    save("10_toast_ok")
    show_toast("warn", "-2", "تنبيه: المخزن واطي (1)")
    save("11_toast_warn")
    st.mode = "busy"
    st.busy_text = "جارٍ التحميل"
    save("12_busy")
    print("saved to", outdir)


def main():
    global DRAW
    if "--check" in sys.argv:
        return cmd_check()
    if "--list" in sys.argv:
        return cmd_list()
    if "--demo" in sys.argv:
        i = sys.argv.index("--demo")
        return cmd_demo(sys.argv[i + 1] if i + 1 < len(sys.argv) else os.path.join(HOME, "knob_demo"))
    if not KEY:
        sys.exit("ضع مفتاح الـ API: export GEEKM_KEY=...   (من ووكومرس > Geekm Figurines)")
    if not TOKEN:
        log("تنبيه: KNOB_TOKEN فارغ - أي جهاز على شبكتك يقدر يتحكم بالشاشة")
    DRAW = Draw()
    outbox_load()
    stats_load()
    log("geekm_knob v%s | site %s | port %d | outbox %d" % (VERSION, URL, PORT, len(OUTBOX)))
    try:
        fetch_items()
        log("products: %d" % len(ITEMS["list"]))
    except WooError as e:
        log("WARN: الموقع ما رد الحين (%s) - أكمل وأحاول لاحقاً" % e)
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=ticker, daemon=True).start()
    threading.Thread(target=outbox_worker, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    log("جاهز. المعاينة: http://<ip>:%d/preview?t=%s" % (PORT, TOKEN))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
