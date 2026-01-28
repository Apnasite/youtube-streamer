#!/usr/bin/env python3
"""
yt-live-web.py (optimized for quick start)

Key optimizations:
- Use yt-dlp --get-id to quickly obtain video IDs (fast).
- Only fetch per-video metadata when needed, and then only a small subset or within a short time budget.
- For "num_latest" auto-start: select N IDs from --get-id and start streaming immediately (no per-id metadata).
- For date-range auto-start: fetch metadata only until we find matching videos or time budget expires.
- For manual selection UI: fetch metadata for first M ids or until time budget expires.

This minimizes blocking so streaming can start within ~60s.
"""
import os
import sys
import time
import tempfile
import glob
import shlex
import shutil
import subprocess
from datetime import datetime
from flask import Flask, request, render_template_string, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me-for-prod")

# ==== Config (defaults) ====
DEFAULT_STREAM_KEY = os.environ.get("YT_STREAM_KEY", "wbj3-huuv-eta4-xp5g-9944")
DEFAULT_CHANNEL = os.environ.get("CHANNEL_URL", "https://www.youtube.com/@IamCitizenind")
DEFAULT_NUM_LATEST = os.environ.get("NUM_LATEST_DEFAULT", "5")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")  # optional

# tools detection
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
    print("ERROR: ffmpeg not found. Install and make available in PATH.")
    sys.exit(1)

REENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-maxrate", "1500k", "-bufsize", "3000k",
    "-b:v", "1200k", "-c:a", "aac", "-b:a", "96k", "-ar", "44100"
]
EXTRACTOR_ARGS = ["--extractor-args", "youtube:player_client=default"]

# ==== HTML template ====
BASE_TEMPLATE = """
<!doctype html>
<title>YT Live Streamer (Optimized)</title>
<h2>YT Live Streamer</h2>

{% with msgs = get_flashed_messages() %}
  {% if msgs %}
    <ul style="color: red;">
    {% for m in msgs %}
      <li>{{ m }}</li>
    {% endfor %}
    </ul>
  {% endif %}
{% endwith %}

<form method="post" action="{{ url_for('list_videos') }}">
  <label>Admin password: <input name="password" type="password"></label><br><br>

  <label>Channel/Handle URL:
    <input name="channel_url" size=60 value="{{ channel_url }}">
  </label><br><br>

  <label>Number of latest videos (fast auto-start):
    <input name="num_latest" type="number" min="1" value="{{ num_latest }}">
  </label><br><br>

  <label>OR Date range (slower, may take seconds):
    From <input name="date_from" placeholder="2025-01-01"> To <input name="date_to" placeholder="2025-06-30">
  </label><br><br>

  <label>Stream key:
    <input name="stream_key" size=64 value="{{ stream_key }}">
  </label><br><br>

  <button type="submit">Fetch / Start (auto-start if num/date provided)</button>
</form>

{% if videos %}
<hr>
<h3>Videos (quick metadata)</h3>
<form method="post" action="{{ url_for('start_stream') }}">
  <input type="hidden" name="channel_url" value="{{ channel_url }}">
  <input type="hidden" name="password" value="{{ password }}">
  <input type="hidden" name="stream_key" value="{{ stream_key }}">
  <ul>
  {% for v in videos %}
    <li>
      <label>
        <input type="checkbox" name="selected" value="{{ v.id }}" {% if loop.index0 < default_checked %}checked{% endif %}>
        <strong>{{ v.title }}</strong> — uploaded: {{ v.upload_date }} — id: {{ v.id }}
      </label>
    </li>
  {% endfor %}
  </ul>
  <button type="submit">Start streaming selected videos</button>
</form>
{% endif %}

<hr>
<p>Notes: num_latest uses fast id-only listing and will start streaming immediately. Date-range requires metadata and may take a few seconds.</p>
"""

# ==== Utility helpers ====
def run_cmd(cmd, capture=False, timeout=None):
    print("RUN:", " ".join(shlex.quote(x) for x in cmd))
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

# Fast id-only listing
def get_channel_ids_fast(channel_url):
    """
    Return list of video ids quickly using --flat-playlist --get-id.
    """
    cookies = os.environ.get("YT_COOKIES")
    cmd = YTDLP + (["--cookies", cookies] if cookies else []) + ["--flat-playlist", "--get-id", channel_url] + EXTRACTOR_ARGS
    try:
        out = run_cmd(cmd, capture=True, timeout=20)
        ids = [line.strip() for line in out.splitlines() if line.strip()]
        print(f"[fast] got {len(ids)} ids")
        return ids
    except Exception as e:
        print("[fast] get-id failed:", e)
        return []

# Fetch metadata for IDs but stop early when time_budget or max_items reached
def fetch_metadata_for_ids(ids, max_items=None, time_budget=10):
    """
    ids: list of video ids (ordered)
    max_items: maximum metadata items to return (None = no limit)
    time_budget: seconds total budget for this function
    Returns list of dicts {id, title, upload_date}
    """
    cookies = os.environ.get("YT_COOKIES")
    results = []
    start = time.time()
    count = 0
    for vid in ids:
        if max_items and len(results) >= max_items:
            break
        if time.time() - start > time_budget:
            print("Metadata fetch time budget exhausted")
            break
        watch = f"https://www.youtube.com/watch?v={vid}"
        cmd = YTDLP + (["--cookies", cookies] if cookies else []) + ["--dump-json", watch] + EXTRACTOR_ARGS
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
            if proc.returncode != 0:
                print(f"Warning: metadata fetch failed for {vid}: returncode {proc.returncode}")
                # include basic id-only entry
                results.append({"id": vid, "title": vid, "upload_date": ""})
            else:
                meta = proc.stdout.strip()
                if not meta:
                    results.append({"id": vid, "title": vid, "upload_date": ""})
                else:
                    import json
                    try:
                        j = json.loads(meta)
                        title = j.get("title") or j.get("fulltitle") or vid
                        upload = j.get("upload_date") or ""
                        if upload and len(upload) == 8 and upload.isdigit():
                            upload = f"{upload[0:4]}-{upload[4:6]}-{upload[6:8]}"
                        results.append({"id": vid, "title": title, "upload_date": upload})
                    except Exception as e:
                        print(f"Warning: failed to parse meta for {vid}: {e}")
                        results.append({"id": vid, "title": vid, "upload_date": ""})
        except subprocess.TimeoutExpired:
            print(f"Timeout fetching metadata for {vid}")
            results.append({"id": vid, "title": vid, "upload_date": ""})
        except Exception as e:
            print(f"Error fetching metadata for {vid}: {e}")
            results.append({"id": vid, "title": vid, "upload_date": ""})
        count += 1
    print(f"Metadata fetched for {len(results)} items (processed {count} ids) in {time.time()-start:.1f}s")
    return results

# Download and stream functions (unchanged)
def download_video_to_temp(video_id, tmp_dir):
    url = f"https://www.youtube.com/watch?v={video_id}"
    dest = os.path.join(tmp_dir, f"{video_id}.%(ext)s")
    cmd = YTDLP + ["-o", dest, url] + EXTRACTOR_ARGS
    cookies = os.environ.get("YT_COOKIES")
    if cookies:
        cmd = YTDLP + ["--cookies", cookies, "-o", dest, url] + EXTRACTOR_ARGS
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        print("Download failed for", video_id, e)
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
    print("Starting ffmpeg:", " ".join(shlex.quote(x) for x in args))
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
            print("Skipping video (download failed):", vid)
            continue
        rc = ffmpeg_stream_local(local, stream_key, reencode=False)
        if rc != 0:
            print("Copy mode failed. Retry with re-encode.")
            rc2 = ffmpeg_stream_local(local, stream_key, reencode=True)
            if rc2 != 0:
                print("Streaming failed for", vid)
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

# ==== Flask routes (optimized) ====
@app.route("/", methods=["GET"])
def index():
    return render_template_string(
        BASE_TEMPLATE,
        channel_url=DEFAULT_CHANNEL,
        stream_key=DEFAULT_STREAM_KEY,
        videos=None,
        password="",
        num_latest=DEFAULT_NUM_LATEST
    )

@app.route("/list", methods=["POST"])
def list_videos():
    password = request.form.get("password", "")
    if ADMIN_PASSWORD:
        if not password or password != ADMIN_PASSWORD:
            flash("Invalid admin password.")
            return redirect(url_for("index"))

    channel_url = request.form.get("channel_url") or DEFAULT_CHANNEL
    num_latest = (request.form.get("num_latest") or "").strip()
    date_from = request.form.get("date_from") or ""
    date_to = request.form.get("date_to") or ""
    stream_key = request.form.get("stream_key") or DEFAULT_STREAM_KEY

    will_auto_start = bool(num_latest or date_from or date_to)
    if will_auto_start and not stream_key:
        flash("Stream key required for auto-start. Paste it in the form or set DEFAULT_STREAM_KEY on server.")
        return redirect(url_for("index"))

    # FAST path: obtain ids quickly
    ids = get_channel_ids_fast(channel_url)
    if not ids:
        flash("No ids found (yt-dlp failed).")
        return redirect(url_for("index"))

    # If user requested num_latest -> pick first N ids and start streaming immediately (no per-id metadata)
    if num_latest:
        try:
            n = int(num_latest)
        except:
            flash("Invalid number for latest videos")
            return redirect(url_for("index"))
        selected_ids = ids[:n]
        print(f"Auto-starting stream for latest {len(selected_ids)} videos (ids-only, fast).")
        success, msg = stream_selected_ids(selected_ids, stream_key)
        if success:
            return f"Streaming completed for {len(selected_ids)} videos (fast path)."
        else:
            flash(msg)
            return redirect(url_for("index"))

    # If date filters provided -> fetch metadata only until we collect matches or time budget reached
    df = None; dt = None
    try:
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d").date()
        if date_to:
            dt = datetime.strptime(date_to, "%Y-%m-%d").date()
    except Exception:
        flash("Invalid date format; use YYYY-MM-DD")
        return redirect(url_for("index"))

    if df or dt:
        # fetch metadata scanning ids until we gather all matches or time budget
        time_budget = 20  # seconds total to spend fetching metadata for date filter
        print(f"Date filter requested, fetching metadata with {time_budget}s budget...")
        metas = fetch_metadata_for_ids(ids, max_items=None, time_budget=time_budget)
        # filter by date from metas
        filtered = []
        for m in metas:
            ud = m.get("upload_date") or ""
            if not ud:
                continue
            try:
                d = datetime.strptime(ud, "%Y-%m-%d").date()
                if df and d < df:
                    continue
                if dt and d > dt:
                    continue
                filtered.append(m)
            except:
                continue
        if not filtered:
            flash("No videos matched date range (within metadata fetch budget). Try a wider range or increase budget.")
            return redirect(url_for("index"))
        selected_ids = [m["id"] for m in filtered]
        print(f"Auto-starting stream for {len(selected_ids)} videos (date-filtered, limited metadata).")
        success, msg = stream_selected_ids(selected_ids, stream_key)
        if success:
            return f"Streaming completed for {len(selected_ids)} videos (date filtered)."
        else:
            flash(msg)
            return redirect(url_for("index"))

    # Otherwise manual selection: fetch metadata for first M ids quickly for UI
    ui_time_budget = 8  # seconds
    max_ui_items = 50
    print(f"Preparing UI: fetching metadata for up to {max_ui_items} ids within {ui_time_budget}s...")
    metas = fetch_metadata_for_ids(ids[:200], max_items=max_ui_items, time_budget=ui_time_budget)  # limit ids scanned
    default_checked = min(5, len(metas))
    return render_template_string(
        BASE_TEMPLATE,
        channel_url=channel_url,
        stream_key=stream_key,
        videos=metas,
        default_checked=default_checked,
        password=password,
        num_latest=DEFAULT_NUM_LATEST
    )

@app.route("/start", methods=["POST"])
def start_stream():
    password = request.form.get("password", "")
    if ADMIN_PASSWORD:
        if not password or password != ADMIN_PASSWORD:
            return "Invalid admin password.", 403

    stream_key = request.form.get("stream_key", "").strip()
    if not stream_key:
        return "Stream key required (paste it into the form).", 400

    selected = request.form.getlist("selected")
    if not selected:
        return "No videos selected.", 400

    success, msg = stream_selected_ids(selected, stream_key)
    if success:
        return "Streaming sequence finished. All selected videos processed."
    else:
        return f"Streaming failed: {msg}", 500

# ==== Run app ====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
