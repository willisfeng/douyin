#!/usr/bin/env python3
"""Build releases_index.json - pre-computed index of all release assets for the frontend.
Runs in CI with GH_TOKEN, so API calls don't count against the anonymous rate limit."""
import json, os, sys, re, urllib.request, base64, time as _time

GH_REPO = os.environ.get("GH_REPO", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")

if not GH_REPO or not GH_TOKEN:
    print("ERROR: GH_REPO and GH_TOKEN env vars required")
    sys.exit(1)

AUTH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json"
}


def fetch_json(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=AUTH_HEADERS)
            resp = urllib.request.urlopen(req, timeout=60)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 403 and "rate limit" in str(e.read().decode(errors="ignore")).lower():
                wait = (attempt + 1) * 30
                print(f"  Rate limited, waiting {wait}s (attempt {attempt+1}/{retries})...")
                _time.sleep(wait)
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                wait = (attempt + 1) * 10
                print(f"  Request failed, retrying in {wait}s (attempt {attempt+1}/{retries})...")
                _time.sleep(wait)
                continue
            raise


def fetch_rooms():
    """Load rooms.txt from the repo to get room_id -> anchor_name mapping."""
    try:
        data = fetch_json(f"https://api.github.com/repos/{GH_REPO}/contents/rooms.txt")
        content = base64.b64decode(data["content"]).decode("utf-8")
        rooms = []
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("=", 1)
            if len(parts) < 2:
                parts = line.split(",", 1)
            rid = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else rid
            rooms.append({"id": rid, "name": name})
        return rooms
    except Exception as e:
        print(f"Warning: could not load rooms.txt: {e}")
        return []


def parse_filename(name):
    """Mirrors the JS pfn() logic: extract room_id, start, end from filename."""
    parts = name.split(".")
    if not parts[0]:
        return None
    pre = parts[0]

    # 0) page_data format: page_data_roomid.YYYYMMDD_HHMMSS.json
    m_pd = re.match(r"^page_data_(\d{9,})$", pre)
    if m_pd and len(parts) >= 2 and re.match(r"^\d{8}_\d{6}$", parts[1]):
        return {"id": m_pd.group(1), "s": parts[1], "e": None}

    # 0.1) segment format: roomid_start_seq
    m0 = re.match(r"^(\d+)_(\d{8}_\d{6})_\d{3}$", pre)
    if m0:
        return {"id": m0.group(1), "s": m0.group(2), "e": None}

    # 0.5) ass/transcript format: roomid_seq.YYYYMMDD_HHMMSS.ext
    # e.g. 215933010618_000.20260528_030014.ass
    if len(parts) >= 3:
        m0a = re.match(r"^(\d+)_(\d{3})$", pre)
        if m0a and re.match(r"^\d{8}_\d{6}$", parts[1]):
            return {"id": m0a.group(1), "s": parts[1], "e": None}

    # 1) roomid_start~end
    m = re.match(r"^(\d+)_(\d{8}_\d{6})~(\d{8}_\d{6})$", pre)
    if m:
        return {"id": m.group(1), "s": m.group(2), "e": m.group(3)}

    # 2) roomid_start_end
    m = re.match(r"^(\d+)_(\d{8}_\d{6})_(\d{8}_\d{6})$", pre)
    if m:
        return {"id": m.group(1), "s": m.group(2), "e": m.group(3)}

    # 3) roomid_start.end (dot-separated)
    if len(parts) >= 3:
        m2 = re.match(r"^(\d+)_(\d{8}_\d{6})$", pre)
        if m2 and re.match(r"^\d{8}_\d{6}$", parts[1]):
            return {"id": m2.group(1), "s": m2.group(2), "e": parts[1]}

    # 4) roomid_FULL_HD1_start
    m = re.match(r"^(\d+)_[A-Z0-9_]+_(\d{8}_\d{6})$", pre)
    if m:
        return {"id": m.group(1), "s": m.group(2), "e": None}

    # 5) roomid_start
    m = re.match(r"^(\d+)_(\d{8}_\d{6})$", pre)
    if m:
        return {"id": m.group(1), "s": m.group(2), "e": None}

    return None


def guess_type(name):
    n = name.lower()
    if n.endswith(".mp4") or n.endswith(".flv") or n.endswith(".webm") or n.endswith(".mkv"):
        return "v"
    if n.endswith(".wav") or n.endswith(".mp3") or n.endswith(".aac") or n.endswith(".ogg"):
        return "a"
    if n.endswith(".srt"):
        return "s"
    return "t"


def main():
    rooms = fetch_rooms()
    room_map = {r["id"]: r["name"] for r in rooms}
    print(f"Loaded {len(rooms)} rooms")

    # Fetch all releases with assets
    all_releases = []
    page = 1
    while True:
        releases = fetch_json(
            f"https://api.github.com/repos/{GH_REPO}/releases?per_page=100&page={page}"
        )
        if not releases:
            break
        all_releases.extend(releases)
        page += 1
        print(f"  Fetched page {page-1}, {len(releases)} releases, total {len(all_releases)}")

    # Filter to releases with assets
    releases_with_assets = [r for r in all_releases if r.get("assets")]
    print(f"Releases with assets: {len(releases_with_assets)}")

    # Build name map from release body: "anchor_name/filename"
    name_map = {}
    for r in releases_with_assets:
        body = r.get("body", "") or ""
        for line in body.split("\n"):
            idx = line.find("/")
            if idx > 0 and idx < len(line) - 1:
                name_map[line[idx+1:]] = line[:idx]

    # Build assets array (mirror of JS `as` array)
    assets = []
    for r in releases_with_assets:
        for a in r["assets"]:
            aname = a["name"]
            # Skip meta JSON files
            if "_meta." in aname and aname.endswith(".json"):
                continue
            if re.search(r"_meta\.json$", aname):
                continue

            t = guess_type(aname)
            p = parse_filename(aname)
            rid = p["id"] if p else None
            an = name_map.get(aname) or (room_map.get(rid) if rid else None) or rid or "oth"

            assets.append({
                "id": a["id"],
                "n": aname,
                "z": a["size"],
                "u": a["browser_download_url"],
                "c": a.get("created_at", r.get("created_at", "")),
                "ty": t,
                "ri": rid,
                "an": an,
                "st": p["s"] if p else None,
                "et": p["e"] if p else None,
            })

    print(f"Total assets: {len(assets)}")

    # Write index
    output_path = os.path.join(os.path.dirname(__file__) or ".", "docs", "releases_index.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"rooms": rooms, "assets": assets}, f, ensure_ascii=False, indent=1)

    print(f"Written to {output_path}")
    print(f"  Assets: {len(assets)}")
    print(f"  Rooms:  {len(rooms)}")


if __name__ == "__main__":
    main()
