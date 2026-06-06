"""Full test: PW open (danmaku+VC) + HTTP VC + MKV"""
import sys, json, os, time, urllib.request, re, subprocess
sys.stdout = open(sys.stdout.fileno(), 'w', encoding='utf-8', buffering=1)

room_id = os.environ.get('TEST_ROOM', '168465302284')
duration = int(os.environ.get('TEST_DURATION', '120'))
OUT = '/tmp/recordings'
os.makedirs(OUT, exist_ok=True)
rec_start = time.time()

def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}')
    sys.stdout.flush()

log(f'Room: {room_id}, duration: {duration}s')

# ===== STEP 1: Playwright open =====
log('Step 1: Playwright start...')
from playwright.sync_api import sync_playwright
pw_instance = sync_playwright()
p = pw_instance.__enter__()
browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'])
ctx = browser.new_context(
    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    viewport={'width': 1280, 'height': 720}
)
page = ctx.new_page()
page.goto(f'https://live.douyin.com/{room_id}', wait_until='domcontentloaded', timeout=15000)
time.sleep(4)

cookies = ctx.cookies()
cookie_str = '; '.join([c['name'] + '=' + c['value'] for c in cookies])
html = page.content()
rid_m = list(re.finditer(r'"room_id_str"\s*:\s*"(\d+)"', html))
internal_rid = rid_m[0].group(1) if rid_m else room_id
log(f'  Cookies: {len(cookies)}, internal_rid: {internal_rid}')

from urllib.parse import urlencode
api_params = {
    'aid': '6383', 'app_name': 'douyin_web', 'live_id': '1',
    'device_platform': 'web', 'language': 'zh-CN', 'enter_from': 'link_share',
    'cookie_enabled': 'true', 'screen_width': '1280', 'screen_height': '720',
    'browser_language': 'zh-CN', 'browser_platform': 'Win32',
    'browser_name': 'Chrome', 'browser_version': '120.0.0.0',
    'os_name': 'Windows', 'os_version': '10',
    'web_rid': room_id, 'room_id_str': internal_rid,
    'is_need_double_stream': 'false',
}
api_url = 'https://live.douyin.com/webcast/room/web/enter/?' + urlencode(api_params)

# ===== STEP 2: Get FLV URL =====
log('Step 2: Get FLV URL...')
api_headers = {
    'Accept': 'application/json, text/plain, */*',
    'Referer': f'https://live.douyin.com/{room_id}',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Cookie': cookie_str,
}
req = urllib.request.Request(api_url, headers=api_headers)
data = json.loads(urllib.request.urlopen(req, timeout=8).read())
d0 = data.get('data', {}).get('data', [{}])[0]
flv_url = d0.get('stream_url', {}).get('flv_pull_url', {}).get('FULL_HD1', '') or \
          d0.get('stream_url', {}).get('flv_pull_url', {}).get('HD1', '')
log(f'  FLV: {flv_url[:60]}...')

# ===== STEP 3: ffmpeg recording =====
log('Step 3: ffmpeg recording...')
seg_prefix = f'{OUT}/{room_id}_seg_'
ffmpeg = subprocess.Popen([
    'ffmpeg', '-y', '-fflags', 'nobuffer',
    '-i', flv_url, '-c', 'copy', '-t', str(duration),
    '-f', 'segment', '-segment_time', '900', '-reset_timestamps', '1',
    f'{seg_prefix}%03d.mp4'
], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# ===== STEP 4: Collect data (PW + HTTP) =====
log('Step 4: Collecting data...')
data_records = []
seen_dm = set()
start = time.time()

# Danmaku selector from main recorder (uses webcast-chatroom class)
DM_JS = '''() => {
    var vc = document.querySelector("[data-e2e=live-room-audience]");
    var v = vc ? vc.textContent.trim() : null;
    var chat = document.querySelector("[class*=webcast-chatroom]") || document.querySelector("[class*=chatroom]");
    var dms = [];
    if (chat) {
        var divs = chat.querySelectorAll(":scope > div");
        for (var d of divs) {
            var t = d.textContent.trim();
            if (t && t.length > 1 && t.length < 200) dms.push(t);
        }
    }
    return {vc: v, dms: dms};
}'''

while time.time() - start < duration:
    now = time.time()
    offset = round(now - rec_start, 1)

    # --- HTTP VC ---
    http_vc = None
    try:
        req = urllib.request.Request(api_url, headers=api_headers)
        d = json.loads(urllib.request.urlopen(req, timeout=8).read())
        dd = d.get('data', {}).get('data', [{}])[0]
        raw = dd.get('stats', {}).get('user_count_str')
        if raw is not None:
            http_vc = int(raw)
        elif dd.get('user_count_str'):
            try:
                http_vc = int(str(dd['user_count_str']).replace(',', '').replace('+',''))
            except:
                pass
    except Exception as e:
        pass

    # --- PW VC + danmaku ---
    pw_vc = None
    pw_dms = []
    try:
        ct = page.evaluate(DM_JS)
        if ct:
            v = ct.get('vc')
            if v:
                v_str = str(v).replace(',', '').replace('+', '')
                if '万' in v_str:
                    pw_vc = int(float(v_str.replace('万', '')) * 10000)
                else:
                    try:
                        pw_vc = int(v_str)
                    except:
                        pass
            for dm_text in (ct.get('dms') or []):
                text_key = dm_text[:50]
                if text_key not in seen_dm:
                    pw_dms.append({'text': dm_text[:80], 'wall_ts': now, 'offset': offset})
                    seen_dm.add(text_key)
    except:
        pass

    data_records.append({
        'wall_ts': now, 'offset': offset,
        'pw_vc': pw_vc, 'http_vc': http_vc,
        'pw_dms': pw_dms,
    })
    time.sleep(1.0)

ffmpeg.wait(timeout=10)
log(f'  ffmpeg exit={ffmpeg.returncode}')

# Collect all danmaku
all_dms = []
for r in data_records:
    for dm in r.get('pw_dms', []):
        all_dms.append(dm)

# Analyze
http_vcs = [(r['offset'], r['http_vc']) for r in data_records if r.get('http_vc') is not None]
pw_vcs_raw = [(r['offset'], r['pw_vc']) for r in data_records if r.get('pw_vc') is not None]

log(f'HTTP VC: {len(http_vcs)}, PW VC: {len(pw_vcs_raw)}, Danmaku: {len(all_dms)}')

# VC comparison
if http_vcs and pw_vcs_raw:
    log('\nVC comparison (PW vs HTTP, first 15):')
    min_l = min(15, len(http_vcs), len(pw_vcs_raw))
    for i in range(min_l):
        ht = http_vcs[i]
        pt = pw_vcs_raw[i]
        match = chr(0x2713) if ht[1] == pt[1] else chr(0x2717)
        log(f'  t={ht[0]:.0f}s HTTP={ht[1]} PW={pt[1]} {match}')

# Danmaku samples
if all_dms:
    log(f'\nDanmaku samples ({len(all_dms)} total):')
    for dm in all_dms[:15]:
        log(f'  t={dm["offset"]:.0f}s: {dm["text"][:60]}')

# ===== Generate ASS + MKV =====
log('\nGenerating ASS + MKV...')
import glob
mp4_files = sorted(glob.glob(f'{seg_prefix}*.mp4'))
if not mp4_files:
    single = f'{OUT}/{room_id}_single.mp4'
    subprocess.run(['ffmpeg','-y','-i',flv_url,'-c','copy','-t',str(duration),single], capture_output=True, timeout=130)
    if os.path.exists(single):
        mp4_files = [single]

for seq_idx, mp4_path in enumerate(mp4_files):
    seg_b = seq_idx * 900
    seg_e = seg_b + 900
    seg_vcs = [v for v in http_vcs if seg_b - 5 <= v[0] <= seg_e + 5]
    seg_dms = [d for d in all_dms if seg_b - 5 <= d['offset'] <= seg_e + 5]
    log(f'  Seg {seq_idx}: {len(seg_vcs)} VC, {len(seg_dms)} DM')

    ass_path = mp4_path.replace('.mp4', '.ass')
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write('[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nTimer: 100.0000\n\n')
        f.write('[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n')
        f.write('Style: vc,Arial,17,&HFFFFFF,&HFFFFFF,&H000000,&H000000,0,0,0,0,100,100,0,0,1,2,0,8,10,10,10,1\n')
        f.write('Style: dm,Arial,22,&HFFFFFF,&HFFFFFF,&H0000FF,&H000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n')
        f.write('\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')

        for v in seg_vcs:
            ass_ts = max(0, v[0] - seg_b)
            st = f'{int(ass_ts//3600):01d}:{int(ass_ts%3600//60):02d}:{ass_ts%60:05.2f}'
            et = f'{int((ass_ts+3)//3600):01d}:{int((ass_ts+3)%3600//60):02d}:{(ass_ts+3)%60:05.2f}'
            f.write(f'Dialogue: 1,{st},{et},vc,,0,0,0,,{{\\move(960,30,960,30)}}在线: {v[1]}\\N')

        for dm in seg_dms:
            ass_ts = max(0, dm['offset'] - seg_b)
            st = f'{int(ass_ts//3600):01d}:{int(ass_ts%3600//60):02d}:{ass_ts%60:05.2f}'
            et = f'{int((ass_ts+5)//3600):01d}:{int((ass_ts+5)%3600//60):02d}:{(ass_ts+5)%60:05.2f}'
            f.write(f'Dialogue: 0,{st},{et},dm,,0,0,0,,{dm["text"]}\\N')

    mkv = mp4_path.replace('.mp4', '.mkv')
    r = subprocess.run(['ffmpeg','-y','-i',mp4_path,'-i',ass_path,'-c:v','copy','-c:a','copy','-c:s','ass',mkv], capture_output=True, timeout=30)
    if r.returncode == 0:
        log(f'    MKV: {os.path.basename(mkv)} ({os.path.getsize(mkv)} bytes)')

browser.close()
pw_instance.__exit__(None, None, None)

log('\n=== SUMMARY ===')
log(f'Room: {room_id}, Duration: {duration}s')
log(f'Cookies: {len(cookies)}')
log(f'HTTP VC: {len(http_vcs)}, PW VC: {len(pw_vcs_raw)}, DM: {len(all_dms)}')
log('DONE')
