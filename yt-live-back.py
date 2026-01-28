#!/usr/bin/env python3
import os
import sys
import subprocess
import shlex
import time
import shutil
import tempfile
import glob

# ------------------ CONFIG ------------------
STREAM_KEY = os.environ.get('YT_STREAM_KEY', 'wbj3-huuv-eta4-xp5g-9944')
CHANNEL_URL = os.environ.get('CHANNEL_URL', 'https://youtube.com/@IamCitizenind')
YT_COOKIES = os.environ.get('YT_COOKIES')  # optional cookies.txt

RTMP_URL = f"rtmps://a.rtmps.youtube.com/live2/{STREAM_KEY}"

# Fallback re-encode settings (kept from your old code)
REENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast",
    "-maxrate", "1500k", "-bufsize", "3000k",
    "-b:v", "1200k",
    "-c:a", "aac", "-b:a", "96k", "-ar", "44100"
]

EXTRACTOR_ARGS = ["--extractor-args", "youtube:player_client=default"]


# ------------------ HELPERS ------------------
def find_yt_dlp():
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return [sys.executable, "-m", "yt_dlp"]
    except:
        return None


YTDLP = find_yt_dlp()
if YTDLP is None:
    print("ERROR: yt-dlp not found. Install with: pip install yt-dlp")
    sys.exit(1)

if shutil.which("ffmpeg") is None:
    print("ERROR: ffmpeg not installed or not in PATH.")
    sys.exit(1)


def run_cmd(cmd, capture=False):
    print("RUN:", " ".join(shlex.quote(x) for x in cmd))
    if capture:
        return subprocess.check_output(cmd, text=True).strip()
    p = subprocess.Popen(cmd)
    p.wait()
    return p.returncode


# ------------------ LOGIC ------------------
def get_video_ids(channel_url):
    cmd = YTDLP + ["--flat-playlist", "--get-id", channel_url] + EXTRACTOR_ARGS
    if YT_COOKIES:
        cmd += ["--cookies", YT_COOKIES]

    try:
        out = run_cmd(cmd, capture=True)
        ids = [line.strip() for line in out.splitlines() if line.strip()]
        return ids
    except Exception as e:
        print("Error fetching video IDs:", e)
        return []


def download_video(video_id, tmp_dir):
    url = f"https://www.youtube.com/watch?v={video_id}"
    dest = os.path.join(tmp_dir, f"{video_id}.%(ext)s")

    cmd = YTDLP + ["-o", dest, url] + EXTRACTOR_ARGS
    if YT_COOKIES:
        cmd += ["--cookies", YT_COOKIES]

    try:
        subprocess.check_call(cmd)
    except Exception as e:
        print("Download failed:", e)
        return None

    # find resulting file
    pattern = dest.replace("%(ext)s", "*")
    files = glob.glob(pattern)
    if not files:
        return None

    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def ffmpeg_stream(path, reencode=False):
    cmd = ["ffmpeg", "-re", "-i", path]

    if not reencode:
        cmd += ["-c", "copy", "-f", "flv", RTMP_URL]
    else:
        cmd += REENCODE_ARGS + ["-f", "flv", RTMP_URL]

    p = subprocess.Popen(cmd)
    p.wait()
    return p.returncode


def process_video(video_id, tmp_dir):
    if not video_id or video_id.startswith("UC"):
        print("Skipping invalid ID:", video_id)
        return

    local_path = download_video(video_id, tmp_dir)
    if not local_path:
        print("Could not download:", video_id)
        return

    print("Streaming:", video_id)

    rc = ffmpeg_stream(local_path, reencode=False)
    if rc != 0:
        print("Copy mode failed. Trying re-encode...")
        ffmpeg_stream(local_path, reencode=True)

    try:
        os.remove(local_path)
    except:
        pass


# ------------------ MAIN ------------------
def main():
    tmp_dir = os.path.join(tempfile.gettempdir(), f"ytlive_{os.getpid()}")
    os.makedirs(tmp_dir, exist_ok=True)
    print("Temp folder:", tmp_dir)

    ids = get_video_ids(CHANNEL_URL)
    if not ids:
        print("No videos found.")
        return

    print(f"Found {len(ids)} videos")

    for vid in ids:
        try:
            process_video(vid, tmp_dir)
            time.sleep(2)
        except KeyboardInterrupt:
            print("Stopped by user.")
            break
        except Exception as e:
            print("Error processing video:", e)

    print("Done.")


if __name__ == "__main__":
    main()
