import urllib.request as u, base64 as b, json, os, sys, time

GH_TOKEN = os.environ.get('GH_TOKEN', '')
OWNER = 'tangwei880620-rgb'
REPO = 'douyin-recorder'
hd = {'Authorization': 'Bearer ' + GH_TOKEN, 'Accept': 'application/vnd.github+json'} if GH_TOKEN else {'Accept': 'application/vnd.github+json'}

def api(path):
    req = u.Request('https://api.github.com/repos/' + OWNER + '/' + REPO + path, headers=hd)
    return json.loads(u.urlopen(req, timeout=30).read())

# 1. Load rooms
room_data = api('/contents/rooms.txt')
room_text = b.b64decode(room_data['content']).decode('utf-8')
room_names = {}
for line in room_text.split('\n'):
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    parts = line.split('=', 1)
    if len(parts) < 2:
        parts = line.split(',', 1)
    rid = parts[0].strip()
    name = parts[1].strip() if len(parts) > 1 else rid
    room_names[rid] = name

print(f'Loaded {len(room_names)} rooms')

# 2. Get all releases
releases = api('/releases?per_page=100')
print(f'Found {len(releases)} releases')

# 3. Collect .txt files grouped by room
groups = {}
total = 0

for rel in releases:
    assets = api('/releases/' + str(rel['id']) + '/assets')
    for a in assets:
        name = a['name']
        if not name.endswith('.txt'):
            continue
        # Parse roomID_start_end(.dot).txt
        base = name[:-4]  # remove .txt
        norm = base.replace('.', '_')
        parts = norm.split('_')
        if len(parts) < 3:
            continue
        rid = parts[0]
        start_ts = parts[1]
        end_ts = '_'.join(parts[2:])

        download_url = ('https://raw.githubusercontent.com/' + OWNER + '/' + REPO +
                        '/main/' + rel['tag_name'] + '/' + name)

        if rid not in groups:
            groups[rid] = []
        groups[rid].append({
            'name': name,
            'start': start_ts,
            'end': end_ts,
            'url': download_url
        })
        total += 1

# 4. Sort by time descending in each group
for rid in groups:
    groups[rid].sort(key=lambda x: x['start'], reverse=True)

# 5. Build output: list of anchor entries
output = []
for rid, items in groups.items():
    display_name = room_names.get(rid, rid)
    output.append({
        'id': rid,
        'name': display_name,
        'transcripts': items
    })

# Sort anchors by most recent transcript
output.sort(key=lambda x: x['transcripts'][0]['start'] if x['transcripts'] else '', reverse=True)

result = {
    'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
    'room_count': len(groups),
    'transcript_count': total,
    'anchors': output
}

print(json.dumps({'updated': result['updated'], 'room_count': result['room_count'], 'transcript_count': result['transcript_count']}))

# Write output
out_path = sys.argv[1] if len(sys.argv) > 1 else 'transcripts_index.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print('Written to ' + out_path)
