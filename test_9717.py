import subprocess, sys, re, os, time, json

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    sys.stdout.flush()

token = os.environ.get("GH_TOKEN", "")
rid = "97171913600"
ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
cookie = os.environ.get("DOUYIN_COOKIE", "")

log(f"Testing room {rid}")

import subprocess
result = subprocess.run(["curl", "-s", "-L", "--max-time", "25",
       "-H", "User-Agent: " + ua,
       "https://live.douyin.com/" + rid], capture_output=True, timeout=30)
html = result.stdout.decode("utf-8", errors="replace")
log(f"HTML size: {len(html)}")

if "flv_pull_url" not in html:
    log("NOT LIVE")
    sys.exit(0)
log("LIVE!")

# Find FULL_HD1 URL in HTML
idx = html.find("FULL_HD1")
chunk = html[idx:idx+500]

# The HTML contains JSON with \\u escaped chars. Extract the http URL following FULL_HD1
# JSON format: "FULL_HD1":"http://...?"
# But the http may contain \\u0026 (which is &)
# Simple split approach:
before, after = chunk.split("http", 1)
url_raw = "http" + after
# Now url_raw looks like: "://...flv?keeptime=...\\u0026wsSecret=...\\u0026..."
# Find the closing quote of the JSON value - it's "
# but the value might contain escaped quotes
# The pattern is: after http, everything until the next unescaped " that is not preceded by backslash

# Simplest: extract until the next " followed by , or }
import re
m = re.match(r'([^"]+)', url_raw)
if m:
    url = m.group(1)
    # Replace unicode escapes
    url = url.replace("\\u0026", "&").replace("\\u003d", "=").replace("\\/", "/")
    log(f"Full decoded URL: {url[:250]}")
else:
    log("Could not extract URL")
    log(f"url_raw[:200]: {url_raw[:200]}")
    sys.exit(1)

# Search for & in URL
log(f"Has wsSecret: {'wsSecret' in url}")
log(f"Has wsTime: {'wsTime' in url}")

# curl with the decoded URL
log("=== curl with decoded URL ===")
curl_cmd = ["curl", "-s", "-o", "/tmp/flv_data2.bin", "-w", "%{http_code}",
            "--max-time", "10",
            "-H", "User-Agent: " + ua,
            "-H", "Referer: https://live.douyin.com/",
    ]
if cookie:
    curl_cmd += ["-H", "Cookie: " + cookie]

try:
    r = subprocess.run(curl_cmd + [url], capture_output=True, timeout=15)
    http_code = r.stdout.decode().strip()
    sz = os.path.getsize("/tmp/flv_data2.bin") if os.path.exists("/tmp/flv_data2.bin") else 0
    log(f"curl result: HTTP {http_code}, size={sz}")
    if sz > 0 and sz < 5000:
        log(f"Content: {repr(open('/tmp/flv_data2.bin','rb').read(200))}")
    elif sz > 0:
        log(f"First 100 bytes hex: {repr(open('/tmp/flv_data2.bin','rb').read(100))}")
except Exception as e:
    log(f"curl error: {e}")

# ffmpeg with decoded URL
log("=== ffmpeg with decoded URL ===")
outfile = "/tmp/test_9717_fixed.mp4"
logfile = "/tmp/test_9717_fixed.log"
cookie_hdr = "Cookie: " + cookie + "\\r\\n" if cookie else ""
headers_arg = "User-Agent: " + ua + "\\r\\nReferer: https://live.douyin.com/\\r\\n" + cookie_hdr
proc = subprocess.Popen(
    ["ffmpeg", "-y", "-loglevel", "info",
     "-headers", headers_arg,
     "-user_agent", ua,
     "-i", url,
     "-c", "copy", "-t", "30", "-f", "mp4", outfile],
    stdout=subprocess.DEVNULL, stderr=open(logfile, "w"))
ret = proc.wait(35)
log(f"ffmpeg return code: {ret}")
with open(logfile) as f:
    log_lines = f.read()
    log(log_lines[-800:])
if os.path.exists(outfile):
    log(f"ffmpeg output: {os.path.getsize(outfile)} bytes")
