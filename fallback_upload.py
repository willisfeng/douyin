import os, json, urllib.request

token = os.environ['GH_TOKEN']
repo = os.environ['GH_REPO']
run_id = os.environ['GH_RUN_ID']

gh = {'Accept':'application/vnd.github+json','Authorization':'Bearer '+token}
existing = set()
page = 1
while True:
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/'+repo+'/releases?per_page=100&page='+str(page), headers=gh)).read())
    except: break
    if not r: break
    for rel in r:
        for a in rel.get('assets', []): existing.add(a['name'])
    page += 1

d = '/tmp/recordings/'
if not os.path.exists(d):
    exit(0)

release_url = None
try:
    r = json.loads(urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/'+repo+'/releases/tags/rec-'+run_id, headers=gh)).read())
    release_url = r.get('upload_url','').split('{')[0]
except:
    try:
        body = json.dumps({'tag_name':'rec-'+run_id,'name':'rec-'+run_id,'body':'','draft':False,'prerelease':True}).encode()
        r = json.loads(urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/'+repo+'/releases',
            data=body, headers={**gh,'Content-Type':'application/json'}, method='POST')).read())
        release_url = r.get('upload_url','').split('{')[0]
    except: pass

if not release_url:
    print('No release available')
    exit(0)

for fn in os.listdir(d):
    fp = os.path.join(d, fn)
    if not os.path.isfile(fp) or fn in existing:
        if fn in existing: print('SKIP (exists):', fn)
        continue
    # Auto-rename if no ~ in filename
    if '~' not in fn:
        try:
            from datetime import datetime
            ets = datetime.now().strftime('%Y%m%d_%H%M%S')
            dn = os.path.dirname(fp)
            bn = fn.rsplit('.', 1)[0]
            ext = fn.split('.')[-1] if '.' in fn else ''
            nfn = bn + '~' + ets + '.' + ext
            np = os.path.join(dn, nfn)
            os.rename(fp, np)
            print('FALLBACK RENAME:', fn, '->', nfn)
            fp = np
            fn = nfn
        except Exception as e:
            print('FALLBACK RENAME fail:', e)
    with open(fp, 'rb') as f:
        data = f.read()
    url = release_url + '?name=' + urllib.parse.quote(fn)
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data, headers={**gh,'Content-Type':'application/octet-stream'}, method='POST'))
        print('UPLOADED:', fn, len(data)//1024//1024, 'MB')
    except Exception as e:
        print('FAILED:', fn, e)

# Notify transcription workflow
print('Triggering transcription check via repository_dispatch...')
try:
    body = json.dumps({'event_type': 'transcribe_self_renew'}).encode()
    req = urllib.request.Request('https://api.github.com/repos/'+repo+'/dispatches',
        data=body, headers={**gh,'Content-Type':'application/json'}, method='POST')
    urllib.request.urlopen(req, timeout=30)
    print('  Trigger OK')
except Exception as e:
    print('  Trigger failed:', e)

