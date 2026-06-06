import os, time, urllib.request, urllib.parse, json

BID = os.environ.get('BROWSER_ID', '?')
ROOM_URL = os.environ.get('ROOM_URL', 'https://live.douyin.com/919096107345')

# Extract room short ID
room_short = ROOM_URL.rstrip('/').split('/')[-1]
print(f'[Browser {BID}] Room: {room_short}')

# Step 1: Get the room page HTML + cookies
req1 = urllib.request.Request(ROOM_URL, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
})
try:
    r = urllib.request.urlopen(req1, timeout=15)
    html = r.read().decode('utf-8', errors='replace')
    # Get cookies from response
    set_cookies = r.headers.get_all('Set-Cookie') if hasattr(r.headers, 'get_all') else []
    print(f'[Browser {BID}] Page loaded: {len(html)} bytes')
    print(f'[Browser {BID}] Status: {r.status}')
    # Check if the page looks like a real room or a block page
    if 'captcha' in html.lower() or 'verify' in html.lower():
        print(f'[Browser {BID}] WARNING: Captcha/verify page detected!')
    elif '直播' in html or 'live' in html.lower():
        print(f'[Browser {BID}] Page contains live room content')
    else:
        print(f'[Browser {BID}] Page looks unusual - first 500 chars:')
        print(html[:500])
except Exception as e:
    print(f'[Browser {BID}] Page load error: {e}')

# Step 2: Try to call the enter API directly
import ssl
ctx = ssl.create_default_context()

enter_url = f'https://live.douyin.com/webcast/room/web/enter/?aid=6383&app_name=douyin_web&live_id=1&device_platform=web&language=zh-CN&enter_from=link_share&cookie_enabled=true&screen_width=1920&screen_height=1080&browser_language=zh-CN&browser_platform=Win32&browser_name=Mozilla&browser_version=5.0+(Windows+NT+10.0%3B+Win64%3B+x64)+AppleWebKit%2F537.36+(KHTML%2C+like+Gecko)+Chrome%2F120.0.0.0+Safari%2F537.36&browser_online=true&tz_name=Asia%2FShanghai&room_id={room_short}'

req2 = urllib.request.Request(enter_url, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Referer': ROOM_URL,
    'Origin': 'https://live.douyin.com',
})
try:
    r2 = urllib.request.urlopen(req2, timeout=10)
    data = r2.read()
    print(f'[Browser {BID}] Enter API: {r2.status}, {len(data)} bytes')
    print(f'[Browser {BID}] Response: {data[:300]}')
except urllib.error.HTTPError as e:
    print(f'[Browser {BID}] Enter API HTTP error: {e.code} {e.reason}')
    print(f'[Browser {BID}] Body: {e.read()[:300]}')
except Exception as e:
    print(f'[Browser {BID}] Enter API error: {e}')

# Step 3: Print public IP
try:
    ip_req = urllib.request.Request('https://api.ipify.org?format=text', headers={'User-Agent': 'curl/7.0'})
    ip = urllib.request.urlopen(ip_req, timeout=10).read().decode().strip()
    print(f'[Browser {BID}] PUBLIC IP: {ip}')
except Exception as e:
    print(f'[Browser {BID}] IP lookup failed: {e}')

print(f'[Browser {BID}] Done')
