import os
import re, sys, urllib.request, json, urllib.parse, glob, traceback

token = os.environ.get("GH_TOKEN", "")
repo = os.environ.get("GH_REPO", "")
run_id = os.environ.get("GH_RUN_ID", "")

if not token or not repo:
    print("Missing GH_TOKEN or GH_REPO")
    sys.exit(1)

tag = "cleanup-" + run_id
hd = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

# Find or create release
release = None
try:
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/releases/tags/" + tag, headers=hd)
    resp = urllib.request.urlopen(req, timeout=30)
    release = json.loads(resp.read())
    print("Using existing release:", tag)
except:
    print("Creating release:", tag)
    try:
        body = json.dumps({
            "tag_name": tag, "name": tag,
            "body": "Transcription results for run " + run_id,
            "target_commitish": "main"
        }).encode()
        req = urllib.request.Request(
            "https://api.github.com/repos/" + repo + "/releases",
            data=body, headers=hd, method="POST")
        resp = urllib.request.urlopen(req, timeout=30)
        release = json.loads(resp.read())
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)

if not release:
    print("No release found or created")
    sys.exit(1)

release_url = release.get("html_url", "")
print("")
print("========================================")
print("转录文件 Release:")
print("  " + release_url)
print("========================================")
print("")

upload_url_template = release.get("upload_url", "")
if not upload_url_template:
    print("Release has no upload_url")
    sys.exit(1)

# Find files
files = sorted(glob.glob("/tmp/recordings/*.txt") + glob.glob("/tmp/recordings/*.srt"))
if not files:
    files = sorted(glob.glob("/tmp/transcripts/*.txt") + glob.glob("/tmp/transcripts/*.srt"))

print("找到 %d 个转录文件:" % len(files))

for f in files:
    if not os.path.exists(f):
        continue
    n = os.path.basename(f)
    fsize = os.path.getsize(f)
    safe = urllib.parse.quote(n.encode("utf-8"))
    upload_url = upload_url_template.replace("{?name,label}", "?name=" + safe)
    try:
        with open(f, "rb") as fh:
            data = fh.read()
        uh = {"Authorization": "Bearer " + token, "Content-Type": "application/octet-stream", "Content-Length": str(len(data))}
        ureq = urllib.request.Request(upload_url, data=data, headers=uh, method="POST")
        uresp = urllib.request.urlopen(ureq, timeout=300)
        asset_id = json.loads(uresp.read()).get("id", "?")
        download_url = release_url + "/download/" + n
        print("  [OK] %s (%d bytes)" % (n, fsize))
        print("       下载: " + download_url)
    except Exception as e:
        # Try to upload without url-encoding (GitHub handles UTF-8 in URL)
        print("  [FAIL] %s: %s" % (n, str(e)[:100]))

print("")
print("========================================")
print("全部上传完成")
print("========================================")
