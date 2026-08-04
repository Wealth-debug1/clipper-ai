# Kids Studio

Standalone service that generates AI kids' story videos (GPT story → fal.ai/gpt-image-1 LEGO-style scenes → Kling animation → ElevenLabs narration → ffmpeg render) and autonomously publishes them to YouTube Shorts, TikTok, Instagram Reels, and Facebook Reels.

Fully separate from the main `clipper-ai` app — its own backend (`main.py`, port 8002) and its own frontend (`frontend/`, port 3001). No shared imports with `backend/`.

## Run locally

Backend:
```bash
cd kids-studio
venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8002
```

Frontend:
```bash
cd kids-studio/frontend
npm install   # first time only
npm run dev   # http://localhost:3001
```

## How it works

- `POST /create-video?book_title=...&target_age=4-8` kicks off the generation pipeline as a background job (`GET /job/{id}` to poll progress).
- Finished videos land in `storage/kids_queue.json`, each with a `platforms` object tracking YouTube/TikTok/Instagram/Facebook independently (a video can post successfully to YouTube and fail on TikTok — they don't block each other).
- An APScheduler job (`autonomous_kids_cycle`, every 60 min) tops up the queue when it's low and attempts to publish the next unposted video to every configured platform, gated by daily per-platform quotas (`quota.py`) and peak-hour/human-pacing rules already in `main.py`.
- `GET /quota-status` shows per-platform daily usage and whether credentials are configured; `POST /post/{video_id}/{platform}` manually triggers or retries a single platform.
- A platform with no credentials configured is skipped cleanly (`status: "skipped_no_credentials"`) — nothing crashes waiting on the other three.

## One-time credential setup

Everything below requires a human clicking through account creation / OAuth consent — none of it can be automated. Nothing here blocks running the generation pipeline or the dashboard against unconfigured platforms; they'll just show as "not configured" until you add credentials.

### YouTube
1. Create a dedicated Google account + new YouTube channel for the kids brand. In YouTube Studio, mark the channel **Made for Kids**.
2. In Google Cloud Console, enable the YouTube Data API v3 and create an OAuth 2.0 Client ID of type **Desktop app**. Download the JSON as `kids-studio/kids_client_secret.json`.
3. Run the backend locally and trigger a post (e.g. `curl -X POST http://127.0.0.1:8002/post/{video_id}/youtube`) — this opens a browser for one-time consent and writes `kids-studio/kids_token.json`. Both files are gitignored; deploy `kids_token.json` alongside the service afterward, no need to redo consent.

### Cloudinary (needed before TikTok/Instagram/Facebook can post)
Sign up for a free-tier account dedicated to kids-studio, then set `KIDS_CLOUDINARY_CLOUD_NAME` / `KIDS_CLOUDINARY_API_KEY` / `KIDS_CLOUDINARY_API_SECRET` in `.env`.

### TikTok
Dedicated TikTok account → register a developer app at developers.tiktok.com → apply for Content Posting API access (expect a review delay before public posting is allowed) → OAuth for `video.publish` scope → `KIDS_TIKTOK_ACCESS_TOKEN`. Access tokens expire ~24h and this service doesn't auto-refresh yet — `KIDS_TIKTOK_REFRESH_TOKEN`/`CLIENT_KEY`/`CLIENT_SECRET` are reserved in `.env.example` for that fast-follow.

### Instagram
Dedicated Instagram Business/Creator account → Meta developer app using "Instagram API with Instagram Login" → long-lived access token + Business Account ID → `KIDS_INSTAGRAM_ACCESS_TOKEN` / `KIDS_INSTAGRAM_ACCOUNT_ID`. Expect Meta App Review for `instagram_content_publish` before this works beyond your own test accounts.

### Facebook
Dedicated Facebook Page → Page Access Token (ideally long-lived, via a Meta Business System User) with video-publish permissions → `KIDS_FACEBOOK_PAGE_ACCESS_TOKEN` / `KIDS_FACEBOOK_PAGE_ID`. Same Meta App Review caveat as Instagram — can often be requested together.

## Cost notes

- Scene images default to fal.ai FLUX Schnell (cheap) with `gpt-image-1` as fallback if `FAL_API_KEY` is unset or the request fails.
- Narration defaults to ElevenLabs `eleven_turbo_v2_5` (half the cost of the older monolingual v2 model).
- Story/scene text defaults to `gpt-5-mini`.
- Don't loop real end-to-end generations while testing — each one spends OpenAI/fal.ai/ElevenLabs/Kling API credit.
