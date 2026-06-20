import urllib.request, json, base64, os, sys

token = os.environ.get('GH_TOKEN', '')
if not token:
    print("ERROR: GH_TOKEN not set")
    sys.exit(1)

repo = 'willisfeng/douyin'
branch = 'main'
commit_msg = 'fix: 文件名时间戳使用中国时区(Asia/Shanghai)'

files = ['recorder.py', 'fallback_upload.py', 'searcher.py', 'transcriber.py', 'build_transcripts_index.py']

headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/vnd.github+json',
    'Content-Type': 'application/json'
}

for fname in files:
    with open(fname, 'rb') as f:
        content = base64.b64encode(f.read()).decode()

    # Get current file sha
    url = f'https://api.github.com/repos/{repo}/contents/{fname}?ref={branch}'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        sha = data['sha']
    except Exception as e:
        print(f'ERROR getting sha for {fname}: {e}')
        continue

    # Update file
    body = json.dumps({
        'message': commit_msg,
        'content': content,
        'sha': sha,
        'branch': branch
    }).encode()

    req2 = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/contents/{fname}',
        data=body,
        headers=headers,
        method='PUT'
    )
    try:
        resp2 = urllib.request.urlopen(req2, timeout=30)
        result = json.loads(resp2.read())
        print(f'OK: {fname}')
    except Exception as e:
        print(f'FAILED: {fname}: {e}')

print('Done.')
