import os
import json
import subprocess
import shutil
from PIL import Image
import imagehash
from tqdm import tqdm

# =============================
# CONFIG
# =============================

FOLDER_PATH = r"G:\XWF"
HASH_DB_FILE = os.path.join(FOLDER_PATH, "content_hash_db.json")

RUSAK_FOLDER = os.path.join(FOLDER_PATH, "_RUSAK")
DUP_FOLDER = os.path.join(FOLDER_PATH, "_DUPLIKAT")

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".wmv")

THRESHOLD = 18
DURATION_TOLERANCE = 2

os.makedirs(RUSAK_FOLDER, exist_ok=True)
os.makedirs(DUP_FOLDER, exist_ok=True)

# =============================
# LOAD DATABASE (SAFE LOAD)
# =============================

if os.path.exists(HASH_DB_FILE):
    try:
        with open(HASH_DB_FILE, "r") as f:
            hash_db = json.load(f)
    except:
        hash_db = {}
else:
    hash_db = {}

# =============================
# GET ALL VIDEOS
# =============================

all_videos = []

for root, _, files in os.walk(FOLDER_PATH):
    if root.startswith(RUSAK_FOLDER) or root.startswith(DUP_FOLDER):
        continue

    for file in files:
        if file.lower().endswith(VIDEO_EXT):
            full_path = os.path.join(root, file)
            if full_path not in hash_db:
                all_videos.append(full_path)

# =============================
# GET DURATION
# =============================

def get_duration(video_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return None

    try:
        return float(result.stdout.strip())
    except:
        return None

# =============================
# VIDEO INFO
# =============================

def get_video_info(video_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,bit_rate",
        "-show_entries",
        "format=duration,bit_rate",
        "-of", "json",
        video_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)

        if "streams" not in data or len(data["streams"]) == 0:
            return None

        stream = data["streams"][0]
        format_data = data["format"]

        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))
        resolution = width * height

        bitrate = int(stream.get("bit_rate") or format_data.get("bit_rate") or 0)
        duration = float(format_data.get("duration", 0))
        filesize = os.path.getsize(video_path)

        return {
            "resolution": resolution,
            "bitrate": bitrate,
            "duration": duration,
            "filesize": filesize
        }

    except:
        return None

# =============================
# EXTRACT FRAME
# =============================

def extract_frame(video_path, timestamp, output_image):
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        output_image
    ]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# =============================
# HASH GENERATION
# =============================

def get_video_hash(video_path, duration):

    if duration is None or duration < 5:
        return None

    if duration < 120:
        frame_points = 3
    elif duration < 1200:
        frame_points = 5
    else:
        frame_points = 9

    timestamps = [
        duration * ((i + 1) / (frame_points + 1))
        for i in range(frame_points)
    ]

    hashes = []

    for i, ts in enumerate(timestamps):
        temp_img = f"temp_{os.getpid()}_{i}.jpg"

        extract_frame(video_path, ts, temp_img)

        if not os.path.exists(temp_img):
            return None

        try:
            img = Image.open(temp_img)
            ph = imagehash.phash(img)
            hashes.append(str(ph))
            img.close()
            os.remove(temp_img)
        except:
            return None

    return "|".join(hashes)

# =============================
# SIMILARITY CHECK
# =============================

def is_similar(hash1, hash2, dur1, dur2):

    if abs(dur1 - dur2) > DURATION_TOLERANCE:
        return False

    h1_list = hash1.split("|")
    h2_list = hash2.split("|")

    if len(h1_list) != len(h2_list):
        return False

    total_diff = 0

    for h1, h2 in zip(h1_list, h2_list):
        total_diff += imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)

    return total_diff <= THRESHOLD

# =============================
# QUALITY COMPARISON
# =============================

def is_better_quality(info1, info2):

    if info2 is None:
        return True

    if info1["resolution"] != info2["resolution"]:
        return info1["resolution"] > info2["resolution"]

    if info1["bitrate"] != info2["bitrate"]:
        return info1["bitrate"] > info2["bitrate"]

    return info1["filesize"] > info2["filesize"]

# =============================
# SAFE MOVE
# =============================

def safe_move(src, dest_folder):

    if not os.path.exists(src):
        return

    filename = os.path.basename(src)
    dest = os.path.join(dest_folder, filename)

    counter = 1
    while os.path.exists(dest):
        name, ext = os.path.splitext(filename)
        dest = os.path.join(dest_folder, f"{name}_{counter}{ext}")
        counter += 1

    shutil.move(src, dest)

# =============================
# MAIN SCAN
# =============================

duplicates = 0
rusak = 0
new_files = 0

for full_path in tqdm(all_videos, desc="Scanning Videos", unit="video"):

    duration = get_duration(full_path)
    if duration is None:
        safe_move(full_path, RUSAK_FOLDER)
        rusak += 1
        continue

    video_hash = get_video_hash(full_path, duration)
    if video_hash is None:
        safe_move(full_path, RUSAK_FOLDER)
        rusak += 1
        continue

    video_info = get_video_info(full_path)
    if video_info is None:
        safe_move(full_path, RUSAK_FOLDER)
        rusak += 1
        continue

    found_duplicate = False

    for existing_path, existing_data in list(hash_db.items()):

        if "hash" not in existing_data:
            continue

        if is_similar(
            video_hash,
            existing_data["hash"],
            duration,
            existing_data.get("duration", 0)
        ):

            existing_info = existing_data.get("info")

            if is_better_quality(video_info, existing_info):

                safe_move(existing_path, DUP_FOLDER)
                hash_db.pop(existing_path, None)

                hash_db[full_path] = {
                    "hash": video_hash,
                    "duration": duration,
                    "info": video_info
                }

            else:
                safe_move(full_path, DUP_FOLDER)

            duplicates += 1
            found_duplicate = True
            break

    if not found_duplicate:
        hash_db[full_path] = {
            "hash": video_hash,
            "duration": duration,
            "info": video_info
        }
        new_files += 1

# =============================
# SAVE DATABASE
# =============================

with open(HASH_DB_FILE, "w") as f:
    json.dump(hash_db, f, indent=4)

# =============================
# SUMMARY
# =============================

print("\n========== HASIL ==========")
print("Video diproses  :", len(all_videos))
print("Video normal    :", new_files)
print("Video duplikat  :", duplicates)
print("Video rusak     :", rusak)
print("\nSelesai ✅")