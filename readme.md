# Video Duplicate Finder

A Python-based tool for detecting **duplicate and visually similar videos** using perceptual hashing (pHash), video metadata, and frame comparison.

The tool is designed to help clean and organize large video collections by identifying similar videos and automatically moving duplicates into a separate folder while keeping the higher-quality version.

## ✨ Features

* 🎬 Detect duplicate and visually similar videos
* 🧠 Perceptual hashing (pHash) for visual similarity detection
* 🎞️ Compare multiple frames from each video
* ⏱️ Compare video duration
* 📐 Compare video resolution
* 📊 Compare video bitrate
* 💾 Compare file size
* 🏆 Automatically keep the higher-quality version
* 📁 Move duplicate videos to a dedicated folder
* 🚨 Move corrupted or unreadable videos to a separate folder
* 💾 Persistent hash database for faster subsequent scans
* 📈 Progress tracking with `tqdm`
* 🛡️ Non-destructive file handling — files are moved instead of deleted

## 🔍 How It Works

The tool does not rely on filenames to identify duplicates.

For each video, it:

1. Reads the video duration using **FFprobe**.
2. Extracts several frames from different points in the video.
3. Generates a perceptual hash (pHash) for each extracted frame.
4. Compares the generated hashes with previously processed videos.
5. Checks video duration similarity.
6. Compares video quality when a duplicate is detected.
7. Keeps the higher-quality video.
8. Moves the lower-quality duplicate to `_DUPLIKAT`.
9. Moves corrupted or unreadable videos to `_RUSAK`.
10. Stores the results in a local JSON database.

### Processing Flow

```text
Video Collection
       │
       ▼
   Read Metadata
       │
       ▼
 Extract Video Frames
       │
       ▼
 Generate pHash
       │
       ▼
 Compare Similarity
       │
       ├── Not Similar ──► Keep
       │
       ▼
     Duplicate
       │
       ▼
 Compare Quality
       │
       ├── Higher Quality ──► Keep
       │
       └── Lower Quality ───► _DUPLIKAT
```

## 🎥 Supported Video Formats

The default configuration supports:

* `.mp4`
* `.mkv`
* `.avi`
* `.mov`
* `.wmv`

Additional formats can be added by modifying `VIDEO_EXT` in the Python script.

## 🛠️ Requirements

### Python

Python **3.9 or newer** is recommended.

### Python Packages

Install the required packages:

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

This project requires **FFmpeg** and **FFprobe**.

Verify that they are available from your terminal:

```bash
ffmpeg -version
```

```bash
ffprobe -version
```

If the commands are not recognized, install FFmpeg and add its `bin` directory to your system `PATH`.

## 🚀 Installation

Clone the repository:

```bash
git clone https://github.com/USERNAME/Video-Duplicate-Finder.git
```

Navigate to the project directory:

```bash
cd Video-Duplicate-Finder
```

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Make sure FFmpeg and FFprobe are available:

```bash
ffmpeg -version
ffprobe -version
```

## ▶️ Usage

Place the script inside the directory containing the videos you want to scan.

Run:

```bash
python video_duplicate_finder.py
```

The script will automatically scan the directory and its subdirectories.

Example:

```text
Video-Duplicate-Finder/
├── video_duplicate_finder.py
├── video1.mp4
├── video2.mp4
├── videos/
│   ├── video3.mkv
│   └── video4.mov
└── ...
```

After processing, duplicate and corrupted files will be moved into dedicated folders:

```text
Video-Duplicate-Finder/
├── video_duplicate_finder.py
├── video1.mp4
├── videos/
│   └── video3.mkv
├── _DUPLIKAT/
│   └── video2.mp4
└── _RUSAK/
    └── corrupted_video.mp4
```

## ⚙️ Configuration

The main configuration options are located at the beginning of the script.

### Similarity Threshold

```python
THRESHOLD = 18
```

This value controls how much perceptual hash difference is allowed between videos.

A lower value means stricter similarity detection.

A higher value allows more visual variation.

### Duration Tolerance

```python
DURATION_TOLERANCE = 2
```

This defines the maximum allowed duration difference in seconds when comparing videos.

For example:

```text
Video A: 120 seconds
Video B: 121 seconds
```

These videos can still be considered similar.

However:

```text
Video A: 120 seconds
Video B: 125 seconds
```

will not pass the default duration check.

## 🏆 Quality Comparison

When two videos are considered duplicates, the tool compares their quality.

The comparison priority is:

1. **Resolution**
2. **Bitrate**
3. **File size**

Example:

```text
Video A
1920 × 1080
8 Mbps
500 MB

Video B
1280 × 720
4 Mbps
300 MB
```

Result:

```text
KEEP       → Video A
DUPLICATE  → Video B
```

This allows the tool to automatically retain the better-quality version instead of simply keeping whichever file was scanned first.

## 💾 Hash Database

The tool maintains a local JSON database:

```text
content_hash_db.json
```

The database stores information such as:

* Video path
* Perceptual hash
* Duration
* Resolution
* Bitrate
* File size

This allows previously processed videos to be skipped during future scans.

The database is intentionally excluded from Git using `.gitignore`.

## 🛡️ Safe File Handling

The tool does **not permanently delete files**.

Duplicate videos are moved to:

```text
_DUPLIKAT/
```

Corrupted or unreadable videos are moved to:

```text
_RUSAK/
```

This makes it possible to manually review the results before permanently deleting anything.

## ⚠️ Important Notes

Always create a backup before running the tool on an important video collection.

Perceptual hashing detects **visual similarity**, not binary file equality.

Therefore, videos with different:

* resolutions
* bitrates
* codecs
* containers
* encodings

may still be identified as duplicates if their visual content is sufficiently similar.

Likewise, videos with significant edits, cropping, overlays, different intros/outros, or substantial frame changes may not be detected as duplicates.

## 📁 Project Structure

```text
Video-Duplicate-Finder/
│
├── video_duplicate_finder.py
├── requirements.txt
├── README.md
├── .gitignore
│
├── _DUPLIKAT/
│   └── ...
│
├── _RUSAK/
│   └── ...
│
└── content_hash_db.json
```

The `_DUPLIKAT`, `_RUSAK`, and `content_hash_db.json` directories/files are generated during runtime and should not be committed to the repository.

## 📦 Technologies

* **Python**
* **FFmpeg**
* **FFprobe**
* **Pillow**
* **ImageHash**
* **tqdm**

## 🔮 Possible Improvements

Future improvements may include:

* Multi-threaded or multiprocessing video processing
* Faster similarity search for large video collections
* Exact file hashing before perceptual hashing
* More advanced video fingerprinting
* GPU-accelerated frame extraction
* Automatic backup and restore functionality
* Detailed scan reports
* CSV/JSON export of duplicate groups
* Web-based interface
* Support for additional video formats

## 📄 License

This project is licensed under the **MIT License**.
# Video-Duplicate-Finder
