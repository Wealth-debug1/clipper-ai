"""
Upload adapters for kids-studio's four publish targets. Adapted from
backend/services/social.py's proven patterns, but kept fully independent
(own env vars, own credential files, own Cloudinary account) so this
service has zero import dependency on the main backend.

Credential-presence checks and quota gating deliberately live in
main.py's orchestrator, not here — these functions assume they're only
called when creds are known-present and quota allows it, and raise on
genuine API failure so the caller can record a real error.
"""
from pathlib import Path
import os
import time

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from quota import track_cloudinary_upload

BASE_DIR = Path(__file__).resolve().parent
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


# --- CLOUDINARY (public-URL intermediary for TikTok/Instagram/Facebook) ---

def upload_to_cloudinary_kids(file_path: str) -> str:
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=os.getenv("KIDS_CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("KIDS_CLOUDINARY_API_KEY"),
        api_secret=os.getenv("KIDS_CLOUDINARY_API_SECRET"),
    )
    response = cloudinary.uploader.upload(file_path, resource_type="video", folder="kids_studio")
    track_cloudinary_upload()
    return response["secure_url"]


# --- YOUTUBE ---

def get_youtube_service_kids():
    token_file = BASE_DIR / "kids_token.json"
    client_secret_file = BASE_DIR / "kids_client_secret.json"
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), YOUTUBE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif client_secret_file.exists():
            # Interactive consent only works with a local browser — see
            # kids-studio/README.md for the one-time setup flow.
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=0)
        else:
            raise RuntimeError(
                "YouTube not authorized: missing kids_token.json and "
                "kids_client_secret.json in kids-studio/. Run this service "
                "locally once with kids_client_secret.json present to "
                "complete OAuth consent and generate kids_token.json."
            )
        with open(token_file, "w") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_to_youtube_kids(video_path: str, title: str, description: str, tags=None) -> dict:
    youtube = get_youtube_service_kids()
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags or [],
                "categoryId": "1",  # Film & Animation
            },
            "status": {
                "privacyStatus": "public",
                # Required for a channel publishing children's content —
                # affects comments, personalized ads, and notifications
                # per YouTube's Made for Kids rules. The channel itself
                # must also be marked "Made for Kids" in YouTube Studio.
                "selfDeclaredMadeForKids": True,
            },
        },
        media_body=MediaFileUpload(video_path, resumable=True),
    )
    response = request.execute()
    video_id = response.get("id")
    return {"post_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}"}


# --- TIKTOK ---

def upload_to_tiktok_kids(video_path: str, title: str) -> dict:
    access_token = os.getenv("KIDS_TIKTOK_ACCESS_TOKEN")
    if not access_token:
        raise RuntimeError("KIDS_TIKTOK_ACCESS_TOKEN not configured")

    cloudinary_url = upload_to_cloudinary_kids(video_path)

    response = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "title": title[:150],  # TikTok hard cap
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": cloudinary_url,
            },
        },
        timeout=30,
    )
    data = response.json()
    error = data.get("error", {})
    if error.get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok upload failed: {error.get('message', data)}")

    publish_id = data["data"]["publish_id"]
    return {"post_id": publish_id, "url": None, "cloudinary_url": cloudinary_url}


# --- INSTAGRAM ---

def upload_to_instagram_kids(video_path: str, caption: str) -> dict:
    access_token = os.getenv("KIDS_INSTAGRAM_ACCESS_TOKEN")
    account_id = os.getenv("KIDS_INSTAGRAM_ACCOUNT_ID")
    if not access_token or not account_id:
        raise RuntimeError("KIDS_INSTAGRAM_ACCESS_TOKEN / KIDS_INSTAGRAM_ACCOUNT_ID not configured")

    cloudinary_url = upload_to_cloudinary_kids(video_path)

    container_response = requests.post(
        f"https://graph.instagram.com/v21.0/{account_id}/media",
        data={
            "media_type": "REELS",
            "video_url": cloudinary_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token,
        },
    )
    container_data = container_response.json()
    if "id" not in container_data:
        raise RuntimeError(f"Instagram container creation failed: {container_data}")
    container_id = container_data["id"]

    for _ in range(30):
        time.sleep(10)
        status_response = requests.get(
            f"https://graph.instagram.com/v21.0/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
        )
        status_code = status_response.json().get("status_code")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise RuntimeError("Instagram video processing failed")
    else:
        raise RuntimeError("Instagram video processing timed out")

    publish_response = requests.post(
        f"https://graph.instagram.com/v21.0/{account_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
    )
    publish_data = publish_response.json()
    if "id" not in publish_data:
        raise RuntimeError(f"Instagram publish failed: {publish_data}")

    return {"post_id": publish_data["id"], "url": None, "cloudinary_url": cloudinary_url}


# --- FACEBOOK ---

def upload_to_facebook_kids(video_path: str, caption: str) -> dict:
    page_token = os.getenv("KIDS_FACEBOOK_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("KIDS_FACEBOOK_PAGE_ID")
    if not page_token or not page_id:
        raise RuntimeError("KIDS_FACEBOOK_PAGE_ACCESS_TOKEN / KIDS_FACEBOOK_PAGE_ID not configured")

    cloudinary_url = upload_to_cloudinary_kids(video_path)

    start_response = requests.post(
        f"https://graph.facebook.com/v21.0/{page_id}/video_reels",
        data={"upload_phase": "start", "access_token": page_token},
    )
    start_data = start_response.json()
    video_id = start_data.get("video_id")
    if not video_id:
        raise RuntimeError(f"Facebook upload init failed: {start_data}")

    finish_response = requests.post(
        f"https://graph.facebook.com/v21.0/{page_id}/video_reels",
        data={
            "upload_phase": "finish",
            "video_id": video_id,
            "file_url": cloudinary_url,
            "description": caption,
            "published": "true",
            "access_token": page_token,
        },
    )
    finish_data = finish_response.json()
    if not finish_data.get("success", True):
        raise RuntimeError(f"Facebook upload finish failed: {finish_data}")

    return {
        "post_id": video_id,
        "url": f"https://www.facebook.com/{page_id}/videos/{video_id}",
        "cloudinary_url": cloudinary_url,
    }
