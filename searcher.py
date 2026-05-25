#!/usr/bin/env python3
"""抖音搜索发现 - requests 搜索页+API混合版"""
import os, sys, json, time, re, base64, urllib.request, urllib.error, traceback as tb
from datetime import datetime
from urllib.parse import quote

GH_REPO = os.environ.get("GH_REPO", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
DOUYIN_COOKIE = os.environ.get("DOUYIN_COOKIE", "")
SEARCH_KEYWORDS = [
    ("泰国", 1000),
    ("美国", 1000),
    ("日本", 1000),
    ("越南", 1000),
]

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def get_rooms():
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GH_REPO}/contents/rooms.txt",
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        content = base64.b64decode(data["content"]).decode("utf-8")
        rooms = {}
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                rid = line.split("=")[0].strip()
            else:
                rid = line.split()[0].strip()
            if rid.isdigit():
                rooms[rid] = line
        return content, rooms, data["sha"]
    except Exception as e:
        log(f"获取rooms.txt失败: {e}")
        return "", {}, ""

def update_rooms(content, new_rooms, sha):
    added = 0
    for rid, line in new_rooms.items():
        if rid not in content:
            content += line + "\n"
            added += 1
    if added == 0:
        log("没有新房间需要添加")
        return True
    b64 = base64.b64encode(content.encode("utf-8")).decode()
    commit = json.dumps({"message": f"searcher: 新增 {added} 个直播间",
        "content": b64, "sha": sha}).encode()
    req = urllib.request.Request(f"https://api.github.com/repos/{GH_REPO}/contents/rooms.txt",
        data=commit, headers={"Authorization": f"Bearer {GH_TOKEN}", "Content-Type": "application/json"},
        method="PUT")
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
        log(f"rooms.txt更新成功: {resp['commit']['sha'][:7]} (新增{added}个)")
        return True
    except Exception as e:
        log(f"更新rooms.txt失败: {e}")
        return False

def search_keyword(keyword, min_watchers, cookie_str):
    """搜索一个关键词，返回 [(rid, anchor, watchers)]"""
    results = []
    
    # 方式1: 直接请求搜索页HTML，从页面中提取房间ID和人数
    try:
        url = f"https://www.douyin.com/search/{quote(keyword)}?type=live"
        log(f"请求搜索页: {url}")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": cookie_str,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        resp = urllib.request.urlopen(req, timeout=30)
        html = resp.read().decode("utf-8", errors="replace")
        
        # 从HTML中提取所有房间ID
        room_ids = set(re.findall(r'live\.douyin\.com/(\d{12,20})', html))
        log(f"搜索页HTML中找到 {len(room_ids)} 个房间ID")
        
        if room_ids:
            # 打印前几个id
            log(f"房间ID: {list(room_ids)[:10]}")
            for rid in list(room_ids)[:30]:
                results.append((rid, rid, 0))
    except Exception as e:
        log(f"搜索页请求失败: {e}")
    
    # 方式2: 尝试搜索API
    try:
        api_url = f"https://www.douyin.com/aweme/v1/web/live/search/?keyword={quote(keyword)}&type=0&offset=0&count=20&source=0&search_source=tab_search"
        req2 = urllib.request.Request(api_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": cookie_str,
            "Accept": "application/json",
            "Referer": f"https://www.douyin.com/search/{quote(keyword)}?type=live",
        })
        resp2 = urllib.request.urlopen(req2, timeout=30)
        api_data = json.loads(resp2.read().decode("utf-8"))
        log(f"API返回: {json.dumps(api_data)[:400]}")
        
        # 从API中提取房间
        data_list = api_data
        if isinstance(api_data, dict):
            data_list = api_data.get("data", api_data.get("items", []))
            if isinstance(data_list, dict):
                data_list = data_list.get("data", data_list.get("items", []))
        
        if isinstance(data_list, list):
            for item in data_list:
                if isinstance(item, dict):
                    rid = item.get("room_id") or item.get("id") or item.get("aweme_id", "")
                    watchers = item.get("user_count") or item.get("watch_count") or 0
                    anchor = item.get("anchor_name") or item.get("nickname") or item.get("nick", "") or ""
                    if rid and str(rid).isdigit():
                        results.append((str(rid), anchor or str(rid), int(watchers)))
    except Exception as e:
        log(f"API请求失败: {e}")
    
    # 方式3: 对于搜索结果中发现的房间，尝试打开直播间页获取人数
    # 从方式1拿到的rid（在线=0），尝试访问直播间确认
    final = []
    seen = set()
    for rid, anchor, watchers in results:
        if rid in seen:
            continue
        seen.add(rid)
        if watchers > 0:
            # API直接给了人数
            if watchers >= min_watchers:
                final.append((rid, anchor, watchers))
        else:
            # 需要从HTML获取人数
            try:
                live_url = f"https://live.douyin.com/{rid}"
                req3 = urllib.request.Request(live_url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Cookie": cookie_str,
                })
                resp3 = urllib.request.urlopen(req3, timeout=15)
                live_html = resp3.read().decode("utf-8", errors="replace")
                
                # 检查是否在直播
                if "暂停" in live_html and "直播已结束" in live_html:
                    continue
                
                # 提取在线人数
                w = 0
                for p in [r'([\d.]+)\s*万?\s*[人着][在正]', r'(\d[\d,]*)\s*[人着]']:
                    m = re.search(p, live_html)
                    if m:
                        s = m.group(1).replace(",", "")
                        w = int(float(s.replace("万", ""))*10000) if "万" in s else int(s)
                        break
                
                # 提取主播名
                aname = anchor
                if not aname or aname == rid:
                    title_m = re.search(r'<title>([^<]+)', live_html)
                    if title_m:
                        aname = title_m.group(1).split("-")[0].strip()
                
                if w >= min_watchers:
                    final.append((rid, aname, w))
            except:
                pass
    
    return final

def main():
    if not DOUYIN_COOKIE:
        log("需要 DOUYIN_COOKIE 环境变量")
        return
    if not GH_REPO or not GH_TOKEN:
        log("需要 GH_REPO 和 GH_TOKEN")
        return
    
    try:
        cookie_dict = json.loads(base64.b64decode(DOUYIN_COOKIE).decode("utf-8"))
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookie_dict])
        log(f"Cookie: {len(cookie_dict)}条")
    except Exception as e:
        log(f"Cookie解析失败: {e}")
        return
    
    content, existing_rooms, sha = get_rooms()
    if not sha:
        log("无法获取rooms.txt")
        return
    log(f"当前房间数: {len(existing_rooms)}")
    
    all_new = {}
    
    for keyword, min_watchers in SEARCH_KEYWORDS:
        log(f"\n===== 搜索: {keyword} (>={min_watchers}人) =====")
        found = search_keyword(keyword, min_watchers, cookie_str)
        
        for rid, anchor, watchers in found:
            if rid not in existing_rooms and rid not in all_new:
                all_new[rid] = f"{rid}={anchor}(搜索:{keyword})"
                log(f"  ✅ {rid} = {anchor} 在线={watchers}")
    
    if all_new:
        log(f"\n共发现 {len(all_new)} 个新直播间")
        update_rooms(content, all_new, sha)
    else:
        log("\n没有发现新直播间")

if __name__ == "__main__":
    main()
