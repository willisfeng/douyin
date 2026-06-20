import urllib.request, subprocess, time, re, os, sys

url = 'https://live.douyin.com/319074168229'
for i in range(10):
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.douyin.com/'
        })
        r = urllib.request.urlopen(req, timeout=10)
        html = r.read().decode('utf-8', errors='replace')
        has_flv = 'flv_pull_url' in html
        print(f'Round {i+1}: len={len(html)} flv={has_flv}', flush=True)
        if has_flv:
            for m in re.finditer(r'["\\]+(FULL_HD1)["\\]+\s*[:=]\s*["\\]+(https?://[^"]+)', html):
                flv = m.group(2).replace('\\/', '/').replace('\\u0026', '&')
                print(f'FLV: {flv[:120]}', flush=True)
                r2 = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error',
                    '-headers', 'User-Agent: Mozilla/5.0\r\nReferer: https://live.douyin.com/\r\n',
                    '-i', flv, '-t', '10', '-c', 'copy', '/tmp/test_seg.mp4'],
                    capture_output=True, timeout=25)
                if r2.returncode == 0:
                    sz = os.path.getsize('/tmp/test_seg.mp4')
                    print(f'ffmpeg OK: {sz} bytes', flush=True)
                else:
                    print(f'ffmpeg ret={r2.returncode} err={r2.stderr.decode()[:200]}', flush=True)
                break
    except Exception as e:
        print(f'Error round {i+1}: {e}', flush=True)
    time.sleep(60)
print('TESTS_DONE', flush=True)
