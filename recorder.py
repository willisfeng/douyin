#!/usr/bin/env python3
"""Douyin live recorder - HTTP detection + ffmpeg recording + Playwright danmaku overlay."""
import os, sys, json, threading, time, subprocess, re, urllib.request, urllib.error, base64, signal, random, concurrent.futures

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
from datetime import datetime

WATCHDOG_TIMEOUT = 180
WATCHDOG_ITER_SEC = 120
_iter_watchdog = None
URLLIB_TIMEOUT = 30
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")

ROOMS_FILE = os.environ.get("ROOMS_FILE", "rooms.txt")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))
MAX_DURATION = int(os.environ.get("MAX_DURATION", str(5 * 3600)))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp/recordings")
GH_REPO = os.environ.get("GH_REPO", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_RUN_ID = os.environ.get("GH_RUN_ID", "0")
GH_RUN_NUMBER = os.environ.get("GH_RUN_NUMBER", "0")
_renew_triggered = False
_upload_queue = []
_upload_thread = None
_upload_lock = threading.Lock()


def log(msg):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        sys.stdout.buffer.write(line.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        import os as _os
        _os.write(1, b"[LOGGING_FAILED]\n")


_ua_pool = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]
_ua_idx = 0

def _next_ua():
    global _ua_idx
    ua = _ua_pool[_ua_idx % len(_ua_pool)]
    _ua_idx += 1
    return ua


# ─── Playwright data collector ───────────────────────────────────────────

_PW_AVAILABLE = None
def _pw_check():
    global _PW_AVAILABLE
    if _PW_AVAILABLE is None:
        try:
            from playwright.sync_api import sync_playwright
            _PW_AVAILABLE = True
        except ImportError:
            _PW_AVAILABLE = False
    return _PW_AVAILABLE


class DanmakuCollector:
    """Thread-based Playwright collector for viewer counts + danmaku.
    Collects every 1s, stores absolute wall timestamps for segment matching."""

    def __init__(self, room_id, anchor_name, output_dir):
        self.room_id = room_id
        self.anchor_name = anchor_name
        self.output_dir = output_dir
        self.data = {"viewer_counts": [], "danmaku": [], "pw_start": 0}
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not _pw_check():
            log(f"[PW] {self.anchor_name} playwright not installed, skipping danmaku")
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._stop.is_set():
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)

    def save_data(self):
        """Write collected data to disk, returns (viewer_counts, danmaku, peak_vc)."""
        pw_start = self.data.get("pw_start", 0)
        vc_list = self.data.get("viewer_counts", [])
        peak_vc = max((v["count"] for v in vc_list), default=0)
        path = os.path.join(self.output_dir, f"page_data_{self.room_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        log(f"[PW] {self.anchor_name} saved: {len(self.data['danmaku'])} danmaku, "
            f"{len(vc_list)} viewer points, peak={peak_vc}")
        return self.data["viewer_counts"], self.data["danmaku"], peak_vc

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return

        self.data["pw_start"] = time.time()

        # Probe: check if GitHub runner can access douyin at all
        try:
            import urllib.request
            _req = urllib.request.Request(f"https://live.douyin.com/{self.room_id}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"})
            _resp = urllib.request.urlopen(_req, timeout=10)
            _html = _resp.read().decode('utf-8', errors='ignore')[:200]
            _live_main = "live-main" in _html
            log(f"[PW] {self.anchor_name} HTTP probe: {len(_html)}b, live-main={_live_main}, first={_html[:80].strip()}")
        except Exception as _e:
            log(f"[PW] {self.anchor_name} HTTP probe FAILED: {_e}")

        try:
            pw_context = sync_playwright()
            p = pw_context.__enter__()
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            )
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            page.goto(f"https://live.douyin.com/{self.room_id}", wait_until="domcontentloaded", timeout=15000)
            # Debug: log page info
            try:
                _pt = page.title()
                _pu = page.url
                log(f"[PW] {self.anchor_name} title=[{_pt}] url=[{_pu[:70]}]")
            except:
                pass
            # Quick check for SSR content
            try:
                page.wait_for_selector("[class*=live-main]", timeout=3000)
            except:
                log(f"[PW] {self.anchor_name} SSR not found (page may be blocked)")
            # Brief wait for hydration
            time.sleep(3.0)
            _seen_texts = set()
            _last_move = time.time()
            _last_scroll = time.time()
            _last_refresh = time.time()
            log(f"[PW] {self.anchor_name} collector started")
            _collect_iter = 0

            while not self._stop.is_set():
                now = time.time()
                _collect_iter += 1
                offset = round(now - self.data["pw_start"], 1)
                wall_ts = round(now, 1)

                # Periodic progress log every 30 iterations
                if _collect_iter % 30 == 0:
                    log(f"[PW] {self.anchor_name} it{_collect_iter}: {len(self.data['viewer_counts'])} vc, {len(self.data['danmaku'])} dm")

                try:
                    # Human-like mouse movement every 15-40s
                    if now - _last_move > random.uniform(15, 40):
                        x = random.randint(200, 1000)
                        y = random.randint(100, 600)
                        page.mouse.move(x, y, steps=random.randint(3, 8))
                        _last_move = now

                    # Scroll chat to bottom every 20-50s
                    if now - _last_scroll > random.uniform(20, 50):
                        page.evaluate('() => {const el=document.querySelector("[class*=webcast-chatroom]");if(el)el.scrollTop=el.scrollHeight;}')
                        time.sleep(random.uniform(0.5, 1.5))
                        _last_scroll = now

                    # Refresh page every 25-35 min
                    if now - _last_refresh > random.uniform(1500, 2100):
                        try:
                            page.goto(f"https://live.douyin.com/{self.room_id}", wait_until="domcontentloaded", timeout=30000)
                            time.sleep(random.uniform(2, 4))
                        except:
                            pass
                        _last_refresh = now
                        _last_move = now
                        _last_scroll = now

                    # 1. Viewer count - use evaluate to robustly find it
                    vc = None
                    try:
                        vc = page.evaluate('''() => {
                            var el = document.querySelector("[data-e2e=live-room-audience]");
                            if(el) return el.textContent.trim();
                            var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                            var texts = [];
                            while(walker.nextNode()){
                                var t = walker.currentNode.textContent.trim();
                                if(t.length > 20 || t.length < 1) continue;
                                var p = walker.currentNode.parentElement;
                                if(p && p.offsetHeight === 0) continue;
                                texts.push(t);
                            }
                            for(var t of texts){
                                t = t.replace(/,/g,'');
                                if(/^\\d{1,5}$/.test(t) && parseInt(t) > 0) return t;
                                if(/^\\d+\\.\\d万$/.test(t) || /^\\d+万$/.test(t)) return t;
                            }
                            return null;
                        }''')
                        if vc and isinstance(vc, str):
                            vc = _parse_viewer_count(vc)
                    except Exception:
                        pass
                    if vc is None and _collect_iter % 120 == 0:
                        log(f"[PW] {self.anchor_name} no viewer count at it{_collect_iter}")

                    if vc is not None:
                        if not self.data["viewer_counts"] or \
                           self.data["viewer_counts"][-1]["count"] != vc:
                            self.data["viewer_counts"].append({
                                "count": vc, "offset": offset, "wall_ts": wall_ts
                            })
                            _seen_texts.clear()

                    # 2. Danmaku - use evaluate to robustly find chat messages
                    dm_before = len(self.data["danmaku"])
                    try:
                        texts = page.evaluate('''() => {
                            var el = document.querySelector("[class*=chatroom]");
                            if(!el) return [];
                            var divs = el.querySelectorAll(":scope > div");
                            var result = [];
                            for(var d of divs){
                                var t = d.textContent.trim();
                                if(t && t.indexOf("：") >= 0) result.push(t);
                            }
                            if(result.length) return result;
                            var all = document.querySelectorAll("*");
                            var best = [];
                            for(var e of all){
                                var t = e.textContent.trim();
                                if(t.indexOf("：") < 0 || t.length > 100) continue;
                                if(e.offsetHeight === 0) continue;
                                var parts = t.split("：");
                                if(parts[0].length > 20 || parts[1] && parts[1].length > 60) continue;
                                best.push(t);
                            }
                            return best.slice(0, 50);
                        }''')
                        for text in (texts or []):
                            if not text or text in _seen_texts or '：' not in text:
                                continue
                            parts = text.split('：', 1)
                            msg = parts[1].strip() if len(parts) > 1 else ''
                            if msg in {"来了", "为主播点赞了", ""} or len(msg) <= 1:
                                continue
                            self.data["danmaku"].append({
                                "text": text[:80],
                                "offset": offset,
                                "wall_ts": wall_ts
                            })
                            _seen_texts.add(text)
                    except Exception:
                        pass

                    # Log chatroom state every 30 iters
                    if _collect_iter % 30 == 0:
                        dm_new = len(self.data["danmaku"]) - dm_before
                        log(f"[PW] {self.anchor_name} it{_collect_iter}: +{dm_new} dm")

                except Exception as e:
                    try:
                        log(f"[PW] {self.anchor_name} collect error: {e}")
                    except:
                        pass

                time.sleep(random.uniform(0.8, 1.3))

            browser.close()
            pw_context.__exit__(None, None, None)

        except Exception as e:
            log(f"[PW] {self.anchor_name} error: {e}")


# ─── ASS generation ──────────────────────────────────────────────────────

def _parse_viewer_count(text):
    """Parse Douyin viewer count: '1.2万' -> 12000, '9999' -> 9999."""
    if not text:
        return None
    text = text.strip()
    # Check for Chinese '万' (wan, 10k)
    WAN = '万'
    if WAN in text:
        num_part = text.replace(WAN, '').replace(',', '').strip()
        try:
            if '.' in num_part:
                val = float(num_part)
                return int(val * 10000)
            else:
                return int(num_part) * 10000
        except (ValueError, TypeError):
            return None
    else:
        c = re.sub(r'\D', '', text)
        if c and c.isdigit():
            return int(c)
    return None


def _fmt_ts(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"

def _build_ass(seg_vc, seg_dm, seg_duration):
    """Build ASS content. Simple ticker: 5s per danmaku, 10 rows, push-up."""

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "Collisions: Normal",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Danmaku,Microsoft YaHei,24,"+chr(38)+"H00FFFFFF,"+chr(38)+"H00FFFFFF,"+chr(38)+"H00000000,"+chr(38)+"H00FFFFFF,0,0,0,0,100,100,0,0,1,1,4,1,20,20,150,1",
        "Style: ViewerCount,Microsoft YaHei,36,"+chr(38)+"H00FFFFFF,"+chr(38)+"H00FFFFFF,"+chr(38)+"H80000000,"+chr(38)+"H00000000,1,0,0,0,100,100,0,0,1,2,0,8,50,50,50,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Viewer count at top center, each shown for 5s
    for i, vp in enumerate(seg_vc):
        t = vp.get("_offset", vp.get("offset", 0))
        s = max(0, t - 1.0)
        e = min(s + 5.0, seg_duration)
        if e > s:
            lines.append(
                "Dialogue: 0," + _fmt_ts(s) + "," + _fmt_ts(e) + ",ViewerCount,,0,0,0,,"
                "{\\an8\\move(960,30,960,30,0,0)\\1c" + chr(38) + "HFFFFFF" + chr(38) + "\\3c" + chr(38) + "H0000FF" + chr(38) + "\\bord8\\shad0}" + str(vp["count"]) + " \u4eba\u5728\u770b"
            )

    if not seg_dm:
        return '\n'.join(lines)

    # Danmaku: simple row cycle, 5s each
    DUR = 5.0
    ROWS = 10
    ROW_H = 34
    BOTTOM_Y = 1050

    for idx, dp in enumerate(seg_dm):
        t = dp.get("_offset", dp.get("offset", 0))
        if t < 0 or t >= seg_duration:
            continue
        txt = (dp.get("text", "") or "")[:60].replace("{", "").replace("}", "")
        if not txt.strip():
            continue
        row = idx % ROWS
        y = BOTTOM_Y - row * ROW_H
        s = max(0, t - 0.1)
        e = min(s + DUR, seg_duration)
        lines.append(
            "Dialogue: 1," + _fmt_ts(s) + "," + _fmt_ts(e) + ",Danmaku,,0,0,0,,"
            "{\\an1\\move(100," + str(y) + ",100," + str(y) + ",0,0)\\fade(255,0,255,0,0,0," + str(int(DUR*1000)) + ")}" + txt
        )

    return '\n'.join(lines)

def _process_segments(output_dir, room_id, anchor_name, seg_files, rec_start, seg_duration):
    """Generate ASS per segment and remux MP4 → MKV. Returns list of (mkv_path, mkv_fname)."""
    data_path = os.path.join(output_dir, f"page_data_{room_id}.json")
    if not os.path.exists(data_path):
        log(f"[ASS] {anchor_name} no page_data, skipping")
        return []

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    vc = data.get("viewer_counts", [])
    dm = data.get("danmaku", [])

    if not vc and not dm:
        log(f"[ASS] {anchor_name} empty data, skipping")
        return []

    mkv_results = []
    # 5-second tolerance at each boundary
    TOLERANCE = 5.0

    # Only process MP4 files (not WAV/other) so seq_idx matches segment position
    mp4_files = [(p, f) for p, f in seg_files if f.endswith('.mp4')]
    if not mp4_files:
        log(f"[ASS] {anchor_name} no MP4 segments found")
        return []

    for seq_idx, (seg_path, seg_fname) in enumerate(mp4_files):
        seg_begin = rec_start + seq_idx * seg_duration
        seg_end = rec_start + (seq_idx + 1) * seg_duration

        # Filter data: wall_ts within [seg_begin - TOL, seg_end + TOL)
        seg_vc = [
            {**p, "_offset": max(0, p["wall_ts"] - seg_begin)}
            for p in vc if (seg_begin - TOLERANCE) <= p.get("wall_ts", seg_begin) < (seg_end + TOLERANCE)
        ]
        seg_dm = [
            {**d, "_offset": max(0, d["wall_ts"] - seg_begin)}
            for d in dm if (seg_begin - TOLERANCE) <= d.get("wall_ts", seg_begin) < (seg_end + TOLERANCE)
        ]

        # Sort by new offset
        seg_vc.sort(key=lambda x: x["_offset"])
        seg_dm.sort(key=lambda x: x["_offset"])

        ass_content = _build_ass(seg_vc, seg_dm, seg_duration)
        ass_path = os.path.join(output_dir, f"{room_id}_{seq_idx:03d}.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        mkv_path = seg_path.replace(".mp4", ".mkv")
        cmd = [FFMPEG, "-y", "-hide_banner",
               "-i", seg_path,
               "-f", "ass", "-i", ass_path,
               "-c:v", "copy", "-c:a", "copy", "-c:s", "ass",
               "-map", "0:v", "-map", "0:a", "-map", "1",
               mkv_path]
        result = subprocess.run(cmd, capture_output=True, timeout=120)

        if os.path.exists(mkv_path):
            mkv_size = os.path.getsize(mkv_path)
            mkv_name = os.path.basename(mkv_path)
            mkv_results.append((mkv_path, mkv_name))
            log(f"[ASS] {anchor_name} seg{seq_idx} → MKV ({len(seg_dm)} dm, {mkv_size/1024/1024:.1f}MB)")
        else:
            log(f"[ASS] {anchor_name} seg{seq_idx} remux FAILED")

    return mkv_results


# ─── Core recording functions ────────────────────────────────────────────

def http_check_live(room_id):
    """HTTP check using shell curl."""
    ua = _next_ua()
    cookie_val = ""
    try:
        cmd = ['curl', '-s', '-L', '--max-time', str(URLLIB_TIMEOUT)]
        if cookie_val:
            cmd.extend(['-H', 'Cookie: ' + cookie_val])
        cmd.extend(['-H', 'User-Agent: ' + ua])
        cmd.extend(['-H', 'Referer: https://www.douyin.com/'])
        cmd.extend(['-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'])
        cmd.extend(['-H', 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8'])
        cmd.append('https://live.douyin.com/' + str(room_id))
        result = subprocess.run(cmd, capture_output=True, timeout=URLLIB_TIMEOUT + 5)
        html = result.stdout.decode('utf-8', errors='replace')
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='replace')[:100]
            return (False, 'curl_err:' + err, None, None)
    except Exception as e:
        return (False, 'curl_exc:' + str(type(e).__name__), None, None)

    if "flv_pull_url" not in html:
        log(f"[DBG] {room_id} no flv len={len(html)}")
        m = re.search(r'data-cluster="([^"]+)"', html)
        cluster = m.group(1) if m else 'none'
        log(f"[DBG] {room_id} cluster={cluster}")
        if len(html) < 100:
            import random as _rnd
            delay = _rnd.uniform(3, 5)
            log(f"[RETRY] {room_id} len={len(html)}, retry in {delay:.0f}s")
            time.sleep(delay)
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=URLLIB_TIMEOUT + 5)
                html2 = result.stdout.decode('utf-8', errors='replace')
                if "flv_pull_url" in html2:
                    log(f"[RETRY] {room_id} success on retry")
                    html = html2
            except:
                pass
        if "flv_pull_url" not in html:
            return (False, 'no_flv', None, None)

    found = []
    priority = {"FULL_HD1": 4, "HD1": 3, "SD1": 2, "SD2": 1}
    for m in re.finditer(r'["\\]+(FULL_HD1|HD1|SD1|SD2)["\\]+\s*[:=]\s*["\\]+(https?://[^"]+)', html):
        curl = m.group(2).replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=")
        if curl.startswith("http"):
            found.append((m.group(1), curl))

    if found:
        best = max(found, key=lambda x: priority.get(x[0], 0))
        flv_url = best[1]
        return (True, "ok", flv_url, best[0])

    return (True, "live_but_no_flv", None, None)


def http_get_anchor_name(room_id):
    """Get anchor name from SSR HTML via curl."""
    ua = _next_ua()
    try:
        cmd = ['curl', '-s', '-L', '--max-time', str(URLLIB_TIMEOUT),
               '-H', 'User-Agent: ' + ua,
               '-H', 'Referer: https://www.douyin.com/',
               '-H', 'Accept: text/html,application/xhtml+xml',
               'https://live.douyin.com/' + str(room_id)]
        result = subprocess.run(cmd, capture_output=True, timeout=URLLIB_TIMEOUT + 5)
        html = result.stdout.decode('utf-8', errors='replace')
    except:
        return None

    for m in re.finditer(r'[\\]?"nickname[\\]?"\s*[:=]\s*[\\]?"([^"]+)', html):
        name = m.group(1)
        name = name.rstrip('\\')
        if name and name != '$undefined' and len(name) >= 2 and 'undefined' not in name:
            return name
    return None


def load_rooms():
    rooms = []
    if os.path.exists(ROOMS_FILE):
        with open(ROOMS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('=', 1)
                if len(parts) < 2:
                    parts = line.split(',', 1)
                rid = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else rid
                rooms.append({"id": rid, "name": name})
    log(f"Loaded {len(rooms)} rooms from {ROOMS_FILE}")
    return rooms


def load_rooms_from_github():
    try:
        if not GH_REPO or not GH_TOKEN:
            return []
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GH_REPO}/contents/{ROOMS_FILE}",
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"})
        resp = urllib.request.urlopen(req, timeout=URLLIB_TIMEOUT)
        data = json.loads(resp.read().decode())
        content = base64.b64decode(data["content"]).decode("utf-8")
        rooms = []
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("=", 1)
            if len(parts) < 2:
                parts = line.split(",", 1)
            rid = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else rid
            rooms.append({"id": rid, "name": name})
        return rooms
    except Exception as e:
        log(f"load_rooms_from_github error: {e}")
        return []


def update_rooms_nickname(anchor_names):
    if not GH_TOKEN or not GH_REPO:
        return
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GH_REPO}/contents/{ROOMS_FILE}",
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"})
        data = json.loads(urllib.request.urlopen(req, timeout=URLLIB_TIMEOUT).read())
        sha = data["sha"]
        content = base64.b64decode(data["content"]).decode("utf-8")
        lines = content.split("\n")
        new_lines = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                new_lines.append(line)
                continue
            parts = line.split("=", 1)
            if len(parts) < 2:
                parts = line.split(",", 1)
            rid = parts[0].strip()
            if rid in anchor_names:
                new_lines.append(f"{rid} = {anchor_names[rid]}")
            else:
                new_lines.append(line)
        new_content = "\n".join(new_lines)
        if new_content == content:
            return
        put = urllib.request.Request(
            f"https://api.github.com/repos/{GH_REPO}/contents/{ROOMS_FILE}",
            data=json.dumps({"message": "update nicknames",
                             "content": base64.b64encode(new_content.encode()).decode(),
                             "sha": sha}).encode(),
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"},
            method="PUT")
        urllib.request.urlopen(put, timeout=URLLIB_TIMEOUT)
        log("rooms.txt nicknames updated via GitHub API")
    except Exception as e:
        log(f"update nicknames error: {e}")


def start_recording(url, quality, room_id, anchor_name=""):
    """Start ffmpeg segmented recording + Playwright data collector."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{room_id}_{ts}"
    seg_duration = int(os.environ.get("SEGMENT_DURATION", "900"))
    outfile_pattern = os.path.join(OUTPUT_DIR, f"{base}_%03d.mp4")
    audiofile = os.path.join(OUTPUT_DIR, f"{base}.wav")

    with open(os.path.join(OUTPUT_DIR, f"{room_id}_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"room_id": room_id, "anchor_name": anchor_name,
                   "filename_base": base, "audio": f"{base}.wav",
                   "quality": quality, "seg_duration": seg_duration}, f)

    log(f"Start recording: {anchor_name}/{base}_%03d.mp4 [{quality}] seg={seg_duration}s + audio")

    ff_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    cookie_val = ""
    cookie_hdr = "Cookie: " + cookie_val + "\r\n" if cookie_val else ""
    ff_headers = [
        "-headers", "User-Agent: " + ff_ua + "\r\n"
        "Referer: https://live.douyin.com/\r\n"
        "Origin: https://live.douyin.com\r\n"
        "Accept: */*\r\n"
        "Host: " + url.split("/")[2] + "\r\n"
        "Connection: keep-alive\r\n"
        + cookie_hdr,
    ]
    ffmpeg_log = os.path.join(OUTPUT_DIR, f"ffmpeg_{room_id}_{ts}.log")
    proc = subprocess.Popen([FFMPEG, "-y", "-loglevel", "warning"] + ff_headers + ["-i", url, "-c", "copy",
                             "-f", "segment", "-segment_time", str(seg_duration),
                             "-reset_timestamps", "1", outfile_pattern],
                            stdout=subprocess.DEVNULL, stderr=open(ffmpeg_log, "w"))

    time.sleep(2)
    audio_log = os.path.join(OUTPUT_DIR, f"audio_{room_id}_{ts}.log")
    audio_proc = subprocess.Popen([FFMPEG, "-y", "-loglevel", "warning"] + ff_headers + ["-i", url, "-vn",
                                    "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audiofile],
                                   stdout=subprocess.DEVNULL, stderr=open(audio_log, "w"))

    # Start Playwright danmaku collector
    collector = DanmakuCollector(room_id, anchor_name, OUTPUT_DIR)
    collector.start()

    return proc, outfile_pattern, audio_proc, audiofile, collector, seg_duration


def stop_proc(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except:
            proc.kill()


def handle_room_end(rid, recordings, anchor_names, now):
    if rid not in recordings:
        return
    rec = recordings[rid]
    stop_proc(rec.get("proc"))
    stop_proc(rec.get("audio_proc"))

    # Stop Playwright collector and save data, get peak viewer count
    peak_vc = 0
    collector = rec.get("collector")
    if collector:
        collector.stop()
        _, _, peak_vc = collector.save_data()

    # Update meta.json with peak viewer count
    meta_path = os.path.join(OUTPUT_DIR, f"{rid}_meta.json")
    try:
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["peak_viewers"] = peak_vc
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

    outfile_pattern = rec.get("outfile", "")
    audiofile = rec.get("audiofile", "")
    seg_duration = rec.get("seg_duration", 900)
    from datetime import datetime as _dt
    start_ts_fmt = _dt.fromtimestamp(rec.get("start", 0)).strftime("%Y%m%d_%H%M%S")
    end_ts_fmt = _dt.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
    aname = anchor_names.get(rid, rid)
    dirname = OUTPUT_DIR

    base_prefix = outfile_pattern.replace("_%03d.mp4", "")
    seg_files = sorted([f for f in os.listdir(dirname)
                        if f.startswith(os.path.basename(base_prefix))
                        and f.endswith(".mp4")
                        and os.path.isfile(os.path.join(dirname, f))])

    upload_files = []
    for seg_fname in seg_files:
        seg_path = os.path.join(dirname, seg_fname)
        seg_end = _dt.fromtimestamp(os.path.getmtime(seg_path)).strftime("%Y%m%d_%H%M%S")
        upload_files.append((seg_path, seg_fname))

    # Handle audio
    new_wav = None
    if audiofile and os.path.exists(audiofile):
        wav_base = os.path.splitext(os.path.basename(audiofile))[0]
        new_wav_name = f"{wav_base}_{end_ts_fmt}.wav"
        new_wav = os.path.join(dirname, new_wav_name)
        try:
            os.rename(audiofile, new_wav)
        except:
            new_wav = audiofile
        upload_files.insert(0, (new_wav, os.path.basename(new_wav)))

    # Generate ASS per segment and remux to MKV
    rec_start = rec.get("start", 0)
    if rec_start > 0:
        mkv_files = _process_segments(dirname, rid, aname, upload_files, rec_start, seg_duration)
        # Add MKV files to upload queue (after WAV, before MP4)
        for mkv_path, mkv_name in mkv_files:
            upload_files.append((mkv_path, mkv_name))

    if upload_files:
        log(f"[{aname}] Recording ended, enqueuing {len(upload_files)} file(s) for upload")

    _enqueue_upload_segments(upload_files, start_ts_fmt)
    del recordings[rid]


def check_renew(elapsed):
    global _renew_triggered
    if elapsed > 270*60 and not _renew_triggered and GH_REPO and GH_TOKEN:
        try:
            check_req = urllib.request.Request(
                f"https://api.github.com/repos/{GH_REPO}/actions/workflows/275535928/runs?per_page=5&status=in_progress",
                headers={"Authorization": f"Bearer {GH_TOKEN}"})
            existing = json.loads(urllib.request.urlopen(check_req, timeout=15).read())
            existing_numbers = [r["run_number"] for r in existing.get("workflow_runs", [])]
            other_runs = [n for n in existing_numbers if str(n) != str(GH_RUN_NUMBER)]
            if len(other_runs) > 0:
                log(f"Renew skipped: {len(other_runs)} other in-progress run(s): {other_runs}")
            else:
                trigger = urllib.request.Request(
                    f"https://api.github.com/repos/{GH_REPO}/actions/workflows/continuous.yml/dispatches",
                    data=json.dumps({"ref": "main"}).encode(),
                    headers={"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"},
                    method="POST")
                urllib.request.urlopen(trigger, timeout=30)
                log(f"Renew triggered (elapsed {elapsed/60:.0f}min)")
        except Exception as e:
            log(f"Renew error: {e}")
        _renew_triggered = True


def run():
    rooms = load_rooms_from_github()
    if not rooms:
        rooms = load_rooms()
    if not rooms:
        log("ERROR: no rooms loaded"); sys.exit(1)
    log(f"Loaded {len(rooms)} rooms:")
    for r in rooms:
        log(f"  {r['id']} = {r['name']}")
    log(f"Room check: ~10-20s per room (serial) | Max duration: {MAX_DURATION//3600}h")
    if GH_REPO and GH_TOKEN:
        log("Self-renewal + upload: enabled")
    if _pw_check():
        log("Playwright available: danmaku overlay ENABLED")
    else:
        log("Playwright NOT available: danmaku overlay DISABLED (pip install playwright)")

    prev_live = {}
    recordings = {}
    anchor_names = {}
    room_names = {r['id']: r['name'] for r in rooms}
    current_room_idx = 0

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(rooms)) as ex:
        def init_check(r):
            live, reason, url, quality = http_check_live(r['id'])
            aname = http_get_anchor_name(r['id'])
            return (r, live, reason, url, quality, aname)
        init_results = list(ex.map(init_check, rooms))
    for r, live, reason, url, quality, aname in init_results:
        safe_name = r['name'][:20]
        log(f"  [{safe_name}] 直播状态={'在线' if live else '离线'} ({reason})")
        if live and url:
            log(f"  -> 直播流: {quality} {url[:80]}...")
        prev_live[r['id']] = live
        if aname:
            anchor_names[r['id']] = aname
            room_names[r['id']] = aname
            log(f"  nickname: {aname}")
            update_rooms_nickname(anchor_names)
    log(f'[init] initial check of {len(rooms)} rooms done in {time.time()-t0:.1f}s')

    # Start recordings for any live rooms
    init_live_map = {}
    for r, live, reason, url, quality, aname in init_results:
        init_live_map[r['id']] = (live, url, quality, aname)
    for r in rooms:
        rid = r['id']
        live, url, quality, aname = init_live_map.get(rid, (False, None, None, r['name']))
        if live and url:
            proc, outfile, audio_proc, audiofile, collector, seg_duration = start_recording(url, quality, rid, aname)
            recordings[rid] = {"proc": proc, "outfile": outfile, "audio_proc": audio_proc,
                               "audiofile": audiofile, "start": time.time(),
                               "collector": collector, "seg_duration": seg_duration}
            log(f"Started recording {aname}")

    log('[init] entering main loop')
    start_time = time.time()
    last_refresh = start_time

    while True:
        try:
            loop_start = time.time()
            now = time.time()
            elapsed = now - start_time

            if elapsed > MAX_DURATION:
                log(f"Time limit ({elapsed/3600:.1f}h), exiting")
                break

            # Refresh rooms from GitHub
            if now - last_refresh > 30:
                new_rooms = load_rooms_from_github()
                for nr in new_rooms:
                    if nr["id"] not in [r["id"] for r in rooms]:
                        log(f"New room: {nr['id']} = {nr['name']}")
                        live, reason, url, quality = http_check_live(nr["id"])
                        prev_live[nr["id"]] = live
                        aname = http_get_anchor_name(nr["id"]) or nr["name"]
                        anchor_names[nr["id"]] = aname
                        room_names[nr["id"]] = aname
                        log(f"  [{aname}] 直播状态={'在线' if live else '离线'} ({reason})")
                        if live and url:
                            proc, outfile, audio_proc, audiofile, collector, seg_duration = start_recording(url, quality, nr["id"], aname)
                            recordings[nr["id"]] = {"proc": proc, "outfile": outfile, "audio_proc": audio_proc,
                                                     "audiofile": audiofile, "start": time.time(),
                                                     "collector": collector, "seg_duration": seg_duration}
                        update_rooms_nickname(anchor_names)

                new_ids = {r["id"] for r in new_rooms}
                for rid in list(prev_live.keys()):
                    if rid not in new_ids:
                        log(f"Room removed: {room_names.get(rid, rid)}")
                        if rid in recordings:
                            handle_room_end(rid, recordings, anchor_names, now)
                        prev_live.pop(rid, None)
                        room_names.pop(rid, None)
                        anchor_names.pop(rid, None)
                rooms = new_rooms
                last_refresh = now

            # Check recording process exit
            for rid in list(recordings.keys()):
                rec = recordings[rid]
                proc = rec.get("proc")
                if proc and proc.poll() is not None:
                    import glob
                    log_dir = OUTPUT_DIR
                    for f in sorted(glob.glob(os.path.join(log_dir, f"ffmpeg_{rid}_*.log"))):
                        try:
                            lines = open(f, "r").read().strip().split("\n")
                            if lines:
                                log(f"[FFERR] {room_names.get(rid, rid)} last lines: {lines[-3:]}")
                        except:
                            pass
                        break
                    log(f"[REC] {room_names.get(rid, rid)} ffmpeg exited, checking if still live")
                    still_live, l_reason, l_url, l_q = http_check_live(rid)
                    if still_live and l_url:
                        log(f"[REC] {room_names.get(rid, rid)} still live -> upload + restart")
                        handle_room_end(rid, recordings, anchor_names, now)
                        log(f"[REC] {room_names.get(rid, rid)} will restart next cycle")
                    else:
                        log(f"[REC] {room_names.get(rid, rid)} went offline, ending")
                        handle_room_end(rid, recordings, anchor_names, now)

            # HTTP detection for non-recording rooms
            detect_rooms = [rid for rid in sorted(prev_live.keys()) if rid not in recordings]
            if detect_rooms:
                rid = detect_rooms[current_room_idx % len(detect_rooms)]
                current_room_idx += 1
                live, reason, url, quality = http_check_live(rid)
                safe_rid = room_names.get(rid, rid)[:20]
                log(f'[{safe_rid}] 直播状态={"在线" if live else "离线"} ({reason})')
                prev_live[rid] = live
                if live and rid not in recordings:
                    if url:
                        aname = anchor_names.get(rid, room_names.get(rid, rid))
                        proc, outfile, audio_proc, audiofile, collector, seg_duration = start_recording(url, quality, rid, aname)
                        recordings[rid] = {"proc": proc, "outfile": outfile, "audio_proc": audio_proc,
                                            "audiofile": audiofile, "start": time.time(),
                                            "collector": collector, "seg_duration": seg_duration}
                log(f'  {len(detect_rooms)} non-rec, next in 10-20s')

            # Recording rooms: skip detection, log duration
            for rid in sorted(recordings.keys()):
                safe_rid = room_names.get(rid, rid)[:20]
                rsec = int(time.time() - recordings[rid]['start'])
                rh, rm = rsec // 3600, (rsec % 3600) // 60
                # Show current viewer count from collector
                _coll = recordings[rid].get('collector')
                _vc = ''
                if _coll:
                    _vcl = _coll.data.get('viewer_counts', [])
                    if _vcl:
                        _vc = f' [{_vcl[-1]["count"]}人]'
                log(f'[REC] {safe_rid} 已录制🔴{rh}时{rm}分{_vc}, 跳过检测')

            for rid in list(recordings.keys()):
                if time.time() - recordings[rid]["start"] > MAX_DURATION:
                    handle_room_end(rid, recordings, anchor_names, time.time())

            check_renew(elapsed)

            if int(elapsed / 60) != int((elapsed - CHECK_INTERVAL) / 60):
                log(f'[heartbeat] running {int(elapsed/60)}min, rooms={len(prev_live)}')

            if int(elapsed / 60) != int((elapsed - CHECK_INTERVAL - 1) / 60):
                try:
                    status_data = {}
                    now_ts = int(time.time())
                    for rid in sorted(prev_live.keys()):
                        in_rec = rid in recordings
                        rec_start = recordings[rid]["start"] if in_rec else 0
                        status_data[rid] = {
                            "live": in_rec or prev_live.get(rid, False),
                            "recording": in_rec,
                            "start_ts": int(rec_start) if in_rec else None,
                            "name": room_names.get(rid, rid),
                            "checked_at": now_ts
                        }
                    _write_status_json(status_data)
                except Exception as _se:
                    log(f'status write error: {_se}')

            time.sleep(random.uniform(10, 20))

        except Exception as _e:
            import traceback as _tb
            if _iter_watchdog:
                _iter_watchdog.cancel()
                _iter_watchdog = None
            log(f"main loop crash: {_e}")
            log(_tb.format_exc())
            time.sleep(10)

    for rid in list(recordings.keys()):
        handle_room_end(rid, recordings, anchor_names, time.time())
    _wait_uploads()
    log("录制结束")


def _enqueue_upload_segments(upload_files, start_ts_fmt):
    global _upload_queue, _upload_thread
    if not upload_files:
        return
    with _upload_lock:
        for fpath, fname in upload_files:
            _upload_queue.append((fpath, fname))
        _start_upload_worker()


def _start_upload_worker():
    global _upload_thread
    if _upload_thread and _upload_thread.is_alive():
        return
    t = threading.Thread(target=_upload_worker, daemon=True)
    t.start()
    _upload_thread = t


def _upload_worker():
    while True:
        with _upload_lock:
            if not _upload_queue:
                break
            fpath, fname = _upload_queue.pop(0)
        ok = _upload_file(fpath, fname)
        if ok and fname.endswith('.wav') and GH_REPO and GH_TOKEN:
            try:
                dispatch = urllib.request.Request(
                    f"https://api.github.com/repos/{GH_REPO}/dispatches",
                    data=json.dumps({"event_type": "transcribe_ready"}).encode(),
                    headers={"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"},
                    method="POST")
                urllib.request.urlopen(dispatch, timeout=URLLIB_TIMEOUT)
                log("Triggered transcription dispatch")
            except Exception as e:
                log(f"Trigger dispatch error: {e}")


def _upload_file(fpath, upload_name):
    if not os.path.exists(fpath) or not GH_REPO or not GH_TOKEN:
        return False
    try:
        with open(fpath, 'rb') as f:
            content = f.read()
        fname_only = os.path.basename(upload_name) if not upload_name.startswith('/') else upload_name
        tag = fname_only
        release_url = f"https://api.github.com/repos/{GH_REPO}/releases"
        rel = json.loads(urllib.request.urlopen(
            urllib.request.Request(release_url + "?per_page=5",
                headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}),
            timeout=URLLIB_TIMEOUT).read())
        existing = [r for r in rel if r.get("tag_name") == tag]
        if existing:
            log(f"Uploading {fname_only} to existing release {tag}")
            url = existing[0]["upload_url"].replace("{?name,label}", f"?name={fname_only}")
        else:
            log(f"Creating release {tag}")
            r2 = json.loads(urllib.request.urlopen(
                urllib.request.Request(release_url,
                    data=json.dumps({"tag_name": tag, "name": tag, "prerelease": True}).encode(),
                    headers={"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"}),
                timeout=URLLIB_TIMEOUT).read())
            url = r2["upload_url"].replace("{?name,label}", f"?name={fname_only}")
        total_size = len(content)
        urllib.request.urlopen(urllib.request.Request(url,
            data=content,
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/octet-stream",
                     "Content-Length": str(total_size)}),
            timeout=600)
        log(f"Uploaded {fname_only} ({total_size/1024/1024:.1f}MB) to Release")
        return True
    except Exception as e:
        log(f"Upload error {fname_only}: {e}")
        return False


def _wait_uploads():
    global _upload_thread
    if _upload_thread and _upload_thread.is_alive():
        log("Waiting for background uploads to finish...")
        _upload_thread.join(timeout=1200)


def _write_status_json(status_data):
    if not GH_REPO or not GH_TOKEN:
        return
    path = 'docs/live_status.json'
    body = json.dumps(status_data, ensure_ascii=False, indent=2)
    b64 = base64.b64encode(body.encode('utf-8')).decode('utf-8')
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                f'https://api.github.com/repos/{GH_REPO}/contents/{path}',
                headers={'Authorization': f'Bearer {GH_TOKEN}', 'Accept': 'application/vnd.github+json'})
            try:
                resp = urllib.request.urlopen(req, timeout=15)
                sha = json.loads(resp.read().decode())['sha']
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    sha = None
                else:
                    log(f'_write_status_json get sha failed: {he.code}')
                    time.sleep(1)
                    continue
            put_data = json.dumps({
                'message': 'update live status',
                'content': b64,
                'sha': sha
            } if sha else {
                'message': 'create live status',
                'content': b64
            }, ensure_ascii=True).encode('utf-8')
            put_req = urllib.request.Request(
                f'https://api.github.com/repos/{GH_REPO}/contents/{path}',
                data=put_data,
                headers={'Authorization': f'Bearer {GH_TOKEN}', 'Content-Type': 'application/json'},
                method='PUT')
            urllib.request.urlopen(put_req, timeout=30)
            return
        except urllib.error.HTTPError as he:
            if he.code == 409:
                log(f'_write_status_json sha conflict, retry {attempt+1}')
                time.sleep(1)
                continue
            log(f'_write_status_json error (attempt {attempt+1}): {he.code}')
            return
        except Exception as e:
            log(f'_write_status_json error (attempt {attempt+1}): {e}')
            return


def fallback_upload():
    if not os.path.exists(OUTPUT_DIR) or not GH_REPO or not GH_TOKEN:
        return
    from pathlib import Path
    for fname in os.listdir(OUTPUT_DIR):
        if not fname.endswith('.wav'):
            continue
        wav_path = os.path.join(OUTPUT_DIR, fname)
        base = fname[:-4]
        txt_path = os.path.join(OUTPUT_DIR, base + '.txt')
        if os.path.exists(txt_path):
            continue
        log(f"Found untranscribed: {fname}")
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://api.github.com/repos/{GH_REPO}/dispatches",
                data=json.dumps({"event_type": "transcribe_ready"}).encode(),
                headers={"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"},
                method="POST"), timeout=15)
            log("Triggered transcription check")
        except:
            pass
        break


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fallback":
        fallback_upload()
    else:
        run()
