# Video Duplicate Finder

A Python-based tool for detecting **duplicate and visually similar videos** using SHA256, video metadata, and multi-frame perceptual hashing (pHash).

The tool helps clean and organize large video collections by identifying similar videos and automatically moving duplicates into a dedicated folder while keeping the higher-quality version.

## ✨ Features

* 🎬 Detect duplicate and visually similar videos
* 🔐 Exact duplicate detection via **SHA256**
* 🧠 Perceptual hashing (pHash) for visual similarity detection
* 🎞️ Compare multiple frames from each video
* ⏱️ Compare video duration
* 📐 Compare video resolution
* 📊 Compare video bitrate
* 💾 Compare file size
* 🏆 Automatically keep the higher-quality version
* 📁 Move duplicate videos to `_DUPLIKAT`
* 🔍 Move borderline matches to `_REVIEW` for manual checking
* 🚨 Move corrupted or unreadable videos to `_RUSAK`
* 💾 Persistent hash database for faster subsequent scans
* 📈 Progress tracking with `tqdm`
* ⚡ Multi-threaded processing (FFmpeg is already multi-threaded)
* 🛡️ Non-destructive file handling — files are moved, never deleted

## 🔍 How It Works

The tool does not rely on filenames to identify duplicates.

For each video it:

1. Reads video metadata (duration, resolution, bitrate, fps) using **FFprobe**.
2. Computes a **SHA256** hash to detect byte-identical files instantly.
3. Extracts 9 frames from evenly spaced points in the video.
4. Generates a perceptual hash (pHash) for each extracted frame.
5. Compares the resulting hashes with previously processed videos of similar duration.
6. Uses a **Hamming distance** to compute a similarity score (0–100%).
7. Classifies matches:
   - Scored value is equal or above the high threshold → **duplicate**.
   - Scored between the review and high thresholds → **review**.
8. For duplicates, compares quality and moves the lower-quality file to `_DUPLIKAT`, keeping the better one.
9. Moves corrupted or unreadable videos to `_RUSAK`.
10. Stores results in a local JSON database.

### Processing Flow

```text
Video Collection
       │
       ▼
 Read Metadata (FFprobe)
       │
       ▼
   SHA256 Check
       │
       ├── Byte-identical ──► Duplicate (100%)
       │
       ▼
 Extract 9 Frames
       │
       ▼
 Generate pHash
       │
       ▼
 Compare Similarity
       │
       ├── < review threshold ──► Keep
       │
       ├── review..high ──► _REVIEW
       │
       ▼
   Duplicate (≥ high threshold)
       │
       ▼
 Compare Quality
       │
       ├── Better / Equal ──► Keep
       │
       └── Worse ───────────► _DUPLIKAT
```

## 🎥 Supported Video Formats

The default configuration supports:

* `.mp4`
* `.mkv`
* `.avi`
* `.mov`
* `.wmv`
* `.flv`
* `.webm`
* `.m4v`
* `.ts`
* `.mts`
* `.m2ts`
* `.3gp`
* `.mpeg`
* `.mpg`

Additional formats can be added by modifying `VIDEO_EXTENSIONS` in the script.

## 🛠️ Requirements

### Python

Python **3.9 or newer** is recommended.

### Python Packages

```bash
pip install -r requirements.txt
```

Required packages:

```text
Pillow
ImageHash
tqdm
```

### FFmpeg

This project requires **FFmpeg** and **FFprobe** available in your `PATH`.

Verify:

```bash
ffmpeg -version
ffprobe -version
```

## 🚀 Installation

```bash
git clone https://github.com/hilalalhm/Video-Duplicate-Finder.git
cd Video-Duplicate-Finder
pip install -r requirements.txt
```

Make sure FFmpeg and FFprobe are available (see above).

## ▶️ Usage

Place the script inside the directory containing the videos you want to scan, then run:

```bash
python dupe.py
```

The script scans the directory and all subdirectories.

> ⚠️ **The default mode is `EXECUTE`.** Files are actually moved. Run a report first with `--dry-run` and always keep a backup of important collections.

### CLI Options

| Option                  | Description                                                        | Default |
| ----------------------- | ------------------------------------------------------------------ | ------- |
| `--dry-run`             | Only report, do not move any file.                                 | off     |
| `--workers N`           | Number of worker threads for hashing.                              | min(4, CPU) |
| `--threshold PCT`       | Similarity (%) for automatic duplicate.                            | 92.0    |
| `--review-threshold PCT`| Similarity (%) to enter the review bucket.                         | 82.0    |
| `--no-review-move`      | Do not move borderline files to `_REVIEW`.                         | off     |
| `--no-color`            | Disable colored terminal output.                                   | off     |

Examples:

```bash
# Report only (no files moved)
python dupe.py --dry-run

# Execute with more workers and stricter threshold
python dupe.py --workers 8 --threshold 95
```

### Example Folder Layout

Before:

```text
Video-Duplicate-Finder/
├── dupe.py
├── video1.mp4
├── video2.mp4
├── videos/
│   ├── video3.mkv
│   └── video4.mov
└── ...
```

After processing:

```text
Video-Duplicate-Finder/
├── dupe.py
├── video1.mp4
├── videos/
│   └── video3.mkv
├── _DUPLIKAT/
│   └── video2.mp4
├── _RUSAK/
│   └── corrupted_video.mp4
└── _REVIEW/
    └── borderline_video.mp4
```

## ⚙️ Configuration

The main configuration options are located at the beginning of the script.

### Similarity Thresholds

```python
HIGH_SIMILARITY = 92.0
REVIEW_SIMILARITY = 82.0
```

* `>= HIGH_SIMILARITY` → treated as a duplicate automatically.
* `>= REVIEW_SIMILARITY` but below the high threshold → moved to `_REVIEW`.
* Below the review threshold → kept.

Percentages are computed from the combined Hamming distance across all sampled frames (each pHash contributes 0–256 bits of distance).

### Duration Tolerance

```python
DURATION_TOLERANCE = 2.0
```

Maximum allowed duration difference (in seconds) between two videos that may be compared.

For example:

```text
Video A: 120 seconds
Video B: 121 seconds   → candidate for comparison
Video C: 125 seconds   → not compared with A/B
```

### Frame Sampling

```python
FRAME_POINTS = 9
PHASH_HASH_SIZE = 16
```

Videos shorter than 5 seconds are skipped (no meaningful sampling).

## 🏆 Quality Comparison

When two videos are considered duplicates, the tool compares quality with this priority:

1. **Resolution** (width × height)
2. **Bitrate**
3. **FPS**
4. **File size**

Example:

```text
Video A: 1920×1080 | 8 Mbps | 500 MB
Video B: 1280×720  | 4 Mbps | 300 MB
Result:
  KEEP → Video A
  MOVE → Video B
```

If qualities are exactly equal, the file whose path sorts first lexicographically is kept — the result is deterministic and stable across runs.

## 💾 Hash Database

The tool maintains a local JSON database:

```text
content_hash_db.json
```

It stores video path, SHA256, perceptual hash, duration, resolution, bitrate, fps, and file size — so unchanged files are skipped on subsequent scans (verified via size + mtime, with a content check via SHA256 when only the timestamp changed). The database (and result folders) are excluded from Git via `.gitignore`.

## 🛡️ Safe File Handling

The tool **does not permanently delete files**:

* Duplicates → `_DUPLIKAT/`
* Corrupted / unreadable → `_RUSAK/`
* Borderline visual matches → `_REVIEW/`

Review those folders before deleting anything manually.

## ⚠️ Important Notes

* Always create a backup before running the tool on an important collection.
* Default mode is **EXECUTE** — use `--dry-run` first to preview the results.
* Perceptual hashing detects **visual similarity**, not binary equality. Videos with different resolution, bitrate, codec, container, or encoding may still be flagged as duplicates if their visual content is similar enough.
* Videos with significant edits, cropping, overlays, or different intros/outros may not be detected — even if related.
* Extraction failures (e.g., codec ffmpeg cannot decode) are classified as corrupted; verify `_RUSAK/` contents before discarding them.

## 📁 Project Structure

```text
Video-Duplicate-Finder/
│
├── dupe.py
├── requirements.txt
├── README.md
├── .gitignore
│
├── _DUPLIKAT/
├── _RUSAK/
├── _REVIEW/
│
└── content_hash_db.json
```

The `_DUPLIKAT`, `_RUSAK`, `_REVIEW` folders and `content_hash_db.json` are generated at runtime and are not committed.

## 📦 Technologies

**Python** · **FFmpeg** · **FFprobe** · **Pillow** · **ImageHash** · **tqdm**

## 🔮 Possible Improvements

* Faster similarity search for very large collections
* Group-based duplicate resolution (keep only the global best among 3+ copies)
* More robust corruption classification
* GPU-accelerated frame extraction
* CSV/JSON report export
* Web-based interface

## 📄 License

MIT License.