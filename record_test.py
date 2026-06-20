import asyncio, os, re, time, json, subprocess, urllib.request

ROOM_URL = os.environ.get("ROOM_URL")
if not ROOM_URL:
    print("ERROR: ROOM_URL not set")
    exit(1)

ROOM_SHORT = ROOM_URL.rstrip("/").split("/")[-1]
DURATION_MIN = int(os.environ.get("DURATION_MIN", "1"))

print(f"Room: {ROOM_SHORT}, Duration: {DURATION_MIN}min")

from playwright.async_api import async_playwright

QUALITY = {"FULL_HD1": 4, "HD1": 3, "SD1": 2, "SD2": 1}

# System messages to filter out
SYSTEM_MSGS = [
    "欢迎来到直播间",
    "抖音严禁",
    "未成年人",
    "违法违规",
    "低俗色情",
    "人身伤害",
    "理性消费",
    "私下交易",
    "网络诈骗",
    "财产安全",
]


class DataStore:
    def __init__(self):
        self.viewer_counts = []
        self.danmaku = []
        self.seen_danmaku = set()
        self.t0 = None

    def set_start(self, t):
        self.t0 = t

    def add_viewer(self, ts, count):
        self.viewer_counts.append({"ts": ts, "count": count})

    def add_danmaku(self, ts, text):
        # Filter system messages
        for sys_msg in SYSTEM_MSGS:
            if sys_msg in text:
                return
        key = text.strip()
        if key and key not in self.seen_danmaku:
            self.seen_danmaku.add(key)
            self.danmaku.append({"ts": ts, "text": text})

    def to_dict(self):
        return {
            "room": ROOM_URL,
            "duration": (self.viewer_counts[-1]["ts"] - self.t0) if self.viewer_counts else 0,
            "viewer_counts": [
                {"offset": round(v["ts"] - self.t0, 1), "count": v["count"]}
                for v in self.viewer_counts
            ],
            "danmaku": [
                {"offset": round(d["ts"] - self.t0, 1), "text": d["text"]}
                for d in self.danmaku
            ],
        }


def extract_stream_url(html):
    found = []
    for m in re.finditer(
        r'["\\]+(FULL_HD1|HD1|SD1|SD2)["\\]+\s*[:=]\s*["\\]+(https?://[^"]+)',
        html,
    ):
        url = m.group(2).replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=")
        if url.startswith("http"):
            found.append((m.group(1), url))
    if found:
        best = max(found, key=lambda x: QUALITY.get(x[0], 0))
        return best[1]
    return None


async def capture_data(page, store):
    """Periodically capture viewer count and danmaku from DOM."""
    viewer_sel = '[data-e2e="live-room-audience"]'

    while True:
        try:
            now = time.time()

            # Viewer count
            viewer_el = await page.query_selector(viewer_sel)
            if viewer_el:
                txt = await viewer_el.text_content()
                if txt and txt.strip().isdigit():
                    store.add_viewer(now, int(txt.strip()))

            # Danmaku from DOM - use evaluate to find all visible chat messages
            new_msgs = await page.evaluate("""() => {
                const results = [];
                const allDivs = document.querySelectorAll('div');
                // Find the chat container by looking for many short text divs with Chinese patterns
                for (const div of allDivs) {
                    const text = (div.textContent || '').trim();
                    if (text.length > 2 && text.length < 150 &&
                        !div.querySelector('*') &&  // leaf node
                        (text.includes('\uff1a') || /[\u4e00-\u9fff]/.test(text) || text.length > 2)) {
                        // Check parent for chat-like structure
                        let p = div.parentElement;
                        let depth = 0;
                        while (p && depth < 4) {
                            const cls = p.className || '';
                            if (typeof cls === 'string' &&
                                (cls.includes('chat') || cls.includes('msg') || cls.includes('message') ||
                                 cls.includes('danmu') || cls.includes('list'))) {
                                const t = text.replace(/\\s+/g, ' ');
                                if (t.length > 2) results.push(t);
                                break;
                            }
                            p = p.parentElement;
                            depth++;
                        }
                    }
                }
                // Also try getting from the chatroom list directly
                const list = document.querySelector('[class*="webcast-chatroom"]');
                if (list) {
                    Array.from(list.querySelectorAll('div')).forEach(d => {
                        const t = (d.textContent || '').trim();
                        if (t.length > 2 && t.length < 150) results.push(t);
                    });
                }
                // Deduplicate
                return [...new Set(results)];
            }""")

            for msg in new_msgs:
                store.add_danmaku(now, msg)

            if len(store.viewer_counts) % 5 == 1 and store.viewer_counts:
                recent = store.danmaku[-3:] if store.danmaku else []
                print(
                    f"  viewer={store.viewer_counts[-1]['count']}, "
                    f"danmaku_total={len(store.danmaku)}, "
                    f"last=[{', '.join(d['text'][:15] for d in recent)}]"
                )

        except Exception as e:
            print(f"  Fetch err: {e}")

        await asyncio.sleep(2)


async def keep_alive(page):
    """Simulate human activity."""
    while True:
        try:
            x = 300 + (int(time.time() * 10) % 500)
            y = 200 + (int(time.time() * 7) % 300)
            await page.mouse.move(x, y)
            await asyncio.sleep(0.3)
            await page.mouse.move(x + 100, y + 50)
        except:
            pass
        await asyncio.sleep(8)


def generate_ass(data, output_path):
    """Generate ASS with scrolling danmaku."""
    duration = data["duration"]
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "Collisions: Normal",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Top-center for viewer count (Alignment=8)
        "Style: ViewerCount,Microsoft YaHei,34,&H00FFFFFF,&H00FFFFFF,&H80000000,&H00000000,"
        "1,0,0,0,100,100,0,0,1,2,0,8,50,50,50,1",
        # Scrolling danmaku from right to left (Alignment=2, left aligned)
        "Style: Danmaku,Microsoft YaHei,28,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,20,20,180,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def fmt_ts(s):
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h}:{m:02d}:{sec:05.2f}"

    # Viewer count - floating at top
    vc = data["viewer_counts"]
    for i, vp in enumerate(vc):
        start = max(0, vp["offset"] - 0.5)
        if i + 1 < len(vc):
            end = vc[i + 1]["offset"] - 0.3
        else:
            end = duration
        if end > start:
            lines.append(
                f'Dialogue: 0,{fmt_ts(start)},{fmt_ts(end)},ViewerCount,,0,0,0,,'
                f'{{\\an8}}{vp["count"]} 人在看'
            )

    # Danmaku - scrolling from right to left, each message 6 seconds to cross screen
    dm = data["danmaku"]
    if dm:
        scroll_time = 6  # seconds to scroll across
        appear_time = 0.5  # fade in time

        for i, dp in enumerate(dm):
            start_sec = max(0, dp["offset"])
            # Message scrolls: starts from right edge, moves to left
            # END position: after scroll_time, it should be at left edge
            end_sec = start_sec + scroll_time + appear_time

            # Start: right edge of screen
            # End: left edge (off screen to the left)
            start_x = 1920
            end_x = -len(dp["text"]) * 16  # roughly text width

            # Multiple lines for backlog
            line_offset = 0
            for j in range(1):
                y_pos = 1080 - 60 - (line_offset * 35)

                text = dp["text"][:60]  # limit text length
                # Use \move for scrolling effect
                lines.append(
                    f'Dialogue: 0,{fmt_ts(start_sec)},{fmt_ts(end_sec)},Danmaku,,0,0,0,,'
                    f'{{\\move({start_x},{y_pos},{end_x},{y_pos})}}{text}'
                )

    ass_content = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    viewer_pts = len(data["viewer_counts"])
    danmaku_pts = len(data["danmaku"])
    print(f"  ASS: {len(ass_content)} bytes, viewer={viewer_pts}, danmaku={danmaku_pts}")
    return ass_content


async def main():
    print("[1/3] Opening Playwright browser...")
    pw = await async_playwright().__aenter__()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    )
    page = await context.new_page()

    await page.goto(ROOM_URL, wait_until="domcontentloaded", timeout=30000)
    print("  Page loaded, waiting for stream data...")
    await asyncio.sleep(5)

    html = await page.content()
    stream_url = extract_stream_url(html)
    print(f"  Stream URL: {'YES' if stream_url else 'NO'}")

    if not stream_url:
        print("  ERROR: Could not find stream URL")
        await browser.close()
        return

    # Start ffmpeg
    print(f"\n[2/3] Recording {DURATION_MIN}min...")
    out = f"{ROOM_SHORT}_test.flv"
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner",
         "-headers", f"Referer: {ROOM_URL}\r\nUser-Agent: Mozilla/5.0",
         "-i", stream_url, "-t", str(DURATION_MIN * 60), "-c", "copy", out],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    print(f"  ffmpeg PID: {ffmpeg.pid}")

    # Start data capture
    print(f"\n[3/3] Capturing data + keeping alive for {DURATION_MIN}min...")
    store = DataStore()
    store.set_start(time.time())

    data_task = asyncio.create_task(capture_data(page, store))
    alive_task = asyncio.create_task(keep_alive(page))

    await asyncio.sleep(DURATION_MIN * 60)

    # Stop
    data_task.cancel()
    alive_task.cancel()
    try:
        await data_task
    except:
        pass
    try:
        await alive_task
    except:
        pass

    ffmpeg.terminate()
    ffmpeg.wait()
    await browser.close()

    elapsed = time.time() - store.t0
    sz = os.path.getsize(out) if os.path.exists(out) else 0
    print(f"\nDone! {elapsed:.0f}s, {out} ({sz} bytes)")
    print(f"  Viewer: {len(store.viewer_counts)} points, Danmaku: {len(store.danmaku)} items")

    if store.danmaku:
        print("  Sample messages:")
        for d in store.danmaku[-5:]:
            offset = d["ts"] - store.t0
            print(f"    [{offset:.0f}s] {d['text'][:60]}")

    # Save raw data
    data = store.to_dict()
    with open("page_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  JSON: page_data.json")

    # Generate ASS
    ass_path = f"{ROOM_SHORT}_overlay.ass"
    generate_ass(data, ass_path)
    print(f"  ASS: {ass_path}")


asyncio.run(main())
