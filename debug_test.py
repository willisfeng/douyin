#!/usr/bin/env python3
import requests, re, sys

room_id = sys.argv[1] if len(sys.argv) > 1 else '932534335198'
ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

resp = requests.get(f'https://live.douyin.com/{room_id}',
    headers={'User-Agent': ua, 'Accept': 'text/html'}, timeout=30)
html = resp.text

print(f'Status: {resp.status_code} Size: {len(html)}')
print(f'Has flv_pull_url: {\"flv_pull_url\" in html}')
print(f'Has web_stream_url: {\"web_stream_url\" in html}')

# Print first 500 and last 500 chars
print(f'\n=== HEAD ===')
print(html[:500])
print(f'\n=== TAIL ===')
print(html[-500:])

# Check all keywords
for kw in ['flv_pull_url', 'web_stream_url', 'liveStatus', 'stream_url', 'pull_data', 'nickname', 'render_data', 'SSR']:
    if kw in html:
        idx = html.find(kw)
        print(f'\n{kw} at {idx}: {repr(html[max(0,idx-30):idx+100])}')
