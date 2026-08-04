"""
Daily per-platform post quota tracking for kids-studio, kept as its own
storage file so this service's usage never touches the main app's
storage/quota_usage.json (kids-studio is deployed as a fully separate
service — see kids-studio/main.py).
"""
from datetime import datetime
from pathlib import Path
import json
import os

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
QUOTA_FILE = STORAGE_DIR / "kids_quota_usage.json"

PLATFORMS = ["youtube", "tiktok", "instagram", "facebook"]

DEFAULT_LIMITS = {
    "youtube": int(os.getenv("KIDS_MAX_YOUTUBE_POSTS_PER_DAY", "2")),
    "tiktok": int(os.getenv("KIDS_MAX_TIKTOK_POSTS_PER_DAY", "2")),
    "instagram": int(os.getenv("KIDS_MAX_INSTAGRAM_POSTS_PER_DAY", "2")),
    "facebook": int(os.getenv("KIDS_MAX_FACEBOOK_POSTS_PER_DAY", "2")),
}


def _today() -> str:
    return datetime.now().date().isoformat()


def get_fresh_quota(today: str) -> dict:
    quota = {"date": today}
    for platform in PLATFORMS:
        quota[platform] = {
            "posts_today": 0,
            "posts_limit": DEFAULT_LIMITS[platform],
            "last_post": None,
            "last_error": None,
            "status": "ok",
        }
    quota["cloudinary"] = {"uploads_today": 0, "last_error": None}
    return quota


def load_quota_usage() -> dict:
    today = _today()
    try:
        with open(QUOTA_FILE, "r") as f:
            data = json.load(f)
        if data.get("date") != today:
            return get_fresh_quota(today)
        return data
    except Exception:
        return get_fresh_quota(today)


def save_quota_usage(data: dict) -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUOTA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def can_use_platform(platform: str) -> bool:
    quota = load_quota_usage()
    entry = quota.get(platform)
    if not entry:
        return True
    return entry["posts_today"] < entry["posts_limit"]


def track_platform_post(platform: str) -> None:
    quota = load_quota_usage()
    entry = quota.setdefault(platform, get_fresh_quota(quota["date"])[platform])
    entry["posts_today"] += 1
    entry["last_post"] = datetime.now().isoformat()
    entry["status"] = "ok"
    save_quota_usage(quota)


def track_platform_error(platform: str, error: str) -> None:
    quota = load_quota_usage()
    entry = quota.setdefault(platform, get_fresh_quota(quota["date"])[platform])
    entry["last_error"] = {"message": str(error)[:200], "time": datetime.now().isoformat()}
    entry["status"] = "error"
    save_quota_usage(quota)


def track_cloudinary_upload() -> None:
    quota = load_quota_usage()
    quota["cloudinary"]["uploads_today"] += 1
    save_quota_usage(quota)
