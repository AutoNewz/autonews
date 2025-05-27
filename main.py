import os
import random
import aiohttp
import aiofiles
import asyncio
import cv2
from gtts import gTTS
from pydub import AudioSegment
from pydub.utils import mediainfo
import subprocess
import warnings
from datetime import datetime
import time

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- SETUP FFMPEG WITHOUT ENV FILE ---
ffmpeg_dir = os.path.join(os.getcwd(), "ffmpeg", "bin")
ffmpeg_path = os.path.join(ffmpeg_dir, "ffmpeg.exe")
ffprobe_path = os.path.join(ffmpeg_dir, "ffprobe.exe")

os.environ["PATH"] += os.pathsep + ffmpeg_dir
AudioSegment.converter = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path
warnings.filterwarnings("ignore", category=RuntimeWarning)

def get_mp3_duration(file_path):
    info = mediainfo(file_path)
    return float(info['duration'])

# --- CONFIG ---
NEWSAPI_KEY = 'ae4bf89b00b64c469014eef6fd52065c'
UNSPLASH_KEY = 'xsSZtq9FLMX9JN9Z3GNkPWt74GaXzVf4ZZ94CxJQJ7c'
NEWS_URL = f"https://newsapi.org/v2/top-headlines?country=us&apiKey={NEWSAPI_KEY}"
IMAGE_DIR = "images"
OUTPUT_FILE = "final_news_video.mp4"
FINAL_OUTPUT_FILE = "final_news_video_with_audio.mp4"
FPS = 24

os.makedirs(IMAGE_DIR, exist_ok=True)

# --- GOOGLE OAUTH CONFIG ---
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "token.json"

CLIENT_CONFIG = {
    "installed": {
        "client_id": "838330170826-cmqt9rt19tv2j0v4ktj9q2po2upbh569.apps.googleusercontent.com",
        "client_secret": "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",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
    }
}

async def fetch_json(session, url):
    async with session.get(url) as response:
        return await response.json()

async def download_image(session, url, filepath):
    async with session.get(url) as resp:
        if resp.status == 200:
            async with aiofiles.open(filepath, 'wb') as f:
                await f.write(await resp.read())
            return filepath
        return None

async def process_article(session, article, index):
    title = article.get('title', 'No Title')
    first_word = title.split()[0] if title else "news"
    unsplash_url = f"https://api.unsplash.com/search/photos?query={first_word}&client_id={UNSPLASH_KEY}"
    image_data = await fetch_json(session, unsplash_url)
    results = image_data.get('results', [])
    if results:
        chosen = random.choice(results)
        image_url = chosen['urls']['regular']
        filepath = os.path.join(IMAGE_DIR, f"news_{index}.jpg")
        return await download_image(session, image_url, filepath)
    return None

async def fetch_and_download_all():
    async with aiohttp.ClientSession() as session:
        news_data = await fetch_json(session, NEWS_URL)
        articles = news_data.get('articles', [])[:5]
        tasks = [process_article(session, art, i+1) for i, art in enumerate(articles)]
        images = await asyncio.gather(*tasks)
        return articles[:5], images

def text_to_speech(text, filename):
    tts = gTTS(text=text, lang='en')
    tts.save(filename)

def generate_narration(articles):
    audio_files = []
    for i, art in enumerate(articles, 1):
        title = art.get('title', '')
        desc = art.get('description', 'No description available.')
        text = f"Title: {title}. Description: {desc}"
        file_path = f"narration_{i}.mp3"
        text_to_speech(text, file_path)
        audio_files.append(file_path)

    combined = AudioSegment.empty()
    durations = []
    for i, af in enumerate(audio_files):
        segment = AudioSegment.from_mp3(af)
        durations.append(segment.duration_seconds)
        combined += segment
        if i < len(audio_files) - 1:
            combined += AudioSegment.silent(duration=3000)

    combined.export("final_narration.mp3", format="mp3")
    return durations

def create_video(image_paths, audio_durations):
    height, width = 720, 1280
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(OUTPUT_FILE, fourcc, FPS, (width, height))
    for i, img_path in enumerate(image_paths):
        if not img_path:
            continue
        img = cv2.imread(img_path)
        if img is None:
            continue
        img = cv2.resize(img, (width, height))
        frame_count = int((audio_durations[i] + 3) * FPS)
        for _ in range(frame_count):
            out.write(img)
    out.release()

def merge_audio_video(video_path, audio_path, output_path):
    cmd = [
        ffmpeg_path, "-y", "-i", video_path, "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-strict", "experimental", output_path
    ]
    subprocess.run(cmd, check=True)

def get_authenticated_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
            # Changed here: use run_local_server instead of run_console
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    youtube = build('youtube', 'v3', credentials=creds)
    return youtube

def upload_video(youtube, file, title, description, tags=None, categoryId="25", privacyStatus="public"):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or ["news", "auto-generated", "python"],
            "categoryId": categoryId,
        },
        "status": {
            "privacyStatus": privacyStatus,
        }
    }
    media = MediaFileUpload(file, chunksize=-1, resumable=True, mimetype="video/*")

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    print(f"Upload Complete! Video ID: {response['id']}")
    return response['id']

async def main():
    print("Fetching news and images...")
    articles, image_paths = await fetch_and_download_all()
    print("Generating narration...")
    durations = generate_narration(articles)
    print("Creating video from images...")
    create_video(image_paths, durations)
    print("Merging audio and video...")
    merge_audio_video(OUTPUT_FILE, "final_narration.mp3", FINAL_OUTPUT_FILE)

    # Prepare title and description with date/time
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    title = f"Auto News || Date : {date_str}"
    description = (
        f"This video is generated by a python code with help of newsapi.org and unsplash.com. "
        f"Date of upload : {date_str} and Time of upload : {time_str}.\n\n"
        f"Subscribe and hit the bell icon to receive notification when uploaded"
    )

    print("Authenticating YouTube API...")
    youtube = get_authenticated_service()
    print("Uploading video to YouTube...")
    upload_video(youtube, FINAL_OUTPUT_FILE, title, description)

if __name__ == "__main__":
   while True:
        asyncio.run(main())
        time.sleep(86400)