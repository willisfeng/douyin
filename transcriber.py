#!/usr/bin/env python3
"""音频转写 - 使用 SenseVoice (funasr) 将录制音频转为文字"""
import os, sys, json, glob, re, subprocess, time
from datetime import datetime

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp/recordings")
TRANSCRIBE_DIR = os.environ.get("TRANSCRIBE_DIR", "/tmp/transcripts")
GH_REPO = os.environ.get("GH_REPO", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def download_release_audio():
    """从GitHub Release下载未转写的音频文件"""
    if not GH_REPO or not GH_TOKEN:
        log("无GitHub配置，扫描本地文件")
        return glob.glob(os.path.join(OUTPUT_DIR, "*.wav")) if os.path.exists(OUTPUT_DIR) else []
    
    wavs = []
    try:
        import subprocess
        result = subprocess.run(
            ["gh", "release", "list", "--repo", GH_REPO, "--limit", "10", "--json", "tagName,isDraft"],
            capture_output=True, text=True, timeout=30, env={**os.environ, "GITHUB_TOKEN": GH_TOKEN}
        )
        if result.returncode != 0:
            log(f"获取Release列表失败: {result.stderr[:200]}")
            return []
        releases = json.loads(result.stdout)
        for rel in releases:
            tag = rel["tagName"]
            assets_result = subprocess.run(
                ["gh", "release", "view", tag, "--repo", GH_REPO, "--json", "assets", "--jq", '.assets[] | select(.name|endswith(".wav")) | .name'],
                capture_output=True, text=True, timeout=30, env={**os.environ, "GITHUB_TOKEN": GH_TOKEN}
            )
            if assets_result.returncode == 0 and assets_result.stdout.strip():
                for name in assets_result.stdout.strip().split("\n"):
                    name = name.strip()
                    if not name: continue
                    local = os.path.join(TRANSCRIBE_DIR, name)
                    if os.path.exists(local):
                        log(f"跳过已下载: {name}")
                        continue
                    os.makedirs(TRANSCRIBE_DIR, exist_ok=True)
                    dl = subprocess.run(["gh","release","download",tag,"--repo",GH_REPO,"--pattern",name,"--dir",TRANSCRIBE_DIR,"--clobber"],
                                       capture_output=True, text=True, timeout=120, env={**os.environ, "GITHUB_TOKEN": GH_TOKEN})
                    if dl.returncode == 0:
                        log(f"下载: {name}")
                        wavs.append(local)
                    else:
                        log(f"下载失败 {name}: {dl.stderr[:200]}")
        # 也扫描本地未处理的
        for w in glob.glob(os.path.join(OUTPUT_DIR, "*.wav")):
            if w not in wavs:
                wavs.append(w)
    except Exception as e:
        log(f"下载异常: {e}")
    return wavs

def transcribe(wav_path, model=None):
    """用 SenseVoice 转写音频文件"""
    if model is None:
        from funasr import AutoModel
        log(f"加载 SenseVoice 模型...")
        model = AutoModel(
            model="iic/SenseVoiceSmall",
            disable_update=True,
            device="cpu",
        )
    
    base = os.path.splitext(os.path.basename(wav_path))[0]
    log(f"转写: {os.path.basename(wav_path)}")
    start = time.time()
    
    result = model.generate(input=wav_path, language="zh", use_itn=True)
    
    duration = time.time() - start
    log(f"转写完成: {os.path.basename(wav_path)} ({duration:.0f}s)")
    
    # 解析结果
    txt_lines = []
    srt_lines = []
    idx = 1
    for seg in result:
        # 兼容 dict 和 object 两种返回格式
        if isinstance(seg, dict):
            text = seg.get("text", "") or ""
            ts = seg.get("timestamp", None)
        else:
            if not hasattr(seg, "text") or not seg.text:
                continue
            text = seg.text
            ts = getattr(seg, "timestamp", None)
        
        if not text:
            continue
        
        if ts and isinstance(ts, (list, tuple)) and len(ts) == 2:
            start_s, end_s = ts[0], ts[1]
            txt_lines.append(f"[{format_time(start_s)} -> {format_time(end_s)}] {text}")
            srt_lines.extend(make_srt(idx, start_s, end_s, text))
            idx += 1
        else:
            txt_lines.append(text)
    
    os.makedirs(TRANSCRIBE_DIR, exist_ok=True)
    txt_path = os.path.join(TRANSCRIBE_DIR, f"{base}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt_lines))
    
    srt_path = os.path.join(TRANSCRIBE_DIR, f"{base}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))
    
    log(f"输出: {txt_path} ({len(txt_lines)}行)")
    return txt_path, srt_path

def format_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def make_srt(idx, start_s, end_s, text):
    def to_srt_ts(sec):
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec - int(sec)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return [str(idx), f"{to_srt_ts(start_s)} --> {to_srt_ts(end_s)}", text, ""]

def upload_result(filepath):
    """上传转写结果到 Release"""
    if not filepath or not os.path.exists(filepath) or not GH_REPO or not GH_TOKEN:
        return
    release_tag = f"trans-{datetime.now().strftime('%Y%m%d')}"
    subprocess.run(["gh","release","create",release_tag,"--repo",GH_REPO,"--title",f"转写 {datetime.now().strftime('%Y-%m-%d')}","--notes","SenseVoice自动转写","--target","main"],
                  capture_output=True, timeout=30, env={**os.environ, "GITHUB_TOKEN": GH_TOKEN})
    r = subprocess.run(["gh","release","upload",release_tag,filepath,"--repo",GH_REPO,"--clobber"],
                      capture_output=True, text=True, timeout=120, env={**os.environ, "GITHUB_TOKEN": GH_TOKEN})
    if r.returncode == 0:
        log(f"转写结果已上传: {os.path.basename(filepath)}")
    else:
        log(f"上传失败: {r.stderr[:200]}")

def main():
    os.makedirs(TRANSCRIBE_DIR, exist_ok=True)
    log("=== SenseVoice 音频转写 ===")
    
    wavs = download_release_audio()
    if not wavs:
        log("没有找到未转写的音频文件")
        return
    
    # 转写所有WAV
    uploaded = []
    for wav in wavs:
        base = os.path.splitext(os.path.basename(wav))[0]
        # 检查是否已经转写过
        txt_path = os.path.join(TRANSCRIBE_DIR, f"{base}.txt")
        if os.path.exists(txt_path):
            log(f"跳过已转写: {os.path.basename(wav)}")
            continue
        try:
            txt, srt = transcribe(wav)
            uploaded.append(txt)
            uploaded.append(srt)
        except Exception as e:
            log(f"转写失败 {wav}: {e}")
            import traceback; traceback.print_exc()
    
    # 上传结果
    for f in uploaded:
        upload_result(f)
    
    log(f"=== 转写完成，处理了 {len([x for x in wavs if not os.path.exists(os.path.join(TRANSCRIBE_DIR, os.path.splitext(os.path.basename(x))[0]+'.txt'))])} 个文件 ===")

if __name__ == "__main__":
    main()
