#!/usr/bin/env python3
"""
yt-live.py

Single-file Flask backend that:
- Serves static UI from ./static/
- Provides /api/videos?page=1&page_size=10&channel_url=... (fast pagination)
- Accepts POST /start with form-data: stream_key, selected (multiple) to stream videos synchronously.

Note: This is synchronous (blocking) for simplicity. For production, run background jobs.
"""
import os
import sys
import time
import tempfile
import glob
import shlex
import shutil
import subprocess
import json
import threading
import time as time_mod
from datetime import datetime
from flask import Flask, request, send_from_directory, jsonify, abort

# ----- Config -----
CACHE_FILE = "video_cache.json"
CACHE_REFRESH_INTERVAL = 4 * 60 * 60  # seconds (4 hours)
DEFAULT_STREAM_KEY = os.environ.get("YT_STREAM_KEY", "wbj3-huuv-eta4-xp5g-9944")
DEFAULT_CHANNEL = os.environ.get("CHANNEL_URL", "https://www.youtube.com/@IamCitizenind")
DEFAULT_NUM_LATEST = int(os.environ.get("NUM_LATEST_DEFAULT", "5"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")  # optional
YT_COOKIES = os.environ.get("YT_COOKIES")  # optional cookies file

REENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-maxrate", "1500k", "-bufsize", "3000k",
    "-b:v", "1200k", "-c:a", "aac", "-b:a", "96k", "-ar", "44100"
]
EXTRACTOR_ARGS = ["--extractor-args", "youtube:player_client=default"]

# ----- Flask app (serve static folder) -----
app = Flask(__name__, static_folder="static", static_url_path="")
app.config["JSON_SORT_KEYS"] = False

# ----- Tool detection -----
def find_yt_dlp():
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        return None

YTDLP = find_yt_dlp()
if YTDLP is None:
    print("ERROR: yt-dlp not found. Install with: pip install -U yt-dlp")
    sys.exit(1)
if shutil.which("ffmpeg") is None:
    print("ERROR: ffmpeg not found. Install and add to PATH.")
    sys.exit(1)

# ----- Helpers -----
def run_cmd(cmd, capture=False, timeout=None):
    print("RUN:", " ".join(shlex.quote(x) for x in cmd), flush=True)
    if capture:
        out = subprocess.check_output(cmd, text=True, timeout=timeout)
        return out.strip()
    p = subprocess.Popen(cmd)
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        raise
    return p.returncode

def get_channel_ids_fast(channel_url):
    """Fast list of IDs using yt-dlp --flat-playlist --get-id"""
    cookies = YT_COOKIES
    cmd = YTDLP + (["--cookies", cookies] if cookies else []) + ["--flat-playlist", "--get-id", channel_url] + EXTRACTOR_ARGS
    try:
        out = run_cmd(cmd, capture=True, timeout=20)
        ids = [line.strip() for line in out.splitlines() if line.strip()]
        return ids
    except Exception as e:
        print("[error] get-id failed:", e, flush=True)
        return []

def fetch_metadata_for_ids(ids, max_items=None, time_budget=50):
        # ...existing code...
    """
    Batch-fetch metadata for a list of video ids using a single yt-dlp call
    when possible. This is MUCH faster than calling yt-dlp per-id.

    Returns list of dicts in the same order as `ids`: {id, title, upload_date}.
    If a video's metadata can't be obtained, returns a fallback entry with id-as-title.
    """
    import json, subprocess, time

    cookies = YT_COOKIES
    results = []
    if not ids:
        return results

    ids_to_fetch = ids if max_items is None else ids[:max_items]
    watch_urls = [f"https://www.youtube.com/watch?v={vid}" for vid in ids_to_fetch]
    cmd = YTDLP + (["--cookies", cookies] if cookies else []) + ["--dump-json"] + watch_urls + EXTRACTOR_ARGS
    print("[batch] running yt-dlp for", len(watch_urls), "items", flush=True)
    batch_timeout = max(10, time_budget)
    def parse_meta(meta, vid):
        def get_field(m, key, fallback=None):
            v = m.get(key)
            return v if v is not None else fallback
        title = get_field(meta, "title", vid) or get_field(meta, "fulltitle", vid) or vid
        views = get_field(meta, "view_count", 0)
        upload = get_field(meta, "upload_date", "")
        if upload and len(str(upload)) == 8 and str(upload).isdigit():
            upload = f"{upload[0:4]}-{upload[4:6]}-{upload[6:8]}"
        description = get_field(meta, "description", "")
        thumbnail = get_field(meta, "thumbnail", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
        channel = get_field(meta, "channel", "") or get_field(meta, "channel_id", "")
        channel_url = get_field(meta, "channel_url", "")
        duration = get_field(meta, "duration_string") or get_field(meta, "duration", 0)
        # Determine type: 'short', 'live', or 'video'
        # Use meta['duration'], meta['is_live'], meta['categories'], meta['tags'], meta['webpage_url']
        video_type = "video"
        # Check for live
        if meta.get("is_live") or meta.get("was_live"):
            video_type = "live"
        # Check for short (YouTube Shorts)
        elif (
            ("shorts" in str(meta.get("webpage_url", ""))) or
            (isinstance(duration, (int, float)) and duration and duration <= 65) or
            (isinstance(duration, str) and duration and duration.isdigit() and int(duration) <= 65)
        ):
            video_type = "short"
        return {
            "id": vid,
            "title": title,
            "views": views,
            "duration": duration,
            "upload_date": upload,
            "description": description,
            "thumbnail": thumbnail,
            "channel": channel,
            "channel_url": channel_url,
            "type": video_type
        }

    batch_timeout = max(10, time_budget)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=batch_timeout)
        out = (proc.stdout or "").strip()
        entries = []
        if proc.returncode == 0 and out:
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                    entries.append(j)
                except Exception as e:
                    print("[batch] failed to parse JSON line:", e)
                    print("[batch] offending line:", line)
                    print("[batch] yt-dlp stderr:\n", proc.stderr or '(no stderr)')
                    return []
        else:
            print(f"[batch] yt-dlp returned code {proc.returncode}; stderr:\n{proc.stderr or '(no stderr)'}")
            print(f"[batch] yt-dlp command: {' '.join(shlex.quote(x) for x in cmd)}")
            print(f"[batch] yt-dlp stdout:\n{proc.stdout or '(no stdout)'}")
            return []

        # Check for missing metadata
        required_fields = ["title", "view_count", "duration", "upload_date", "thumbnail", "channel", "channel_url"]
        for idx, vid in enumerate(ids_to_fetch):
            meta = entries[idx] if idx < len(entries) else {}
            missing_fields = [f for f in required_fields if meta.get(f) is None or meta.get(f) == ""]
            if missing_fields:
                print(f"[error] Missing metadata for {vid}, aborting cache update.")
                print(f"[error] Missing fields: {missing_fields}")
                # print(f"[error] Partial metadata: {json.dumps(meta, ensure_ascii=False, indent=2)}")
                reason = meta.get('extractor_error') or meta.get('error') or None
                if reason:
                    print(f"[error] yt-dlp reported: {reason}")
                return []
        return [parse_meta(entries[idx], ids_to_fetch[idx]) for idx in range(len(ids_to_fetch))]
    except subprocess.TimeoutExpired:
        print("[batch] yt-dlp batch call timed out after", batch_timeout, "seconds. Retrying with longer timeout.")
        # Retry once with double timeout
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=batch_timeout*2)
            out = (proc.stdout or "").strip()
            entries = []
            if proc.returncode == 0 and out:
                for line in out.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        j = json.loads(line)
                        entries.append(j)
                    except Exception as e:
                        print("[batch] failed to parse JSON line:", e)
                        print("[batch] offending line:", line)
                        print("[batch] yt-dlp stderr:\n", proc.stderr or '(no stderr)')
                        return []
            else:
                print(f"[batch] yt-dlp returned code {proc.returncode}; stderr:\n{proc.stderr or '(no stderr)'}")
                print(f"[batch] yt-dlp command: {' '.join(shlex.quote(x) for x in cmd)}")
                print(f"[batch] yt-dlp stdout:\n{proc.stdout or '(no stdout)'}")
                return []
            required_fields = ["title", "view_count", "duration", "upload_date", "description", "thumbnail", "channel", "channel_url"]
            for idx, vid in enumerate(ids_to_fetch):
                meta = entries[idx] if idx < len(entries) else {}
                missing = any((meta.get(f) is None or meta.get(f) == "") for f in required_fields)
                if missing:
                    print(f"[error] Missing metadata for {vid} after retry, aborting cache update.")
                    return []
            return [parse_meta(entries[idx], ids_to_fetch[idx]) for idx in range(len(ids_to_fetch))]
        except Exception as e:
            print("[batch] yt-dlp batch call failed after retry:", e)
            return []
    except Exception as e:
        print("[batch] unexpected error while running yt-dlp batch:", e)
        return []

def download_video_to_temp(video_id, tmp_dir):
    url = f"https://www.youtube.com/watch?v={video_id}"
    dest = os.path.join(tmp_dir, f"{video_id}.%(ext)s")
    cmd = YTDLP + ["-o", dest, url] + EXTRACTOR_ARGS
    if YT_COOKIES:
        cmd = YTDLP + ["--cookies", YT_COOKIES, "-o", dest, url] + EXTRACTOR_ARGS
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        print(f"[error] download failed for {video_id}: {e}", flush=True)
        return None
    pattern = dest.replace("%(ext)s", "*")
    files = glob.glob(pattern)
    files = [f for f in files if os.path.basename(f).startswith(video_id)]
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]

def ffmpeg_stream_local(file_path, stream_key, reencode=False):
    rtmp = f"rtmps://a.rtmps.youtube.com/live2/{stream_key}"
    args = ["ffmpeg", "-re", "-i", file_path]
    if not reencode:
        args += ["-c", "copy", "-f", "flv", rtmp]
    else:
        args += REENCODE_ARGS + ["-f", "flv", rtmp]
    print("Starting ffmpeg:", " ".join(shlex.quote(x) for x in args), flush=True)
    p = subprocess.Popen(args)
    p.wait()
    return p.returncode

def stream_selected_ids(selected_ids, stream_key):
    if not stream_key:
        return False, "Stream key missing"
    tmp_dir = os.path.join(tempfile.gettempdir(), f"ytlive_{os.getpid()}")
    os.makedirs(tmp_dir, exist_ok=True)
    for vid in selected_ids:
        if not vid or vid.startswith("UC"):
            continue
        local = download_video_to_temp(vid, tmp_dir)
        if not local:
            print(f"[warn] skipping download failed: {vid}", flush=True)
            continue
        rc = ffmpeg_stream_local(local, stream_key, reencode=False)
        if rc != 0:
            print("[info] copy failed, trying re-encode", flush=True)
            rc2 = ffmpeg_stream_local(local, stream_key, reencode=True)
            if rc2 != 0:
                print(f"[error] streaming failed for {vid}", flush=True)
        try:
            os.remove(local)
        except:
            pass
        time.sleep(1)
    try:
        if not os.listdir(tmp_dir):
            os.rmdir(tmp_dir)
    except:
        pass
    return True, "Streaming completed"

# ----- Routes -----
def load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ids": [], "videos": []}

def save_cache(data):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[cache] Failed to save cache: {e}", flush=True)

def refresh_cache(channel_url=DEFAULT_CHANNEL):
    print(f"[cache] Refreshing cache for channel: {channel_url}", flush=True)
    ids = get_channel_ids_fast(channel_url)
    cache = load_cache()
    cached_videos = {v.get("id"): v for v in cache.get("videos", [])}
    # Only fetch metadata for new IDs not present in cache
    new_ids = [vid for vid in ids if vid not in cached_videos]
    all_ids = ids
    videos = []
    # Fetch metadata for new videos only
    # Build the updated videos list: keep old metadata for existing IDs, add new metadata for new IDs
    videos = []
    for vid in all_ids:
        if vid in cached_videos:
            videos.append(cached_videos[vid])
        else:
            # Fetch metadata for this vid one by one
            meta_list = fetch_metadata_for_ids([vid], max_items=1, time_budget=20)
            if meta_list and len(meta_list) == 1:
                meta = meta_list[0]
                videos.append(meta)
                # Save cache after each new video is fetched
                save_cache({"ids": all_ids, "videos": videos})
                print(f"[cache] Added video {vid} to cache.", flush=True)
    # Final save to ensure all videos are present
    save_cache({"ids": all_ids, "videos": videos})
    print(f"[cache] Cache updated: {len(videos)} videos.", flush=True)

def cache_refresher_thread():
    while True:
        try:
            refresh_cache()
        except Exception as e:
            print(f"[cache] Error during refresh: {e}", flush=True)
        time_mod.sleep(CACHE_REFRESH_INTERVAL)

def start_cache_thread():
    # Start background cache refresher thread (including initial refresh)
    t = threading.Thread(target=cache_refresher_thread, daemon=True)
    t.start()
    print("[cache] Background cache refresher started.", flush=True)

start_cache_thread()
@app.route("/", methods=["GET"])
def serve_index():
    return send_from_directory("static", "index.html")

@app.route("/api/videos", methods=["GET"])
def api_videos():
    """
    Query params:
      channel_url (optional)
      page (1-based, default 1)
      page_size (default 10)
    """
    channel_url = request.args.get("channel_url") or DEFAULT_CHANNEL
    try:
        page = max(1, int(request.args.get("page", "1")))
    except:
        page = 1
    try:
        page_size = max(1, int(request.args.get("page_size", "20")))
    except:
        page_size = 20

    cache = load_cache()
    ids = cache.get("ids", [])
    videos = cache.get("videos", [])
    total = len(ids)
    if total == 0:
        print("[api_videos] No video IDs found in cache.", flush=True)
        return jsonify({"total_count": 0, "page": page, "page_size": page_size, "videos": []})

    start = (page - 1) * page_size
    end = start + page_size
    # For compatibility, if frontend expects reduced fields, map them here
    def reduce_meta(meta):
        vid = meta.get("id")
        thumb = meta.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        return {
            "id": vid,
            "title": meta.get("title") or vid,
            "views": meta.get("view_count"),
            "duration": meta.get("duration_string") or meta.get("duration"),
            "upload_date": meta.get("upload_date") or "",
            "description": meta.get("description") or "",
            "thumbnail": thumb,
            "channel": meta.get("channel"),
            "channel_url": meta.get("channel_url")
        }
    videos_slice = videos[start:end]
    print(f"[api_videos] Returning {len(videos_slice)} videos to client from cache.", flush=True)
    # If you want to return full metadata, use: return jsonify({"total_count": total, "page": page, "page_size": page_size, "videos": videos_slice})
    # If you want to return reduced fields, use:
    return jsonify({"total_count": total, "page": page, "page_size": page_size, "videos": [reduce_meta(v) for v in videos_slice]})

@app.route("/start", methods=["POST"])
def start_route():
    # Accept stream_key and selected ids (form-data or json)
    # Optional: password verification
    password = request.form.get("password") or request.json.get("password") if request.is_json else None
    if ADMIN_PASSWORD:
        if not password or password != ADMIN_PASSWORD:
            return jsonify({"ok": False, "error": "Invalid admin password"}), 403

    # stream_key
    stream_key = request.form.get("stream_key") or (request.json.get("stream_key") if request.is_json else None) or DEFAULT_STREAM_KEY
    if not stream_key:
        return jsonify({"ok": False, "error": "Missing stream_key"}), 400

    # selected IDs: form multiple 'selected' fields or JSON list
    selected = request.form.getlist("selected")
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if not selected:
            if isinstance(body.get("selected"), list):
                selected = body.get("selected")
    if not selected:
        return jsonify({"ok": False, "error": "No videos selected"}), 400

    # Run streaming (synchronous)
    try:
        success, msg = stream_selected_ids(selected, stream_key)
        if success:
            return jsonify({"ok": True, "message": msg})
        else:
            return jsonify({"ok": False, "error": msg}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# Serve other static files (CSS/JS)
@app.route("/<path:path>")
def static_proxy(path):
    # This will serve files under ./static
    return send_from_directory("static", path)

# ----- Run server -----
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)


