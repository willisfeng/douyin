import json, re
d = json.load(open('docs/releases_index.json', encoding='utf-8'))
rids = {r['id']: r['name'] for r in d['rooms']}

for a in d['assets']:
    n = a['n']
    if n.startswith('page_data_'):
        # page_data_847349416634.20260528_083757.json
        m = re.match(r'^page_data_(\d{9,})\.(\d{8})_(\d{6})\.json$', n)
        if m:
            rid = m.group(1)
            print(f'  rid={rid} name={rids.get(rid,"?")} date={m.group(2)} time={m.group(3)}')
            print(f'    st={a.get("st")} et={a.get("et")} ri={a.get("ri")} an={a.get("an")}')
        else:
            print(f'  UNMATCHED: {n}')
            print(f'    st={a.get("st")} et={a.get("et")} ri={a.get("ri")} an={a.get("an")}')
