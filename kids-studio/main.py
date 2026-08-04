from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import jwt
import requests
import uuid
import json
import os
import subprocess
import time
import random

from quota import can_use_platform, track_platform_post, track_platform_error
from social import (
    upload_to_youtube_kids,
    upload_to_tiktok_kids,
    upload_to_instagram_kids,
    upload_to_facebook_kids,
)




load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5-mini")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
FAL_API_KEY = os.getenv("FAL_API_KEY")
KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY")
KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "/usr/local/bin/ffmpeg")

client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI()

# --- STORAGE ---
# Anchored to this file's directory so behavior doesn't depend on the
# process's current working directory (matters once this runs under a
# process manager / deploy target instead of a local shell).
BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
STORIES_DIR = STORAGE_DIR / "stories"
SCENES_DIR = STORAGE_DIR / "scenes"
VOICEOVER_DIR = STORAGE_DIR / "voiceovers"
VIDEO_DIR = STORAGE_DIR / "videos"
QUEUE_FILE = STORAGE_DIR / "kids_queue.json"
HISTORY_FILE = STORAGE_DIR / "kids_history.json"

for d in [STORIES_DIR, SCENES_DIR, VOICEOVER_DIR, VIDEO_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Override with the deployed public URL once this is hosted; localhost
# only works while the API and its caller are on the same machine.
BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8002")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/videos", StaticFiles(directory=str(VIDEO_DIR)), name="videos")
app.mount("/scenes", StaticFiles(directory=str(SCENES_DIR)), name="scenes")

JOBS = {}

# --- POSTING STRATEGY ---
MAX_POSTS_PER_DAY = 2
PEAK_HOURS = [8, 15]  # 8am and 3pm — best for kids content


# --- STORAGE HELPERS ---

def load_queue():
    try:
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_to_queue(video):
    queue = load_queue()
    queue.append(video)
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)
    return queue

def load_history():
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_to_history(record):
    history = load_history()
    history.append(record)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# --- STEP 1: GPT STORY GENERATION ---

def generate_story_script(book_title: str, target_age: str = "4-8"):
    prompt = f"""
You are a children's story writer creating a fun, engaging short video script.

Book or story: {book_title}
Target age: {target_age}

Requirements:
- 60 to 90 seconds when narrated
- Simple, warm, playful language
- Strong opening that grabs kids attention
- Clear beginning, middle, end
- Positive message or lesson
- Fun and imaginative
- Optimized for YouTube Shorts / TikTok vertical video

Return ONLY valid JSON. No markdown. No code block.

Format:
{{
    "title": "...",
    "tagline": "...",
    "narration": "...",
    "moral": "...",
    "target_age": "{target_age}",
    "mood": "fun / magical / adventurous / heartwarming"
}}
"""
    response = client.chat.completions.create(
        model=OPENAI_TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


# --- STEP 2: SCENE BREAKDOWN ---

def generate_scene_breakdown(story: dict):
    prompt = f"""
You are a LEGO animation director creating a storyboard for a kids short video.

Story title: {story["title"]}
Narration: {story["narration"]}
Mood: {story["mood"]}

Requirements:
- 6 to 8 scenes
- LEGO minifigure characters throughout
- Bright, colorful, cheerful environments
- Each scene matches the narration timing
- Vertical composition (9:16)
- Cinematic but child-friendly
- Consistent main character appearance across all scenes
- Include camera angle and movement per scene

Return ONLY valid JSON.

Format:
{{
  "character_description": "LEGO minifigure with yellow face, blue outfit, brown hair",
  "scenes": [
    {{
      "scene": 1,
      "duration": 8,
      "visual": "detailed scene description for image generation",
      "camera": "wide shot / close up / medium shot",
      "narration_line": "the exact line being spoken in this scene",
      "mood": "happy / mysterious / exciting"
    }}
  ]
}}
"""
    response = client.chat.completions.create(
        model=OPENAI_TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


# --- STEP 3: SCENE IMAGE GENERATION ---
# Primary path is fal.ai FLUX: roughly 5-15x cheaper per image than
# gpt-image-1 (FAL_API_KEY was already in .env but never used — the
# code called gpt-image-1 directly and just labeled it "flux fallback").
# gpt-image-1 is kept as the fallback if FAL_API_KEY is unset or the
# request fails; note gpt-image-1 itself is slated for deprecation by
# OpenAI on 2026-10-23, so that fallback path has a shelf life too.

def _build_scene_prompt(scene: dict, character_description: str) -> str:
    return f"""
LEGO minifigure animation style.
Main character: {character_description}
Scene: {scene["visual"]}
Style: Bright colorful cinematic LEGO movie quality.
Vertical 9:16 composition.
Child-friendly cheerful lighting.
Highly detailed LEGO bricks and minifigures.
"""


def _save_scene_image(scene: dict, image_bytes: bytes) -> dict:
    filename = f"scene_{scene['scene']}_{uuid.uuid4().hex[:8]}.png"
    image_path = SCENES_DIR / filename

    with open(image_path, "wb") as f:
        f.write(image_bytes)

    print(f"IMAGE SAVED: {filename}")

    return {
        "scene": scene["scene"],
        "image_path": str(image_path),
        "image_url": f"{BASE_URL}/scenes/{filename}",
        "narration_line": scene["narration_line"],
        "duration": scene["duration"]
    }


def generate_scene_image_fal(scene: dict, character_description: str):
    if not FAL_API_KEY:
        return None
    try:
        response = requests.post(
            "https://fal.run/fal-ai/flux/schnell",
            headers={
                "Authorization": f"Key {FAL_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "prompt": _build_scene_prompt(scene, character_description),
                "image_size": "portrait_16_9",
                "num_images": 1,
                "enable_safety_checker": True
            },
            timeout=60
        )
        if response.status_code != 200:
            print("FAL ERROR:", response.status_code, response.text)
            return None

        images = response.json().get("images", [])
        if not images:
            print("FAL ERROR: no images returned")
            return None

        image_bytes = requests.get(images[0]["url"], timeout=30).content
        return _save_scene_image(scene, image_bytes)

    except Exception as e:
        print("FAL ERROR:", str(e))
        return None


def generate_scene_image_openai(scene: dict, character_description: str):
    try:
        response = client.images.generate(
            model="gpt-image-1",
            prompt=_build_scene_prompt(scene, character_description),
            size="1024x1536"
        )

        import base64
        image_bytes = base64.b64decode(response.data[0].b64_json)
        return _save_scene_image(scene, image_bytes)

    except Exception as e:
        print("IMAGE ERROR:", str(e))
        return None


def generate_scene_image_flux(scene: dict, character_description: str):
    """
    Generates a LEGO scene image. Tries fal.ai FLUX first (cheap, fast);
    falls back to OpenAI gpt-image-1 if FAL is unavailable or fails.
    """
    result = generate_scene_image_fal(scene, character_description)
    if result:
        return result
    print(f"SCENE {scene['scene']}: falling back to gpt-image-1")
    return generate_scene_image_openai(scene, character_description)


def generate_all_scene_images(scene_data: dict):
    character_description = scene_data.get(
        "character_description",
        "LEGO minifigure with yellow face, colorful outfit"
    )
    generated = []
    for scene in scene_data["scenes"]:
        print(f"GENERATING SCENE {scene['scene']}...")
        result = generate_scene_image_flux(scene, character_description)
        if result:
            generated.append(result)
        time.sleep(1)  # Rate limit buffer
    return generated


# --- STEP 4: KLING VIDEO GENERATION ---

def _kling_jwt() -> str:
    """
    Kling doesn't use a static API key — it signs a short-lived JWT from
    an access key / secret key pair (30 min max validity per their docs).
    """
    now = int(time.time())
    payload = {
        "iss": KLING_ACCESS_KEY,
        "exp": now + 1800,
        "nbf": now - 5
    }
    return jwt.encode(payload, KLING_SECRET_KEY, algorithm="HS256", headers={"alg": "HS256", "typ": "JWT"})


def animate_scene_kling(image_path: str, prompt: str, duration: int = 5):
    """
    Animates a scene image into a video clip using Kling AI.
    """
    if not KLING_ACCESS_KEY or not KLING_SECRET_KEY:
        print("KLING SKIPPED: KLING_ACCESS_KEY / KLING_SECRET_KEY not configured")
        return None
    try:
        headers = {
            "Authorization": f"Bearer {_kling_jwt()}",
            "Content-Type": "application/json"
        }

        # Convert image to base64
        import base64
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        # Kling's image2video only accepts a duration of exactly 5 or 10
        # seconds (confirmed live: any other value 400s with "duration
        # value 'X' is invalid") — but scene durations come from GPT's
        # scene breakdown and are arbitrary (6, 8, 9...). Snap to nearest.
        kling_duration = 5 if abs(duration - 5) <= abs(duration - 10) else 10

        payload = {
            "model": "kling-v1",
            "image": image_b64,
            "prompt": prompt,
            "duration": str(kling_duration),
            "aspect_ratio": "9:16",
            "cfg_scale": 0.5
        }

        response = requests.post(
            "https://api.klingai.com/v1/videos/image2video",
            headers=headers,
            json=payload
        )

        print("KLING STATUS:", response.status_code)
        result = response.json()

        task_id = result.get("data", {}).get("task_id")

        if not task_id:
            print("KLING ERROR: No task ID returned", result)
            return None

        # Poll for completion
        for attempt in range(30):
            time.sleep(10)
            poll_response = requests.get(
                f"https://api.klingai.com/v1/videos/image2video/{task_id}",
                headers=headers
            )
            poll_result = poll_response.json()
            status = poll_result.get("data", {}).get("task_status")
            print(f"KLING POLL {attempt + 1}: {status}")

            if status == "succeed":
                video_url = (
                    poll_result["data"]["task_result"]["videos"][0]["url"]
                )
                # Download video
                video_data = requests.get(video_url).content
                filename = f"scene_{uuid.uuid4().hex[:8]}.mp4"
                video_path = SCENES_DIR / filename
                with open(video_path, "wb") as f:
                    f.write(video_data)
                print(f"KLING VIDEO SAVED: {filename}")
                return str(video_path)

            if status == "failed":
                print("KLING FAILED:", poll_result)
                return None

        print("KLING TIMEOUT")
        return None

    except Exception as e:
        print("KLING ERROR:", str(e))
        return None


def animate_all_scenes(scenes: list):
    animated = []
    for scene in scenes:
        print(f"ANIMATING SCENE {scene['scene']}...")
        video_path = animate_scene_kling(
            image_path=scene["image_path"],
            prompt=f"Smooth gentle camera movement. LEGO animation style. {scene.get('narration_line', '')}",
            duration=scene.get("duration", 5)
        )
        if video_path:
            scene["video_path"] = video_path
            animated.append(scene)
        else:
            # Fallback: use static image as video
            print(f"KLING FAILED FOR SCENE {scene['scene']} — using static image fallback")
            scene["video_path"] = None
            animated.append(scene)
    return animated


# --- STEP 5: ELEVENLABS NARRATION ---

def generate_narration_elevenlabs(narration_text: str, voice_id: str = None):
    """
    Generates warm, expressive kids narration using ElevenLabs.
    Default voice: Rachel (warm and friendly)
    Good kids voices:
    - Rachel: 21m00Tcm4TlvDq8ikWAM
    - Callum: N2lVS1w4EtoT3dr4eOWO
    - Charlie: IKne3meq5aSn9XLyUdCD

    Model defaults to eleven_turbo_v2_5: half the per-character cost of
    eleven_monolingual_v2 (which this used to hardcode) at comparable
    quality for short-form narration. Override via ELEVENLABS_MODEL if
    eleven_v3's extra expressiveness is worth 2x the cost for a given run.
    """
    voice_id = voice_id or ELEVENLABS_VOICE_ID
    try:
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }

        payload = {
            "text": narration_text,
            "model_id": ELEVENLABS_MODEL,
            "voice_settings": {
                "stability": 0.75,
                "similarity_boost": 0.85,
                "style": 0.5,
                "use_speaker_boost": True
            }
        }

        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers=headers,
            json=payload
        )

        if response.status_code != 200:
            print("ELEVENLABS ERROR:", response.status_code, response.text)
            return None

        filename = f"narration_{uuid.uuid4().hex[:8]}.mp3"
        audio_path = VOICEOVER_DIR / filename

        with open(audio_path, "wb") as f:
            f.write(response.content)

        print(f"NARRATION SAVED: {filename}")
        return str(audio_path)

    except Exception as e:
        print("ELEVENLABS ERROR:", str(e))
        return None


# --- STEP 6: FFMPEG RENDER ---

def render_final_video(scenes: list, narration_path: str, title: str):
    """
    Stitches all scene videos + narration into final vertical Short.
    Falls back to static images if Kling video failed for any scene.
    """
    output_filename = f"kids_{uuid.uuid4().hex[:8]}.mp4"
    output_path = VIDEO_DIR / output_filename
    concat_file = VIDEO_DIR / f"concat_{uuid.uuid4().hex[:8]}.txt"

    with open(concat_file, "w") as f:
        for scene in scenes:
            if scene.get("video_path"):
                f.write(f"file '{Path(scene['video_path']).resolve()}'\n")
            else:
                # Use image as static video segment
                duration = scene.get("duration", 5)
                img_path = Path(scene["image_path"]).resolve()
                # Create a short video from the static image
                temp_video = VIDEO_DIR / f"temp_{uuid.uuid4().hex[:8]}.mp4"
                subprocess.run([
                    FFMPEG_BIN, "-y",
                    "-loop", "1",
                    "-i", str(img_path),
                    "-t", str(duration),
                    "-vf", "scale=720:1280",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    str(temp_video)
                ], check=True)
                f.write(f"file '{temp_video.resolve()}'\n")

    # Stitch video + narration
    command = [
        FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-i", narration_path,
        "-vf", "scale=720:1280",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        "-pix_fmt", "yuv420p",
        str(output_path)
    ]

    subprocess.run(command, check=True)
    print("FINAL VIDEO RENDERED:", output_filename)

    return {
        "video_file": output_filename,
        "video_path": str(output_path),
        "video_url": f"{BASE_URL}/videos/{output_filename}"
    }


# --- FULL PIPELINE ---

def create_kids_video_job(job_id: str, book_title: str, target_age: str = "4-8"):
    try:
        # Step 1: Generate story
        JOBS[job_id]["status"] = "writing_story"
        JOBS[job_id]["progress"] = 10
        print("STEP 1: Generating story...")
        story = generate_story_script(book_title, target_age)
        JOBS[job_id]["story"] = story

        # Step 2: Scene breakdown
        JOBS[job_id]["status"] = "planning_scenes"
        JOBS[job_id]["progress"] = 20
        print("STEP 2: Planning scenes...")
        scene_data = generate_scene_breakdown(story)

        # Step 3: Generate images with Flux
        JOBS[job_id]["status"] = "generating_images"
        JOBS[job_id]["progress"] = 35
        print("STEP 3: Generating LEGO images...")
        images = generate_all_scene_images(scene_data)

        if not images:
            raise Exception("No images were generated")

        # Step 4: Animate with Kling
        JOBS[job_id]["status"] = "animating_scenes"
        JOBS[job_id]["progress"] = 55
        print("STEP 4: Animating scenes...")
        animated_scenes = animate_all_scenes(images)

        # Step 5: Generate narration
        JOBS[job_id]["status"] = "generating_narration"
        JOBS[job_id]["progress"] = 75
        print("STEP 5: Generating narration...")
        narration_path = generate_narration_elevenlabs(story["narration"])

        if not narration_path:
            raise Exception("Narration generation failed")

        # Step 6: Render final video
        JOBS[job_id]["status"] = "rendering_video"
        JOBS[job_id]["progress"] = 90
        print("STEP 6: Rendering final video...")
        video = render_final_video(animated_scenes, narration_path, story["title"])

        # Save to queue for posting
        queue_item = {
            "id": job_id,
            "title": story["title"],
            "tagline": story["tagline"],
            "moral": story["moral"],
            "target_age": target_age,
            "video_path": video["video_path"],
            "video_url": video["video_url"],
            "created_at": datetime.now().isoformat(),
            "youtube_title": f"{story['title']} | LEGO Kids Story",
            "youtube_description": (
                f"{story['tagline']}\n\n"
                f"Moral: {story['moral']}\n\n"
                f"Perfect for ages {target_age}!\n\n"
                f"#LEGOStory #KidsVideo #BedtimeStory #Shorts"
            ),
            "hashtags": [
                "#LEGO", "#KidsStory", "#BedtimeStory",
                "#Shorts", "#KidsYouTube", "#LEGOMovie",
                "#ChildrensBooks", "#Storytime"
            ],
            "platforms": fresh_platform_status()
        }

        save_to_queue(queue_item)

        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["video"] = video
        JOBS[job_id]["story"] = story
        JOBS[job_id]["message"] = f"Video created: {story['title']}"

        print("KIDS VIDEO COMPLETE:", story["title"])

    except Exception as e:
        print("KIDS VIDEO JOB FAILED:", str(e))
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["error"] = str(e)


# --- MULTI-PLATFORM POSTING ---

PLATFORMS = ["youtube", "tiktok", "instagram", "facebook"]

PLATFORM_CONFIGURED = {
    "youtube": lambda: (BASE_DIR / "kids_client_secret.json").exists() or (BASE_DIR / "kids_token.json").exists(),
    "tiktok": lambda: bool(os.getenv("KIDS_TIKTOK_ACCESS_TOKEN")),
    "instagram": lambda: bool(os.getenv("KIDS_INSTAGRAM_ACCESS_TOKEN") and os.getenv("KIDS_INSTAGRAM_ACCOUNT_ID")),
    "facebook": lambda: bool(os.getenv("KIDS_FACEBOOK_PAGE_ACCESS_TOKEN") and os.getenv("KIDS_FACEBOOK_PAGE_ID")),
}

PLATFORM_UPLOADERS = {
    "youtube": lambda item: upload_to_youtube_kids(
        item["video_path"], item["youtube_title"], item["youtube_description"], item.get("hashtags", [])
    ),
    "tiktok": lambda item: upload_to_tiktok_kids(item["video_path"], item["youtube_title"]),
    "instagram": lambda item: upload_to_instagram_kids(item["video_path"], item["youtube_description"]),
    "facebook": lambda item: upload_to_facebook_kids(item["video_path"], item["youtube_description"]),
}


def fresh_platform_status() -> dict:
    return {
        platform: {
            "status": "pending",
            "post_id": None,
            "url": None,
            "posted_at": None,
            "error": None,
            "attempts": 0,
        }
        for platform in PLATFORMS
    }


def migrate_queue_item(item: dict) -> dict:
    """Backfills the per-platform status dict for any item saved before
    this schema existed (and drops the old single global status field,
    which can't represent "posted on YouTube, failed on TikTok")."""
    if "platforms" not in item:
        item["platforms"] = fresh_platform_status()
    item.pop("status", None)
    return item


def compute_publish_status(item: dict) -> str:
    statuses = [p["status"] for p in item["platforms"].values()]
    terminal = {"posted", "skipped_no_credentials"}
    if all(s in terminal for s in statuses):
        return "fully_posted"
    if any(s == "posted" for s in statuses):
        return "partially_posted"
    if any(s == "failed" for s in statuses) and not any(s == "pending" for s in statuses):
        return "posting_failed"
    return "unposted"


def get_posts_today():
    history = load_history()
    today = datetime.now().date()
    return sum(
        1 for item in history
        if item.get("posted_at") and
        datetime.fromisoformat(item["posted_at"]).date() == today
    )

def is_peak_hour():
    current_hour = datetime.now().hour
    for peak in PEAK_HOURS:
        if abs(current_hour - peak) <= 1:
            return True
    return False

def can_post():
    if get_posts_today() >= MAX_POSTS_PER_DAY:
        print(f"KIDS POST SKIPPED: Daily limit of {MAX_POSTS_PER_DAY} reached.")
        return False
    if not is_peak_hour():
        print("KIDS POST SKIPPED: Not peak hour.")
        return False
    if random.random() < 0.15:
        print("KIDS POST SKIPPED: Random human delay.")
        return False
    return True

def get_next_unposted_video():
    queue = load_queue()
    for item in queue:
        if compute_publish_status(item) != "fully_posted":
            return item
    return None


def attempt_post_to_platform(item: dict, platform: str) -> dict:
    """Mutates and returns item["platforms"][platform]. Never raises —
    every failure mode (no creds, quota, API error) becomes a terminal
    per-platform status so one platform can never block the others."""
    p = item["platforms"][platform]

    if not PLATFORM_CONFIGURED[platform]():
        p.update(status="skipped_no_credentials", error=None)
        return p

    if not can_use_platform(platform):
        p.update(status="skipped_quota", error=None)
        return p

    p["attempts"] += 1
    try:
        result = PLATFORM_UPLOADERS[platform](item)
        p.update(
            status="posted",
            post_id=result.get("post_id"),
            url=result.get("url"),
            posted_at=datetime.now().isoformat(),
            error=None,
        )
        track_platform_post(platform)
        save_to_history({
            "platform": platform,
            "post_id": result.get("post_id"),
            "title": item["youtube_title"],
            "video_id": item["id"],
            "posted_at": p["posted_at"],
        })
        print(f"KIDS POST COMPLETE [{platform}]:", result.get("post_id"))
    except Exception as e:
        p.update(status="failed", error=str(e))
        track_platform_error(platform, str(e))
        print(f"KIDS POST FAILED [{platform}]:", str(e))

    return p


def post_item_to_all_platforms(item_id: str) -> dict:
    queue = load_queue()
    item = next((v for v in queue if v.get("id") == item_id), None)
    if not item:
        raise ValueError(f"No queue item with id {item_id}")

    migrate_queue_item(item)
    for platform in PLATFORMS:
        attempt_post_to_platform(item, platform)

    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)
    return item


def post_next_video():
    if not can_post():
        return
    item = get_next_unposted_video()
    if not item:
        print("KIDS POST: No unposted videos in queue.")
        return

    print("KIDS POSTING:", item.get("youtube_title"))

    # Human-like delay
    delay = random.randint(30, 120)
    print(f"KIDS POST: Waiting {delay}s...")
    time.sleep(delay)

    post_item_to_all_platforms(item["id"])


# --- AUTONOMOUS CYCLE ---

BOOK_IDEAS = [
    "The Very Hungry Caterpillar",
    "Where the Wild Things Are",
    "Goodnight Moon",
    "The Gruffalo",
    "Dragons Love Tacos",
    "The Giving Tree",
    "Cloudy With a Chance of Meatballs",
    "Harold and the Purple Crayon",
    "If You Give a Mouse a Cookie",
    "The Lorax",
    "Green Eggs and Ham",
    "Curious George",
    "Chicka Chicka Boom Boom",
    "Pete the Cat",
    "The Very Busy Spider"
]

def autonomous_kids_cycle():
    print("=" * 40)
    print("KIDS CYCLE START:", datetime.now().isoformat())

    # Check queue — if low, create a new video
    try:
        queue = load_queue()
        unposted = [v for v in queue if compute_publish_status(migrate_queue_item(v)) != "fully_posted"]
        if len(unposted) < 2:
            print(f"KIDS QUEUE LOW ({len(unposted)} unposted) — creating new video...")
            # Pick a random book idea
            book_title = random.choice(BOOK_IDEAS)
            print("CREATING VIDEO FOR:", book_title)
            job_id = str(uuid.uuid4())
            JOBS[job_id] = {"status": "starting", "progress": 0}
            import threading
            threading.Thread(
                target=create_kids_video_job,
                args=(job_id, book_title),
                daemon=True
            ).start()
        else:
            print(f"KIDS QUEUE HEALTHY ({len(unposted)} unposted)")
    except Exception as e:
        print("KIDS CREATE ERROR:", str(e))

    # Post next video
    try:
        post_next_video()
    except Exception as e:
        print("KIDS POST ERROR:", str(e))

    print("KIDS CYCLE END:", datetime.now().isoformat())
    print("=" * 40)


# --- ROUTES ---

@app.get("/")
def home():
    return {"status": "Kids Studio running", "port": 8002}

@app.get("/health")
def health():
    queue = [migrate_queue_item(v) for v in load_queue()]
    statuses = [compute_publish_status(v) for v in queue]
    return {
        "status": "running",
        "queue_total": len(queue),
        "queue_unposted": sum(1 for s in statuses if s != "fully_posted"),
        "queue_fully_posted": sum(1 for s in statuses if s == "fully_posted"),
        "posts_today": get_posts_today(),
        "max_posts_per_day": MAX_POSTS_PER_DAY,
        "peak_hour_now": is_peak_hour(),
        "peak_hours": PEAK_HOURS,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/job/{job_id}")
def get_job(job_id: str):
    if job_id not in JOBS:
        return {"status": "not_found"}
    return JOBS[job_id]

@app.get("/queue")
def get_queue():
    queue = [migrate_queue_item(v) for v in load_queue()]
    for item in queue:
        item["publish_status"] = compute_publish_status(item)
    return {"queue": queue, "count": len(queue)}

@app.get("/queue/{video_id}")
def get_queue_item(video_id: str):
    queue = [migrate_queue_item(v) for v in load_queue()]
    item = next((v for v in queue if v.get("id") == video_id), None)
    if not item:
        return {"error": "not_found"}
    item["publish_status"] = compute_publish_status(item)
    return item

@app.get("/quota-status")
def quota_status():
    from quota import load_quota_usage
    usage = load_quota_usage()
    return {
        platform: {**usage[platform], "configured": PLATFORM_CONFIGURED[platform]()}
        for platform in PLATFORMS
    }

@app.post("/post/{video_id}/{platform}")
def post_to_platform(video_id: str, platform: str):
    if platform not in PLATFORMS:
        return {"error": f"unknown platform '{platform}', expected one of {PLATFORMS}"}
    queue = load_queue()
    item = next((v for v in queue if v.get("id") == video_id), None)
    if not item:
        return {"error": "not_found"}
    migrate_queue_item(item)
    result = attempt_post_to_platform(item, platform)
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)
    return result

@app.post("/create-video")
def create_video(book_title: str, target_age: str = "4-8"):
    import threading
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "starting",
        "progress": 0,
        "book_title": book_title,
        "target_age": target_age
    }
    threading.Thread(
        target=create_kids_video_job,
        args=(job_id, book_title, target_age),
        daemon=True
    ).start()
    return {
        "job_id": job_id,
        "status": "processing",
        "book_title": book_title
    }

@app.post("/post-next")
def post_next():
    post_next_video()
    return {"status": "post_attempted"}

@app.post("/auto-run")
def auto_run():
    import threading
    threading.Thread(target=autonomous_kids_cycle, daemon=True).start()
    return {"status": "cycle_started"}


##### SCHEDULER #####

scheduler = BackgroundScheduler()
scheduler.add_job(autonomous_kids_cycle, "interval", minutes=60)
scheduler.start()

print("KIDS STUDIO SCHEDULER STARTED")
