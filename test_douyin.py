import urllib.request as uu
import sys

room_id = sys.argv[1] if len(sys.argv) > 1 else '30972107798'
ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
hdrs = {
    'User-Agent': ua,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}
req = uu.Request('https://live.douyin.com/' + room_id, headers=hdrs)
resp = uu.urlopen(req, timeout=30)
raw = resp.read()
html = raw.decode('utf-8', errors='replace')
flv_in = 'flv_pull_url' in html
ws_in = 'web_stream_url' in html
print('Status: ' + str(resp.status) + '  Size: ' + str(len(html)))
print('flv_pull_url: ' + str(flv_in))
print('web_stream_url: ' + str(ws_in))
idx = html.find('web_stream_url')
if idx >= 0:
    print('web_stream_url snippet: ' + repr(html[idx:idx+60]))
print('Server: ' + str(resp.headers.get('Server', '?')))
sc = resp.headers.get('Set-Cookie', 'none')
print('Set-Cookie: ' + sc[:60])
print('data-cluster present: ' + str('data-cluster' in html))
cluster_idx = html.find('data-cluster')
if cluster_idx >= 0:
    print('data-cluster value: ' + html[cluster_idx:cluster_idx+30])
