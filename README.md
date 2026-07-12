# StreamShare

StreamShare is a modular Python CLI application that monitors livestreams on **Twitch**, **TikTok**, and **YouTube**, and automatically announces them on **Twitter (X)**, **Instagram Stories**, and **Discord**. 

It downloads stream thumbnails (or game box/key art), crops and resizes them to the correct aspect ratios (9:16 for Instagram Stories, 16:9 for Twitter/Discord), posts announcements, and continues monitoring the stream to update or delete announcements when categories change or the stream ends.

## Features

- **Multi-Source Support**: Fetch livestream status and metadata from Twitch, TikTok, and YouTube.
- **Dynamic Live Frames**: Extract a live frame from the stream using FFmpeg to use as the announcement image (with automatic ad-screen detection and fallback).
- **IGDB Integration**: Fetch and cache high-resolution game cover/key art from the IGDB database using Twitch credentials.
- **Automated Image Cropping**: Auto-detects content boundaries (removes letterbox/pillarbox bars) and crops images to size (1080x1920 for Stories, 1280x720/1080x1350 for Twitter/Discord posts).
- **Ultrawide Support**: Reverse squishing of ultrawide (21:9 or 32:9) stream thumbnails.
- **Auto-Cleanup / Monitoring**: Monitors the stream's status in the background:
  - Deletes announcements when the stream goes offline (unless `--preserve-end-posts` is set).
  - Deletes or updates posts when the streamer changes game categories (unless `--preserve-category-posts` is set).
- **Instagram Session Caching & 2FA**: Caches Instagram login sessions locally (`instagram_session.json`) to prevent repeated login challenges, and supports TOTP 2FA.
- **Twitter Thread Announce**: Post the link in a response/reply tweet instead of the main tweet (`--response`) to improve algorithmic visibility on Twitter.

## Prerequisites

- Python 3.12+
- **FFmpeg**: Required if using the `--live-frame` capture feature. Ensure `ffmpeg` is installed and available in your system path.
- API credentials for the platforms you wish to integrate.

## Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/StreamShare.git
   cd StreamShare
   ```

2. **Create a virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```

## Usage

Start sharing a stream by running:
```bash
python streamshare.py [identifier] [options]
```

### Arguments
- `identifier`: Streamer's username, URL, or channel ID.

### Options
- `--platform`: Select stream source platform: `twitch`, `tiktok`, or `youtube`. Defaults to `twitch` (or `DEFAULT_SOURCE_PLATFORM` in `.env`).
- `--boxart`: Use game/category box art instead of the stream thumbnail.
- `--keyart`: Use high-resolution game cover art from IGDB.
- `--anchor`: Crop anchor side if the image needs scaling: `center`, `left`, `right`, `top`, `bottom`. Defaults to `center`.
- `--aspect-ratio`: Force input aspect ratio adjustment for ultrawide streams: `auto`, `21:9`, `32:9`.
- `--local`: Run in local mode; processes and saves images in `generated_images/` without posting to social media (highly recommended for previewing crops).
- `--live-frame`: Capture a live frame from the active stream via FFmpeg instead of using the static stream thumbnail.
- `--no-monitor`: Fire-and-forget; announce the stream and exit immediately without monitoring status changes or deleting posts.
- `--preserve-end-posts`: Prevent automatic post deletion when the stream ends.
- `--preserve-category-posts`: Prevent automatic post deletion when the game category changes.
- `--response`: Post the stream URL as a reply tweet on Twitter to improve visibility.
- `--debug`: Enable verbose debug logging.

### Examples

* **Test stream cropping locally (Twitch)**:
  ```bash
  python streamshare.py test_stream --platform twitch --local
  ```
  Check `generated_images/` for output cropped files.

* **Share Twitch stream and monitor status**:
  ```bash
  python streamshare.py username --platform twitch
  ```

* **Share TikTok Live with a captured live frame**:
  ```bash
  python streamshare.py @username --platform tiktok --live-frame
  ```

* **Share YouTube stream using box art and reply-to link**:
  ```bash
  python streamshare.py @channel_handle --platform youtube --boxart --response
  ```

## Architecture

The codebase follows a modular architecture under `src/`:
- `src/core/`: Environment config parsing (`config.py`), log formatters (`logging.py`), custom exceptions (`exceptions.py`), and system user context (`context.py`).
- `src/media/`: PIL-based image resizing, cropping, and aspect-ratio handlers (`processor.py`, `instagram.py`, `twitter.py`).
- `src/platforms/`: Client integrations for social media APIs (`instagram/`, `twitter/`, `discord/`).
- `src/platforms/sources/`: Stream status extractors (`twitch.py`, `tiktok.py`, `youtube.py`, `igdb.py`) coordinated by a central manager (`manager.py`).

## License

MIT
