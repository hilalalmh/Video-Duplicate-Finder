import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import imagehash
from PIL import Image
from tqdm import tqdm


# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

FOLDER_PATH = SCRIPT_DIR

HASH_DB_FILE = FOLDER_PATH / "content_hash_db.json"

RUSAK_FOLDER = FOLDER_PATH / "_RUSAK"
DUP_FOLDER = FOLDER_PATH / "_DUPLIKAT"
REVIEW_FOLDER = FOLDER_PATH / "_REVIEW"

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".mts",
    ".m2ts",
    ".3gp",
    ".mpeg",
    ".mpg",
}

# Durasi maksimum perbedaan agar dianggap kandidat
DURATION_TOLERANCE = 2.0

# pHash:
#
# Setiap frame mempunyai Hamming distance 0-64.
#
# SCORE:
#   100% = sama persis
#   0%   = sangat berbeda
#
# HIGH threshold:
#   otomatis dianggap duplicate
#
# REVIEW threshold:
#   kemungkinan duplicate, tetapi perlu review
#
HIGH_SIMILARITY = 92.0
REVIEW_SIMILARITY = 82.0

# Jumlah frame sampling.
# Semua video >= 5 detik menggunakan 9 frame.
FRAME_POINTS = 9

# Ukuran hash.
PHASH_HASH_SIZE = 16

# Jumlah worker default.
# Thread cocok karena proses utamanya ffmpeg/ffprobe.
DEFAULT_WORKERS = max(1, min(4, (os.cpu_count() or 4)))

# Ukuran buffer ketika SHA256
HASH_CHUNK_SIZE = 1024 * 1024

# Jika True, database akan disimpan secara berkala.
SAVE_EVERY = 25


# ============================================================
# FOLDER SETUP
# ============================================================

RUSAK_FOLDER.mkdir(exist_ok=True)
DUP_FOLDER.mkdir(exist_ok=True)
REVIEW_FOLDER.mkdir(exist_ok=True)


# ============================================================
# ARGUMENTS
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Video Duplicate Finder - SHA256 + Metadata + Multi-frame pHash"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Hanya laporan, tidak memindahkan file. Tanpa ini file langsung dipindahkan.",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Jumlah worker untuk hashing. Default: {DEFAULT_WORKERS}",
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=HIGH_SIMILARITY,
        help=f"Similarity minimum untuk duplicate otomatis. Default: {HIGH_SIMILARITY}",
    )

    parser.add_argument(
        "--review-threshold",
        type=float,
        default=REVIEW_SIMILARITY,
        help=f"Similarity minimum untuk REVIEW. Default: {REVIEW_SIMILARITY}",
    )

    parser.add_argument(
        "--no-review-move",
        action="store_true",
        help="File borderline tidak dipindahkan ke _REVIEW.",
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Nonaktifkan warna terminal.",
    )

    return parser.parse_args()


# ============================================================
# TERMINAL COLORS
# ============================================================

class Colors:
    RESET = "\033[0m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"


def color(text, code, enabled=True):
    if not enabled:
        return text
    return f"{code}{text}{Colors.RESET}"


# ============================================================
# DATABASE
# ============================================================

def load_database():
    if not HASH_DB_FILE.exists():
        return {}

    try:
        with HASH_DB_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        return data

    except Exception as e:
        print(
            color(
                f"[WARNING] Database tidak dapat dibaca: {e}",
                Colors.YELLOW,
            )
        )

        return {}


def save_database(db):
    temp_file = HASH_DB_FILE.with_suffix(".tmp")

    try:
        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(
                db,
                f,
                indent=2,
                ensure_ascii=False,
            )

        temp_file.replace(HASH_DB_FILE)

    except Exception as e:
        print(
            color(
                f"[ERROR] Gagal menyimpan database: {e}",
                Colors.RED,
            )
        )

        try:
            if temp_file.exists():
                temp_file.unlink()
        except Exception:
            pass


# ============================================================
# PATH UTILITIES
# ============================================================

def normalize_path(path):
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(Path(path).absolute())


def is_inside_folder(path, folder):
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
        return True
    except ValueError:
        return False


def should_ignore_path(path):
    path = Path(path)

    if is_inside_folder(path, RUSAK_FOLDER):
        return True

    if is_inside_folder(path, DUP_FOLDER):
        return True

    if is_inside_folder(path, REVIEW_FOLDER):
        return True

    return False


# ============================================================
# FIND VIDEOS
# ============================================================

def find_all_videos():
    videos = []

    for root, dirs, files in os.walk(FOLDER_PATH):
        root_path = Path(root)

        # Jangan masuk ke folder hasil.
        dirs[:] = [
            d
            for d in dirs
            if d not in {
                RUSAK_FOLDER.name,
                DUP_FOLDER.name,
                REVIEW_FOLDER.name,
            }
        ]

        for filename in files:
            path = root_path / filename

            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue

            if should_ignore_path(path):
                continue

            videos.append(normalize_path(path))

    return videos


# ============================================================
# FFPROBE CHECK
# ============================================================

def check_ffprobe():
    try:
        result = subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )

        return result.returncode == 0

    except Exception:
        return False


def check_ffmpeg():
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )

        return result.returncode == 0

    except Exception:
        return False


# ============================================================
# VIDEO METADATA
# ============================================================

def get_video_info(video_path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,bit_rate,r_frame_rate,avg_frame_rate,nb_frames",
        "-show_entries",
        "format=duration,bit_rate,size",
        "-of",
        "json",
        video_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

    except subprocess.TimeoutExpired:
        return None

    except Exception:
        return None

    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)

        streams = data.get("streams", [])

        if not streams:
            return None

        stream = streams[0]
        format_data = data.get("format", {})

        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)

        resolution = width * height

        duration = float(
            format_data.get("duration")
            or 0
        )

        stream_bitrate = stream.get("bit_rate")

        format_bitrate = format_data.get("bit_rate")

        bitrate = int(
            stream_bitrate
            or format_bitrate
            or 0
        )

        filesize = 0

        try:
            filesize = os.path.getsize(video_path)
        except Exception:
            pass

        fps = parse_fps(
            stream.get("avg_frame_rate")
            or stream.get("r_frame_rate")
        )

        nb_frames = stream.get("nb_frames")

        try:
            nb_frames = int(nb_frames)
        except Exception:
            nb_frames = 0

        return {
            "width": width,
            "height": height,
            "resolution": resolution,
            "bitrate": bitrate,
            "duration": duration,
            "filesize": filesize,
            "fps": fps,
            "frames": nb_frames,
        }

    except Exception:
        return None


def parse_fps(value):
    if not value:
        return 0.0

    try:
        if "/" in str(value):
            numerator, denominator = str(value).split("/", 1)

            numerator = float(numerator)
            denominator = float(denominator)

            if denominator == 0:
                return 0.0

            return numerator / denominator

        return float(value)

    except Exception:
        return 0.0


# ============================================================
# SHA256
# ============================================================

def calculate_sha256(video_path):
    sha = hashlib.sha256()

    try:
        with open(video_path, "rb") as f:
            while True:
                chunk = f.read(HASH_CHUNK_SIZE)

                if not chunk:
                    break

                sha.update(chunk)

        return sha.hexdigest()

    except Exception:
        return None


# ============================================================
# FRAME EXTRACTION
# ============================================================

def get_frame_timestamps(duration):
    if duration < 5:
        return []

    return [
        duration * ((i + 1) / (FRAME_POINTS + 1))
        for i in range(FRAME_POINTS)
    ]


def extract_frame(video_path, timestamp, output_image):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-vf",
        "scale=512:-2",
        "-q:v",
        "2",
        "-y",
        str(output_image),
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )

        return result.returncode == 0 and Path(output_image).exists()

    except Exception:
        return False


# ============================================================
# PHASH
# ============================================================

def get_video_hash(video_path, duration):
    timestamps = get_frame_timestamps(duration)

    if not timestamps:
        return None

    hashes = []

    with tempfile.TemporaryDirectory(
        prefix="video_hash_"
    ) as temp_dir:

        for index, timestamp in enumerate(timestamps):
            image_path = (
                Path(temp_dir)
                / f"frame_{index:02d}.jpg"
            )

            success = extract_frame(
                video_path,
                timestamp,
                image_path,
            )

            if not success:
                return None

            try:
                with Image.open(image_path) as img:
                    img = img.convert("RGB")

                    ph = imagehash.phash(
                        img,
                        hash_size=PHASH_HASH_SIZE,
                    )

                    hashes.append(str(ph))

            except Exception:
                return None

    if len(hashes) != FRAME_POINTS:
        return None

    return "|".join(hashes)


# ============================================================
# PHASH COMPARISON
# ============================================================

def hash_similarity(hash1, hash2):
    if not hash1 or not hash2:
        return 0.0

    list1 = hash1.split("|")
    list2 = hash2.split("|")

    if len(list1) != len(list2):
        return 0.0

    try:
        total_distance = 0

        max_distance = (
            PHASH_HASH_SIZE
            * PHASH_HASH_SIZE
            * len(list1)
        )

        for h1, h2 in zip(list1, list2):
            hash_a = imagehash.hex_to_hash(h1)
            hash_b = imagehash.hex_to_hash(h2)

            total_distance += hash_a - hash_b

        if max_distance <= 0:
            return 0.0

        similarity = (
            1.0
            - (total_distance / max_distance)
        ) * 100.0

        return max(0.0, min(100.0, similarity))

    except Exception:
        return 0.0


# ============================================================
# DURATION CHECK
# ============================================================

def duration_difference(duration1, duration2):
    return abs(
        float(duration1 or 0)
        - float(duration2 or 0)
    )


def duration_is_candidate(duration1, duration2):
    return (
        duration_difference(
            duration1,
            duration2,
        )
        <= DURATION_TOLERANCE
    )


# ============================================================
# QUALITY COMPARISON
# ============================================================

def quality_score(info):
    if not info:
        return (
            0,
            0,
            0,
            0,
        )

    return (
        int(info.get("resolution", 0)),
        int(info.get("bitrate", 0)),
        float(info.get("fps", 0)),
        int(info.get("filesize", 0)),
    )


def is_better_quality(
    info1,
    info2,
    path1=None,
    path2=None,
):
    if not info1:
        return False

    if not info2:
        return True

    score1 = quality_score(info1)
    score2 = quality_score(info2)

    if score1 != score2:
        return score1 > score2

    # Kualitas identik: pertahankan yang path-nya lebih kecil
    # secara leksikografis agar hasil tidak bergantung pada
    # urutan pemrosesan / isi dictionary.
    if path1 is not None and path2 is not None:
        return path1 < path2

    return True


# ============================================================
# DATABASE RECORD
# ============================================================

def build_record(video_path, info, sha256=None, video_hash=None):
    try:
        stat = os.stat(video_path)

        mtime = stat.st_mtime
        filesize = stat.st_size

    except Exception:
        mtime = 0
        filesize = 0

    return {
        "path": normalize_path(video_path),
        "size": filesize,
        "mtime": mtime,
        "sha256": sha256,
        "hash": video_hash,
        "duration": info.get("duration", 0),
        "info": info,
    }


# ============================================================
# DATABASE CACHE CHECK
# ============================================================

def get_cached_record(db, video_path):
    path = normalize_path(video_path)

    record = db.get(path)

    if not isinstance(record, dict):
        return None

    try:
        stat = os.stat(path)

        current_size = stat.st_size
        current_mtime = stat.st_mtime

        old_size = record.get("size")
        old_mtime = record.get("mtime")

        if (
            old_size == current_size
            and old_mtime == current_mtime
            and record.get("hash")
            and record.get("info")
        ):
            return record

        # Ukuran sama tapi mtime berubah (misal touch/copy):
        # cek konten via SHA256. Jika identik, pakai ulang record
        # agar tidak perlu ekstraksi frame ulang.
        if (
            old_size == current_size
            and record.get("sha256")
            and record.get("hash")
            and record.get("info")
        ):
            sha = calculate_sha256(path)

            if sha and sha == record["sha256"]:
                updated = dict(record)
                updated["size"] = current_size
                updated["mtime"] = current_mtime

                return updated

    except Exception:
        return None

    return None


# ============================================================
# REMOVE STALE DATABASE
# ============================================================

def clean_stale_database(db):
    stale = []

    for path in list(db.keys()):
        if not os.path.exists(path):
            stale.append(path)

    for path in stale:
        db.pop(path, None)

    return len(stale)


# ============================================================
# SAFE MOVE
# ============================================================

def safe_move(src, dest_folder):
    src = Path(src)
    dest_folder = Path(dest_folder)

    if not src.exists():
        return None

    dest_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = dest_folder / src.name

    counter = 1

    while destination.exists():
        destination = (
            dest_folder
            / f"{src.stem}_{counter}{src.suffix}"
        )

        counter += 1

    try:
        shutil.move(
            str(src),
            str(destination),
        )

        return normalize_path(destination)

    except Exception as e:
        print(
            color(
                f"[ERROR] Gagal memindahkan:\n"
                f"        {src}\n"
                f"        {e}",
                Colors.RED,
            )
        )

        return None


# ============================================================
# PROCESS SINGLE VIDEO
# ============================================================

def process_video(video_path, db):
    video_path = normalize_path(video_path)

    cached = get_cached_record(
        db,
        video_path,
    )

    if cached:
        return cached

    info = get_video_info(video_path)

    if info is None:
        return {
            "path": video_path,
            "status": "corrupt",
        }

    if info["duration"] < 5:
        return {
            "path": video_path,
            "status": "too_short",
            "info": info,
        }

    sha256 = calculate_sha256(video_path)

    if sha256 is None:
        return {
            "path": video_path,
            "status": "corrupt",
        }

    video_hash = get_video_hash(
        video_path,
        info["duration"],
    )

    if video_hash is None:
        return {
            "path": video_path,
            "status": "corrupt",
        }

    return build_record(
        video_path,
        info,
        sha256,
        video_hash,
    )


# ============================================================
# EXACT DUPLICATE INDEX
# ============================================================

def build_sha_index(db):
    index = {}

    for path, record in db.items():
        if not isinstance(record, dict):
            continue

        sha = record.get("sha256")

        if not sha:
            continue

        if not os.path.exists(path):
            continue

        index.setdefault(
            sha,
            [],
        ).append(path)

    return index


# ============================================================
# DURATION INDEX
# ============================================================

def duration_bucket(duration):
    return int(
        round(
            float(duration)
            / DURATION_TOLERANCE
        )
    )


def build_duration_index(db):
    index = {}

    for path, record in db.items():
        if not isinstance(record, dict):
            continue

        if not os.path.exists(path):
            continue

        duration = record.get("duration")

        if duration is None:
            continue

        bucket = duration_bucket(duration)

        index.setdefault(
            bucket,
            [],
        ).append(path)

    return index


def get_candidate_paths(
    record,
    duration_index,
):
    duration = record.get("duration", 0)

    bucket = duration_bucket(duration)

    candidates = set()

    # Cek bucket sekitar karena toleransi durasi.
    # Window ±2 menutup edge case banker's rounding
    # (misal 101.0 s dan 103.0 s beda 2 bucket penuh);
    # kelebihan kandidat nanti difilter oleh duration_is_candidate.
    for nearby_bucket in range(
        bucket - 2,
        bucket + 3,
    ):
        for path in duration_index.get(
            nearby_bucket,
            [],
        ):
            candidates.add(path)

    return candidates


# ============================================================
# FIND EXACT DUPLICATE
# ============================================================

def find_exact_duplicate(
    current_path,
    current_record,
    sha_index,
):
    sha = current_record.get("sha256")

    if not sha:
        return None

    candidates = sha_index.get(
        sha,
        [],
    )

    for path in candidates:
        if path == current_path:
            continue

        if os.path.exists(path):
            return path

    return None


# ============================================================
# FIND VISUAL MATCH
# ============================================================

def find_visual_matches(
    current_path,
    current_record,
    db,
    duration_index,
    high_threshold,
    review_threshold,
):
    candidates = get_candidate_paths(
        current_record,
        duration_index,
    )

    results = []

    current_duration = current_record.get(
        "duration",
        0,
    )

    current_hash = current_record.get(
        "hash"
    )

    if not current_hash:
        return results

    for candidate_path in candidates:
        if candidate_path == current_path:
            continue

        if not os.path.exists(candidate_path):
            continue

        candidate_record = db.get(
            candidate_path
        )

        if not candidate_record:
            continue

        candidate_duration = candidate_record.get(
            "duration",
            0,
        )

        if not duration_is_candidate(
            current_duration,
            candidate_duration,
        ):
            continue

        candidate_hash = candidate_record.get(
            "hash"
        )

        if not candidate_hash:
            continue

        similarity = hash_similarity(
            current_hash,
            candidate_hash,
        )

        if similarity >= review_threshold:
            results.append(
                {
                    "path": candidate_path,
                    "similarity": similarity,
                    "level": (
                        "duplicate"
                        if similarity >= high_threshold
                        else "review"
                    ),
                }
            )

    results.sort(
        key=lambda x: x["similarity"],
        reverse=True,
    )

    return results


# ============================================================
# DISPLAY HELPERS
# ============================================================

def format_size(size):
    size = float(size or 0)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
    ]

    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size:.2f} PB"


def format_duration(seconds):
    seconds = int(seconds or 0)

    hours = seconds // 3600

    minutes = (
        seconds % 3600
    ) // 60

    secs = seconds % 60

    if hours:
        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{secs:02d}"
        )

    return (
        f"{minutes:02d}:"
        f"{secs:02d}"
    )


def print_match(
    current_path,
    existing_path,
    similarity,
    action,
    colors_enabled,
):
    if action == "duplicate":
        label = color(
            "[DUPLIKAT]",
            Colors.RED,
            colors_enabled,
        )

    elif action == "review":
        label = color(
            "[REVIEW]",
            Colors.YELLOW,
            colors_enabled,
        )

    else:
        label = "[MATCH]"

    print()
    print(
        f"{label} "
        f"{similarity:.2f}%"
    )

    print(
        color(
            f"  File baru : {current_path}",
            Colors.WHITE,
            colors_enabled,
        )
    )

    print(
        color(
            f"  Pembanding: {existing_path}",
            Colors.GRAY,
            colors_enabled,
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():
    args = parse_args()

    colors_enabled = not args.no_color

    if args.workers < 1:
        args.workers = 1

    if not (
        0 <= args.review_threshold
        <= args.threshold
        <= 100
    ):
        print(
            color(
                "[ERROR] Threshold harus memenuhi:",
                Colors.RED,
                colors_enabled,
            )
        )

        print(
            "        0 <= review-threshold <= threshold <= 100"
        )

        sys.exit(1)

    execute_mode = not args.dry_run

    print()
    print(
        color(
            "==============================================",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print(
        color(
            "        VIDEO DUPLICATE FINDER",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print(
        color(
            "==============================================",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print()
    print(f"Folder scan : {FOLDER_PATH}")
    print(f"Database    : {HASH_DB_FILE}")
    print(f"Workers     : {args.workers}")
    print(f"Duplicate   : >= {args.threshold:.1f}%")
    print(f"Review      : >= {args.review_threshold:.1f}%")
    print(
        "Mode        : "
        + (
            color(
                "EXECUTE",
                Colors.GREEN,
                colors_enabled,
            )
            if execute_mode
            else color(
                "DRY RUN",
                Colors.RED,
                colors_enabled,
            )
        )
    )

    print()

    # --------------------------------------------------------
    # CHECK FFMPEG
    # --------------------------------------------------------

    if not check_ffprobe():
        print(
            color(
                "[ERROR] ffprobe tidak ditemukan.",
                Colors.RED,
                colors_enabled,
            )
        )

        print(
            "Pastikan FFmpeg sudah terinstall dan "
            "ffprobe tersedia di PATH."
        )

        sys.exit(1)

    if not check_ffmpeg():
        print(
            color(
                "[ERROR] ffmpeg tidak ditemukan.",
                Colors.RED,
                colors_enabled,
            )
        )

        print(
            "Pastikan FFmpeg sudah terinstall dan "
            "ffmpeg tersedia di PATH."
        )

        sys.exit(1)

    # --------------------------------------------------------
    # LOAD DATABASE
    # --------------------------------------------------------

    db = load_database()

    stale_count = clean_stale_database(db)

    if stale_count:
        print(
            color(
                f"[DATABASE] "
                f"{stale_count} entry lama dibersihkan.",
                Colors.YELLOW,
                colors_enabled,
            )
        )

    # --------------------------------------------------------
    # FIND VIDEOS
    # --------------------------------------------------------

    all_videos = find_all_videos()

    print(
        f"Video ditemukan : {len(all_videos)}"
    )

    if not all_videos:
        print()
        print(
            color(
                "Tidak ada video yang ditemukan.",
                Colors.YELLOW,
                colors_enabled,
            )
        )

        return

    # --------------------------------------------------------
    # PROCESS VIDEOS
    # --------------------------------------------------------

    records = {}

    new_records = 0
    cached_records = 0
    corrupt_files = []
    short_files = []

    print()

    with ThreadPoolExecutor(
        max_workers=args.workers
    ) as executor:

        future_map = {}

        for video_path in all_videos:
            future = executor.submit(
                process_video,
                video_path,
                db,
            )

            future_map[future] = video_path

        for future in tqdm(
            as_completed(future_map),
            total=len(future_map),
            desc="Analyzing videos",
            unit="video",
        ):
            video_path = future_map[future]

            try:
                record = future.result()

            except Exception as e:
                print(
                    color(
                        f"\n[ERROR] {video_path}\n"
                        f"        {e}",
                        Colors.RED,
                        colors_enabled,
                    )
                )

                corrupt_files.append(
                    video_path
                )

                continue

            status = record.get(
                "status"
            )

            if status == "corrupt":
                corrupt_files.append(
                    video_path
                )

                continue

            if status == "too_short":
                short_files.append(
                    video_path
                )

                continue

            db_entry = db.get(video_path, {})

            if (
                db_entry.get("mtime")
                == record.get("mtime")
                and db_entry.get("size")
                == record.get("size")
            ) or (
                db_entry.get("sha256")
                and db_entry.get("sha256")
                == record.get("sha256")
            ):
                cached_records += 1

            else:
                new_records += 1

            db[video_path] = record
            records[video_path] = record

    # --------------------------------------------------------
    # SAVE INITIAL DATABASE
    # --------------------------------------------------------

    save_database(db)

    # --------------------------------------------------------
    # BUILD INDEX
    # --------------------------------------------------------

    sha_index = build_sha_index(db)

    duration_index = build_duration_index(db)

    # --------------------------------------------------------
    # SCAN DUPLICATES
    # --------------------------------------------------------

    duplicates = []
    reviews = []

    processed_duplicate_paths = set()
    processed_review_paths = set()

    print()
    print(
        color(
            "Mencari duplikat...",
            Colors.CYAN,
            colors_enabled,
        )
    )

    for current_path in tqdm(
        records.keys(),
        desc="Comparing",
        unit="video",
    ):
        current_record = records[
            current_path
        ]

        # ----------------------------------------------------
        # EXACT DUPLICATE
        # ----------------------------------------------------

        exact_path = find_exact_duplicate(
            current_path,
            current_record,
            sha_index,
        )

        if exact_path:
            # Hindari memproses pasangan yang sama
            pair = tuple(
                sorted(
                    [
                        current_path,
                        exact_path,
                    ]
                )
            )

            if pair in processed_duplicate_paths:
                continue

            processed_duplicate_paths.add(pair)

            duplicates.append(
                {
                    "current": current_path,
                    "existing": exact_path,
                    "similarity": 100.0,
                    "reason": "SHA256",
                }
            )

            continue

        # ----------------------------------------------------
        # VISUAL MATCH
        # ----------------------------------------------------

        matches = find_visual_matches(
            current_path,
            current_record,
            db,
            duration_index,
            args.threshold,
            args.review_threshold,
        )

        if not matches:
            continue

        best = matches[0]

        existing_path = best["path"]
        similarity = best["similarity"]
        level = best["level"]

        pair = tuple(
            sorted(
                [
                    current_path,
                    existing_path,
                ]
            )
        )

        if level == "duplicate":
            if pair in processed_duplicate_paths:
                continue

            processed_duplicate_paths.add(pair)

            duplicates.append(
                {
                    "current": current_path,
                    "existing": existing_path,
                    "similarity": similarity,
                    "reason": "pHash",
                }
            )

        elif level == "review":
            if pair in processed_review_paths:
                continue

            processed_review_paths.add(pair)

            reviews.append(
                {
                    "current": current_path,
                    "existing": existing_path,
                    "similarity": similarity,
                    "reason": "pHash",
                }
            )

    # --------------------------------------------------------
    # RESOLVE DUPLICATES
    # --------------------------------------------------------

    moved_duplicates = 0
    moved_reviews = 0
    moved_corrupt = 0

    print()

    print(
        color(
            "==============================================",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print(
        color(
            "                 HASIL SCAN",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print(
        color(
            "==============================================",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print()

    print(
        f"Total video          : {len(all_videos)}"
    )

    print(
        f"Video database baru  : {new_records}"
    )

    print(
        f"Video dari cache     : {cached_records}"
    )

    print(
        f"Video rusak          : {len(corrupt_files)}"
    )

    print(
        f"Video terlalu pendek : {len(short_files)}"
    )

    print(
        f"Duplikat             : {len(duplicates)}"
    )

    print(
        f"Review               : {len(reviews)}"
    )

    # --------------------------------------------------------
    # CORRUPT FILES
    # --------------------------------------------------------

    if corrupt_files:
        print()
        print(
            color(
                "========== VIDEO RUSAK ==========",
                Colors.RED,
                colors_enabled,
            )
        )

        for path in corrupt_files:
            print(
                f"[RUSAK] {path}"
            )

            if execute_mode:
                destination = safe_move(
                    path,
                    RUSAK_FOLDER,
                )

                if destination:
                    db.pop(
                        normalize_path(path),
                        None,
                    )

                    moved_corrupt += 1

    # --------------------------------------------------------
    # DUPLICATES
    # --------------------------------------------------------

    if duplicates:
        print()
        print(
            color(
                "========== DUPLIKAT ==========",
                Colors.RED,
                colors_enabled,
            )
        )

    for duplicate in duplicates:
        current_path = duplicate[
            "current"
        ]

        existing_path = duplicate[
            "existing"
        ]

        similarity = duplicate[
            "similarity"
        ]

        reason = duplicate[
            "reason"
        ]

        current_record = db.get(
            current_path
        )

        existing_record = db.get(
            existing_path
        )

        if not current_record:
            continue

        if not existing_record:
            continue

        current_info = current_record.get(
            "info"
        )

        existing_info = existing_record.get(
            "info"
        )

        # Tentukan file mana yang dipertahankan.
        if is_better_quality(
            current_info,
            existing_info,
            current_path,
            existing_path,
        ):
            keep_path = current_path
            move_path = existing_path

        else:
            keep_path = existing_path
            move_path = current_path

        print()
        print(
            color(
                f"[DUPLIKAT] "
                f"{similarity:.2f}% "
                f"({reason})",
                Colors.RED,
                colors_enabled,
            )
        )

        print(
            f"  KEEP : {keep_path}"
        )

        print(
            f"  MOVE : {move_path}"
        )

        if current_info:
            print(
                f"  Current : "
                f"{current_info.get('width', 0)}x"
                f"{current_info.get('height', 0)} | "
                f"{format_size(current_info.get('filesize', 0))} | "
                f"{current_info.get('bitrate', 0) / 1000:.0f} kbps"
            )

        if existing_info:
            print(
                f"  Existing: "
                f"{existing_info.get('width', 0)}x"
                f"{existing_info.get('height', 0)} | "
                f"{format_size(existing_info.get('filesize', 0))} | "
                f"{existing_info.get('bitrate', 0) / 1000:.0f} kbps"
            )

        if execute_mode:
            destination = safe_move(
                move_path,
                DUP_FOLDER,
            )

            if destination:
                moved_duplicates += 1

                db.pop(
                    normalize_path(move_path),
                    None,
                )

                # Jika keep path merupakan current,
                # pastikan tetap ada di database.
                if os.path.exists(keep_path):
                    db[normalize_path(keep_path)] = (
                        db.get(
                            normalize_path(keep_path),
                            current_record
                            if keep_path == current_path
                            else existing_record,
                        )
                    )

    # --------------------------------------------------------
    # REVIEW
    # --------------------------------------------------------

    if reviews:
        print()
        print(
            color(
                "========== REVIEW ==========",
                Colors.YELLOW,
                colors_enabled,
            )
        )

    for review in reviews:
        current_path = review[
            "current"
        ]

        existing_path = review[
            "existing"
        ]

        similarity = review[
            "similarity"
        ]

        reason = review[
            "reason"
        ]

        print_match(
            current_path,
            existing_path,
            similarity,
            "review",
            colors_enabled,
        )

        if (
            execute_mode
            and not args.no_review_move
        ):
            destination = safe_move(
                current_path,
                REVIEW_FOLDER,
            )

            if destination:
                moved_reviews += 1

                db.pop(
                    normalize_path(
                        current_path
                    ),
                    None,
                )

    # --------------------------------------------------------
    # SHORT FILES
    # --------------------------------------------------------

    if short_files:
        print()
        print(
            color(
                "========== VIDEO < 5 DETIK ==========",
                Colors.YELLOW,
                colors_enabled,
            )
        )

        print(
            "Video < 5 detik tidak dianalisis "
            "dengan pHash dan tidak dipindahkan."
        )

        for path in short_files:
            print(
                f"  {path}"
            )

    # --------------------------------------------------------
    # SAVE DATABASE
    # --------------------------------------------------------

    save_database(db)

    # --------------------------------------------------------
    # FINAL SUMMARY
    # --------------------------------------------------------

    print()
    print(
        color(
            "==============================================",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print(
        color(
            "                 SELESAI",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print(
        color(
            "==============================================",
            Colors.CYAN,
            colors_enabled,
        )
    )

    print()

    print(
        f"Video dipindai       : {len(all_videos)}"
    )

    print(
        f"Duplikat ditemukan   : {len(duplicates)}"
    )

    print(
        f"Review ditemukan     : {len(reviews)}"
    )

    print(
        f"Video rusak          : {len(corrupt_files)}"
    )

    print(
        f"Video < 5 detik      : {len(short_files)}"
    )

    if execute_mode:
        print()
        print(
            color(
                f"Duplikat dipindahkan : {moved_duplicates}",
                Colors.GREEN,
                colors_enabled,
            )
        )

        print(
            color(
                f"Review dipindahkan   : {moved_reviews}",
                Colors.YELLOW,
                colors_enabled,
            )
        )

        print(
            color(
                f"Rusak dipindahkan    : {moved_corrupt}",
                Colors.RED,
                colors_enabled,
            )
        )

    else:
        print()
        print(
            color(
                "DRY RUN: tidak ada file yang dipindahkan.",
                Colors.YELLOW,
                colors_enabled,
            )
        )

        print()
        print(
            "Jalankan tanpa --dry-run untuk "
            "langsung memindahkan file:"
        )

        print()
        print(
            "    python dupe.py"
        )

    print()
    print(
        f"Database: {HASH_DB_FILE}"
    )

    print()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print()
        print(
            "\nProses dihentikan oleh user."
        )

    except Exception as e:
        print()
        print(
            f"[FATAL ERROR] {e}"
        )

        raise