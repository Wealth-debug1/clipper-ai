
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import mediapipe as mp
import uuid
import cv2
import threading
import shutil
import subprocess
import requests
import time
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

app.mount("/clips", StaticFiles(directory="../storage/clips"), name="clips")
app.mount("/transcripts", StaticFiles(directory="../storage/transcripts"), name="transcripts")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
CLIP_DIR = BASE_DIR / "storage" / "clips"
AUDIO_DIR = BASE_DIR / "storage" / "audio"
TRANSCRIPT_DIR = BASE_DIR / "storage" / "transcripts"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CLIP_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

JOBS = {}

@app.get("/")
def home():
    return {"message": "Clipper AI backend is running"}


def extract_audio(video_path: Path, audio_path: Path):
    command = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "mp3",
        str(audio_path)
    ]
    subprocess.run(command, check=True)


def upload_audio_to_assemblyai(audio_path: Path):
    headers = {"authorization": ASSEMBLYAI_API_KEY}

    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=audio_file
        )

    response.raise_for_status()
    return response.json()["upload_url"]


def request_transcription(audio_url: str):
    headers = {
        "authorization": ASSEMBLYAI_API_KEY,
        "content-type": "application/json"
    }

    data = {
    "audio_url": audio_url,
    "speech_models": ["universal-3-pro", "universal-2"],
    "word_boost": [],
    "punctuate": True,
    "format_text": True
}

    response = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        json=data,
        headers=headers
    )

    response.raise_for_status()
    return response.json()["id"]


def poll_transcription(transcript_id: str):
    headers = {"authorization": ASSEMBLYAI_API_KEY}
    url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"

    while True:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        result = response.json()

        if result["status"] == "completed":
            return result

        if result["status"] == "error":
            raise Exception(result.get("error", "Transcription failed"))

        time.sleep(1)


def detect_viral_clips(transcript_text: str):
    prompt = f"""
Return ONLY valid JSON.

Format:
[
  {{
    "clip_number": 1,
    "title": "...",
    "hook": "...",
    "start_time_estimate": "...",
    "end_time_estimate": "...",
    "reason": "...",
    "virality_score": 1
  }}
]

Find ONLY 1 viral short clip from this transcript.
The clip MUST be between 20 and 35 seconds long.
Do NOT return clips longer than 35 seconds.

Transcript:
{transcript_text}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You ONLY return valid JSON. No text."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )

    content = response.choices[0].message.content.strip()

    print("RAW AI RESPONSE:", content)  # debug

    try:
        return json.loads(content)
    except:
        return [{"error": "AI did not return valid JSON", "raw": content}]

def time_to_seconds(time_str: str):
    parts = time_str.split(":")

    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])

    return float(time_str)

def get_face_crop_x(video_path: Path, start_time: str, end_time: str):
    cap = cv2.VideoCapture(str(video_path))

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    start_seconds = time_to_seconds(start_time)
    end_seconds = time_to_seconds(end_time)

    start_frame = int(start_seconds * fps)
    end_frame = int(end_seconds * fps)

    crop_width = int(height * 9 / 16)

    if crop_width > width:
        cap.release()
        return 0

    face_centers = []

    # 1) Try MediaPipe first
    mp_face = mp.solutions.face_detection

    with mp_face.FaceDetection(
        model_selection=0,
        min_detection_confidence=0.25
    ) as face_detection:
        frame_index = start_frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        while frame_index < end_frame:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_index % 3 == 0:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = face_detection.process(rgb_frame)

                if results.detections:
                    detection = results.detections[0]
                    bbox = detection.location_data.relative_bounding_box
                    face_center_x = int((bbox.xmin + bbox.width / 2) * width)
                    face_centers.append(face_center_x)

            frame_index += 1

    # 2) Fallback: OpenCV Haar face detection
    if not face_centers:
        haar_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(haar_path)

        frame_index = start_frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        while frame_index < end_frame:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_index % 3 == 0:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                faces = face_cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=3,
                    minSize=(30, 30)
                )

                if len(faces) > 0:
                    # choose largest face
                    x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
                    face_center_x = x + w // 2
                    face_centers.append(face_center_x)

            frame_index += 1

    cap.release()

    if not face_centers:
        return int((width - crop_width) / 2)

    average_face_x = int(sum(face_centers) / len(face_centers))
    crop_x = average_face_x - crop_width // 2
    crop_x = max(0, min(crop_x, width - crop_width))

    return crop_x

def cut_video_clip(video_path: Path, start_time: str, end_time: str, output_path: Path):
    command = [
        "ffmpeg",
        "-y",
        "-ss", start_time,
        "-to", end_time,
        "-i", str(video_path),
        "-vf", "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(output_path)
    ]

    subprocess.run(command, check=True)



def create_clips_from_ai(video_path: Path, clips, words):
    created_clips = []

    for clip in clips[:1]:
        if "error" in clip:
            continue

        clip_number = clip.get("clip_number", len(created_clips) + 1)
        start_time = "00:00"
        end_time = "00:30"

        if not start_time or not end_time:
            continue

        output_filename = f"clip_{clip_number}.mp4"
        output_path = CLIP_DIR / output_filename

        srt_filename = f"clip_{clip_number}.srt"
        srt_path = CLIP_DIR / srt_filename

        generate_srt_for_clip(words, start_time, end_time, srt_path)

        cut_video_clip(video_path, start_time, end_time, output_path)

        created_clips.append({
            "caption_file": srt_filename,
            "caption_url": f"http://127.0.0.1:8001/clips/{srt_filename}",
            "clip_number": clip_number,
            "title": clip.get("title"),
            "hook": clip.get("hook"),
            "start_time": start_time,
            "end_time": end_time,
            "output_file": output_filename,
            "output_path": str(output_path),
            "clip_url": f"http://127.0.0.1:8001/clips/{output_filename}",
            "virality_score": clip.get("virality_score")
        })

    return created_clips


def create_subtitle_file(transcript_text: str, output_srt: Path):
    lines = transcript_text.split(". ")

    with open(output_srt, "w") as f:
        for i, line in enumerate(lines[:15]):
            start = i * 2
            end = start + 2

            f.write(f"{i+1}\n")
            f.write(f"00:00:{start:02d},000 --> 00:00:{end:02d},000\n")
            f.write(line.strip() + "\n\n")
def time_to_ms(time_str: str):
    parts = time_str.split(":")

    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    elif len(parts) == 2:
        hours = 0
        minutes = int(parts[0])
        seconds = float(parts[1])
    else:
        hours = 0
        minutes = 0
        seconds = float(parts[0])

    return int((hours * 3600 + minutes * 60 + seconds) * 1000)

def ms_to_srt_time(ms: int):
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    milliseconds = ms % 1000

    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def generate_srt_for_clip(words, start_time: str, end_time: str, output_srt: Path):
    start_ms = time_to_ms(start_time)
    end_ms = time_to_ms(end_time)

    clip_words = [
        word for word in words
        if word.get("start", 0) >= start_ms and word.get("end", 0) <= end_ms
    ]

    caption_groups = []
    group = []

    for word in clip_words:
        group.append(word)

        if len(group) >= 5:
            caption_groups.append(group)
            group = []

    if group:
        caption_groups.append(group)

    with open(output_srt, "w") as f:
        for index, group in enumerate(caption_groups, start=1):
            group_start = group[0]["start"] - start_ms
            group_end = group[-1]["end"] - start_ms
            text = " ".join([w["text"] for w in group])

            f.write(f"{index}\n")
            f.write(f"{ms_to_srt_time(group_start)} --> {ms_to_srt_time(group_end)}\n")
            f.write(text + "\n\n")
def process_video_job(job_id, video_path, filename):
    try:
        JOBS[job_id]["status"] = "extracting_audio"
        JOBS[job_id]["progress"] = 20

        audio_filename = Path(filename).stem + ".mp3"
        audio_path = AUDIO_DIR / audio_filename

        transcript_filename = Path(filename).stem + ".txt"
        transcript_path = TRANSCRIPT_DIR / transcript_filename

        extract_audio(video_path, audio_path)

        JOBS[job_id]["status"] = "transcribing"
        JOBS[job_id]["progress"] = 40

        audio_url = upload_audio_to_assemblyai(audio_path)
        transcript_id = request_transcription(audio_url)
        transcript_result = poll_transcription(transcript_id)

        transcript_text = transcript_result.get("text", "")
        words = transcript_result.get("words", [])

        with open(transcript_path, "w") as transcript_file:
            transcript_file.write(transcript_text)

        words_filename = Path(filename).stem + "_words.json"
        words_path = TRANSCRIPT_DIR / words_filename
        words_url = f"http://127.0.0.1:8001/transcripts/{words_filename}"

        with open(words_path, "w") as words_file:
            json.dump(words, words_file)

        JOBS[job_id]["status"] = "finding_clips"
        JOBS[job_id]["progress"] = 70

        clips = detect_viral_clips(transcript_text)

        JOBS[job_id]["status"] = "creating_clips"
        JOBS[job_id]["progress"] = 90

        created_clips = create_clips_from_ai(video_path, clips, words)

        for clip in created_clips:
            clip["words_url"] = words_url

        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["clips"] = created_clips
        JOBS[job_id]["message"] = "Clips generated successfully"

    except Exception as e:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["error"] = str(e)

@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())

    video_path = UPLOAD_DIR / file.filename

    with open(video_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    JOBS[job_id] = {
        "status": "uploaded",
        "progress": 10,
        "clips": [],
        "message": "Video uploaded"
    }

    thread = threading.Thread(
        target=process_video_job,
        args=(job_id, video_path, file.filename)
    )
    thread.start()

    return {
        "job_id": job_id,
        "status": "processing",
        "progress": 10
    }

@app.get("/job/{job_id}")
def get_job(job_id: str):
    if job_id not in JOBS:
        return {
            "status": "not_found",
            "progress": 0,
            "error": "Job not found"
        }

    return JOBS[job_id]

