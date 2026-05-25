#!/usr/bin/env python3
"""
百度网盘自动上传脚本
流程: refresh_token → access_token → 预创建 → 上传 → 创建文件
按主播名分文件夹上传 (从 _meta.json 读取)
环境变量:
  BAIDU_APP_KEY, BAIDU_SECRET_KEY, BAIDU_REFRESH_TOKEN
  BAIDU_REMOTE_DIR (可选, 默认 /apps/自动上传/抖音录制)
"""
import os
import sys
import json
import time
import hashlib
import re
import requests

APP_KEY = os.environ["BAIDU_APP_KEY"]
SECRET_KEY = os.environ["BAIDU_SECRET_KEY"]
REFRESH_TOKEN = os.environ["BAIDU_REFRESH_TOKEN"]
REMOTE_DIR = os.environ.get("BAIDU_REMOTE_DIR", "/apps/自动上传/抖音录制")
RECORDING_DIR = os.environ.get("OUTPUT_DIR", "/tmp/recordings")

# 百度网盘API端点
TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"
PRECREATE_URL = "https://pan.baidu.com/rest/2.0/xpan/file?method=precreate"
CREATE_URL = "https://pan.baidu.com/rest/2.0/xpan/file?method=create"


def log(msg):
    print(f"[Baidu] {msg}", flush=True)


def get_access_token():
    """用 refresh_token 换 access_token"""
    params = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": APP_KEY,
        "client_secret": SECRET_KEY,
    }
    resp = requests.post(TOKEN_URL, params=params, timeout=30)
    data = resp.json()
    if "access_token" not in data:
        log(f"获取 token 失败: {data}")
        return None
    log(f"access_token 获取成功 (有效期 {data.get('expires_in', 0)//3600}h)")
    return data["access_token"]


def ensure_remote_dir(access_token, remote_path):
    """确保远程目录存在（递归创建），重名不报错"""
    parts = remote_path.strip("/").split("/")
    current = ""
    for part in parts:
        current = f"{current}/{part}"
        params = {
            "access_token": access_token,
            "path": current,
            "size": 0,
            "isdir": 1,
            "rtype": 1,
        }
        headers = {"User-Agent": "pan.baidu.com"}
        try:
            requests.post(CREATE_URL, params=params, headers=headers, timeout=15)
        except Exception as e:
            log(f"  创建目录 {current} 失败: {e}")


def precreate_file(access_token, path, file_size):
    """预创建文件，获取 uploadid"""
    params = {
        "access_token": access_token,
        "path": path,
        "size": file_size,
        "isdir": 0,
        "rtype": 1,
        "block_list": json.dumps(["a" * 40] * max(1, (file_size + 4194304 - 1) // 4194304)),
        "autoinit": 1,
    }
    headers = {"User-Agent": "pan.baidu.com"}
    resp = requests.post(PRECREATE_URL, params=params, headers=headers, timeout=15)
    return resp.json()


def upload_to_baidu(access_token, local_path, remote_path):
    """完整上传流程: 预创建 → 上传（整文件）→ 创建文件"""
    file_size = os.path.getsize(local_path)
    file_name = os.path.basename(local_path)
    log(f"准备上传: {file_name} ({file_size / 1024 / 1024:.1f}MB)")
    
    # Step 1: 预创建
    log("  预创建文件...")
    precreate = precreate_file(access_token, remote_path, file_size)
    if "uploadid" not in precreate:
        log(f"  预创建失败: {precreate}")
        return False
    
    uploadid = precreate["uploadid"]
    block_list = precreate.get("block_list", [])
    log(f"  预创建成功, 分片数: {len(block_list)}")
    
    # Step 2: 上传整个文件
    log(f"  开始上传...")
    upload_url = "https://d.pcs.baidu.com/rest/2.0/pcs/file"
    params = {
        "method": "upload",
        "access_token": access_token,
        "path": remote_path,
        "uploadid": uploadid,
    }
    headers = {"User-Agent": "pan.baidu.com"}
    
    with open(local_path, "rb") as f:
        files = {"file": (file_name, f)}
        resp = requests.post(upload_url, params=params, headers=headers, files=files, timeout=3600)
    
    log(f"  HTTP状态: {resp.status_code}")
    if not resp.text.strip():
        log(f"  ❌ 空响应")
        return False
    if resp.status_code != 200:
        log(f"  ❌ HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    result = resp.json()
    if "md5" in result:
        log(f"  ✅ 上传完成! MD5: {result['md5']}")
        return True
    else:
        log(f"  ❌ 上传失败: {result}")
        return False


def load_room_meta():
    """从 /tmp/recordings 读取所有 _meta.json，返回 {room_id: room_name}"""
    mapping = {}
    if not os.path.isdir(RECORDING_DIR):
        return mapping
    for f in os.listdir(RECORDING_DIR):
        if f.endswith("_meta.json"):
            try:
                with open(os.path.join(RECORDING_DIR, f), encoding="utf-8") as fp:
                    meta = json.load(fp)
                mapping[meta["room_id"]] = meta.get("room_name", meta["room_id"])
            except:
                pass
    return mapping


def main():
    log(f"扫描本地录制目录: {RECORDING_DIR}")
    
    if not os.path.isdir(RECORDING_DIR):
        log(f"目录不存在: {RECORDING_DIR}")
        return
    
    files = [f for f in os.listdir(RECORDING_DIR) if f.endswith(".mp4")]
    if not files:
        log("没有找到 MP4 文件，跳过上传")
        return
    
    log(f"找到 {len(files)} 个录制文件")
    
    # 读取主播映射
    room_names = load_room_meta()
    if room_names:
        log(f"主播映射: {room_names}")
    
    # 获取 access_token
    access_token = get_access_token()
    if not access_token:
        log("获取 access_token 失败，终止上传")
        sys.exit(1)
    
    # 确保主目录存在
    ensure_remote_dir(access_token, REMOTE_DIR)
    
    # 上传每个文件
    success_count = 0
    for filename in sorted(files):
        local_path = os.path.join(RECORDING_DIR, filename)
        
        # 从文件名提取房间ID，查找主播名
        # 文件名格式: roomId_quality_timestamp.mp4
        room_id = filename.split("_")[0] if "_" in filename else ""
        room_name = room_names.get(room_id, "")
        
        # 构建远程路径: /apps/自动上传/抖音录制/主播名/filename
        if room_name:
            # 去掉非法文件名字符
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", room_name)
            sub_dir = f"{REMOTE_DIR}/{safe_name}"
        else:
            sub_dir = REMOTE_DIR
        
        ensure_remote_dir(access_token, sub_dir)
        remote_path = f"{sub_dir}/{filename}"
        
        log(f"\n{'='*50}")
        log(f"开始上传: {filename}")
        log(f"目标目录: {sub_dir}")
        
        ok = upload_to_baidu(access_token, local_path, remote_path)
        if ok:
            success_count += 1
        else:
            log(f"上传失败 (跳过): {filename}")
    
    log(f"\n{'='*50}")
    log(f"上传完成: {success_count}/{len(files)} 个文件成功")


if __name__ == "__main__":
    main()
