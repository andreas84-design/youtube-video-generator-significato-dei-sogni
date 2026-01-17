import os
import base64
import json
import tempfile
import subprocess
import uuid
import datetime as dt
import requests
from flask import Flask, request, jsonify
import boto3
from botocore.config import Config
import math
import random
from threading import Thread
import logging
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# 🔧 Railway Variables
MAX_DURATION = int(os.getenv('MAX_DURATION', '3600'))
MAX_CONCURRENT = int(os.getenv('MAX_CONCURRENT', '5'))
MAX_CLIPS = int(os.getenv('MAX_CLIPS', '40'))
LOG_RATE = int(os.getenv('LOG_RATE', '100'))

app = Flask(__name__)

# Config R2 (S3 compatibile)
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME")
R2_PUBLIC_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL")
R2_REGION = os.environ.get("R2_REGION", "auto")
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")

# Pexels / Pixabay API
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

# ✅ ID FISSO SIGNIFICATO DEI SOGNI (SOSTITUISCI CON TUO SPREADSHEET_ID!)
SPREADSHEET_ID = "1okc3JU-dhnmHHFwuW39NClBNbvEeADOE4pzw1SE3HA4"

# 🔔 Webhook flusso 2 (Significato dei Sogni)
N8N_WEBHOOK_URL_FLUSSO2 = os.environ.get("N8N_WEBHOOK_URL_SIGNIFICATO_DEI_SOGNI_FLUSSO2", "https://andreas84.app.n8n.cloud/webhook/Significato-dei-sogni-flusso-2-workflow-b")

jobs = {}
MAX_JOBS = 50

def get_gspread_client():
    """Client Google Sheets per update Video_URL"""
    try:
        if not GOOGLE_CREDENTIALS_JSON:
            return None
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        return gspread.authorize(credentials)
    except Exception as e:
        logger.error(f"Google Sheets client error: {e}")
        return None

def get_s3_client():
    """Client S3 configurato per Cloudflare R2"""
    if R2_ACCOUNT_ID:
        endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    else:
        endpoint_url = None
    if endpoint_url is None:
        raise RuntimeError("Endpoint R2 non configurato: imposta R2_ACCOUNT_ID in Railway")
    
    session = boto3.session.Session()
    s3_client = session.client(
        service_name="s3",
        region_name=R2_REGION,
        endpoint_url=endpoint_url,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(s3={"addressing_style": "virtual"}),
    )
    return s3_client

def cleanup_old_videos(s3_client, current_key):
    """Cancella tutti i video MP4 in R2 TRANNE quello appena caricato"""
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix="videos/")
        deleted_count = 0
        for page in pages:
            if "Contents" not in page:
                continue
            for obj in page["Contents"]:
                key = obj["Key"]
                if key.endswith(".mp4") and key != current_key:
                    s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
                    deleted_count += 1
                    print(f"🗑️ Cancellato vecchio video: {key}", flush=True)
        if deleted_count > 0:
            print(f"✅ Rotazione completata: {deleted_count} video vecchi rimossi", flush=True)
        else:
            print("✅ Nessun video vecchio da rimuovere", flush=True)
    except Exception as e:
        print(f"⚠️ Errore rotazione R2 (video vecchi restano): {str(e)}", flush=True)

def notify_n8n_flusso2(job):
    """Invia webhook a n8n quando il job è completato."""
    if not N8N_WEBHOOK_URL_FLUSSO2:
        print("⚠️ N8N_WEBHOOK_URL_SIGNIFICATO_DEI_SOGNI_FLUSSO2 non configurata, skip webhook", flush=True)
        return

    try:
        payload = {
            "job_id": job.get("job_id"),
            "video_url": job.get("video_url"),
            "duration": job.get("duration"),
            "clips_used": job.get("clips_used"),
            "title": job.get("data", {}).get("title"),
            "description_pro": job.get("data", {}).get("description_pro"),
            "row_id": job.get("row_number") or job.get("data", {}).get("row_id"),
            "keywords": job.get("data", {}).get("keywords"),
            "playlist": job.get("data", {}).get("playlist"),
            "channel": "significato_sogni",
        }
        resp = requests.post(N8N_WEBHOOK_URL_FLUSSO2, json=payload, timeout=15)
        print(f"🔔 Webhook n8n flusso2 status={resp.status_code}", flush=True)
    except Exception as e:
        print(f"⚠️ Errore invio webhook n8n flusso2: {e}", flush=True)
# -------------------------------------------------
# Mapping SCENA → QUERY visiva (canale SIGNIFICATO DEI SOGNI)
# -------------------------------------------------
def pick_visual_query(context: str, keywords_text: str = "") -> str:
    """Query ottimizzate per B-roll SOGNI: atmosfera onirica, simboli, introspezione psicologica."""
    ctx = (context or "").lower()
    kw = (keywords_text or "").lower()
    
    base = "dreamlike night sky surreal clouds moonlight abstract shapes introspective person thinking"
    
    if any(w in ctx for w in ["ricorrent", "sempre lo stesso", "ripet", "loop"]):
        return "repeating corridor endless hallway looping doors abstract repetition surreal pattern"
    if any(w in ctx for w in ["ansia", "paura", "terror", "incubo", "angoscia", "panico"]):
        return "dark corridor shadowy figure dramatic lighting person stressed surreal nightmare atmosphere"
    if any(w in ctx for w in ["ex", "relazione", "amore", "partner", "fidanzat"]):
        return "couple silhouette at night distant people city lights person looking old photos emotional"
    if any(w in ctx for w in ["famiglia", "genitori", "madre", "padre", "figli", "bambin"]):
        return "family silhouettes warm home interior parents with child soft light nostalgic atmosphere"
    if any(w in ctx for w in ["morte", "lutto", "perdita", "funerale", "addio"]):
        return "lonely person cemetery sunset field person sitting alone bench reflective grief mood"
    if any(w in ctx for w in ["volare", "volo", "libertà", "libero"]):
        return "person on mountain top arms open birds in sky clouds airplane window wide open sky"
    if any(w in ctx for w in ["cadere", "vuoto", "precipitar", "crollo"]):
        return "view from high building down abstract falling shapes person looking down height dramatic"
    if any(w in ctx for w in ["inseguit", "scappare", "fuga", "scappando"]):
        return "person running at night dark alley light at end blurred motion tense chase atmosphere"
    if any(w in ctx for w in ["nudo", "nuda", "vergogna", "imbarazz", "esposto"]):
        return "person covering with blanket spotlight on stage crowd blurred shame concept"
    if any(w in ctx for w in ["mare", "acqua", "oceano", "onda", "tsunami", "pioggia"]):
        return "ocean waves slow motion underwater light rays rain on window calm stormy sea"
    if any(w in ctx for w in ["casa", "stanza", "porta", "corridoio", "scalinata"]):
        return "mysterious door light behind long corridor house interior shadows stairs low light"
    if any(w in ctx for w in ["cane", "gatto", "serpente", "animale", "leone", "ragno"]):
        return "animal silhouette fog close up eye animal surreal environment symbolic wildlife"
    if any(w in ctx for w in ["esame", "scuola", "università", "lavoro", "colloquio", "licenziato"]):
        return "student classroom stressed person office desk night alarm clock papers books"
    if any(w in ctx for w in ["sogno lucido", "lucidi", "controllo sogno", "consapevole"]):
        return "person floating space surreal landscape glowing moon mountains abstract geometric dream world"
    if any(w in ctx for w in ["trauma", "incident", "shock", "attacco"]):
        return "broken glass slow motion person sitting floor dark room dramatic shadow lighting"
    if any(w in ctx for w in ["archetipo", "jung", "ombra", "inconscio", "psiche"]):
        return "surreal human silhouette galaxy inside split face light shadow abstract psychological art"
    if any(w in ctx for w in ["premonit", "spiritual", "segnale", "universo", "destino"]):
        return "starry night sky person looking stars galaxy timelapse light beams from sky"
    
    if kw and kw != "none":
        return f"{kw}, dreamy night sky, surreal atmosphere, symbolic visuals, introspective person"
    
    return base

def is_sogni_video_metadata(video_data, source):
    """🔧 Filtro SOGNI - NO banned (sport/cucina/gaming/business) + atmosfera onirica"""
    dream_keywords = ["dream", "night", "sky", "clouds", "moon", "stars", "surreal", "abstract", 
                      "person", "thinking", "silhouette", "shadow", "light", "emotional", "reflective",
                      "water", "ocean", "corridor", "dark", "mysterious", "spiritual", "cosmic"]
    banned = ["football", "soccer", "basketball", "tennis", "sport", "workout", "gym", "fitness",
              "kitchen", "cooking", "recipe", "food", "restaurant", "party", "festival", "concert",
              "videogame", "gaming", "esports", "business", "stock market", "money", "dollar", "finance"]
    
    if source == "pexels":
        text = (video_data.get("description", "") + " " + " ".join(video_data.get("tags", []))).lower()
    else:
        text = " ".join(video_data.get("tags", [])).lower()
    
    dream_count = sum(1 for kw in dream_keywords if kw in text)
    has_banned = any(kw in text for kw in banned)
    
    if has_banned:
        status = "❌ BANNED"
    elif dream_count >= 1:
        status = f"✅ DREAM({dream_count})"
    else:
        status = f"⚠️ NEUTRAL(dream:{dream_count})"
    
    print(f"🔍 [{source}] '{text[:60]}...' → {status}", flush=True)
    return not has_banned

def download_file(url: str) -> str:
    tmp_clip = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    clip_resp = requests.get(url, stream=True, timeout=30)
    clip_resp.raise_for_status()
    for chunk in clip_resp.iter_content(chunk_size=1024 * 1024):
        if chunk:
            tmp_clip.write(chunk)
    tmp_clip.close()
    return tmp_clip.name

def fetch_clip_for_scene(scene_number: int, query: str, avg_scene_duration: float):
    """🎯 Canale SOGNI: B-roll onirico. Fallback Pixabay se Pexels 0."""
    target_duration = min(4.0, avg_scene_duration)
    
    def try_pexels():
        if not PEXELS_API_KEY:
            return None
        headers = {"Authorization": PEXELS_API_KEY}
        params = {
            "query": f"{query} dreamy night surreal abstract psychology",
            "orientation": "landscape",
            "per_page": 25,
            "page": random.randint(1, 3),
        }
        resp = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=20)
        if resp.status_code != 200:
            return None
        videos = resp.json().get("videos", [])
        dream_videos = [v for v in videos if is_sogni_video_metadata(v, "pexels")]
        print(f"🎯 Pexels: {len(videos)} totali → {len(dream_videos)} OK (no banned)", flush=True)
        if dream_videos:
            video = random.choice(dream_videos)
            for vf in video.get("video_files", []):
                if vf.get("width", 0) >= 1280:
                    return download_file(vf["link"])
        return None
    
    def try_pixabay():
        if not PIXABAY_API_KEY:
            return None
        params = {
            "key": PIXABAY_API_KEY,
            "q": f"{query} dreamy night surreal abstract psychology",
            "per_page": 25,
            "safesearch": "true",
            "min_width": 1280,
        }
        resp = requests.get("https://pixabay.com/api/videos/", params=params, timeout=20)
        if resp.status_code != 200:
            return None
        hits = resp.json().get("hits", [])
        for hit in hits:
            if is_sogni_video_metadata(hit, "pixabay"):
                videos = hit.get("videos", {})
                for quality in ["large", "medium", "small"]:
                    if quality in videos and "url" in videos[quality]:
                        return download_file(videos[quality]["url"])
        return None
    
    for source_name, func in [("Pexels", try_pexels), ("Pixabay", try_pixabay)]:
        try:
            path = func()
            if path:
                print(f"🎥 Scena {scene_number}: '{query[:40]}...' → {source_name} ✓", flush=True)
                return path, target_duration
        except Exception as e:
            print(f"⚠️ {source_name}: {e}", flush=True)
    
    print(f"⚠️ NO CLIP per scena {scene_number}: '{query}'", flush=True)
    return None, None

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "jobs": len(jobs)})

@app.route("/ffmpeg-test", methods=["GET"])
def ffmpeg_test():
    result = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    firstline = result.stdout.splitlines()[0] if result.stdout else "no output"
    return jsonify({"ffmpeg_output": firstline})

@app.route("/status/<job_id>", methods=["GET"])
def get_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    
    response = {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job.get("created_at")
    }
    if job['status'] == 'completed':
        response['video_url'] = job.get('video_url')
        response['duration'] = job.get('duration')
        response['clips_used'] = job.get('clips_used')
    elif job['status'] == 'failed':
        response['error'] = job.get('error')
    
    return jsonify(response)
def process_video_async(job_id, data):
    """Processa video in background thread"""
    job = jobs[job_id]
    job["status"] = "processing"
    job["job_id"] = job_id
    job["data"] = data
    
    audiopath = None
    audio_wav_path = None
    video_looped_path = None
    final_video_path = None
    scene_paths = []
    
    try:
        if not all([R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_PUBLIC_BASE_URL]):
            raise RuntimeError("Config R2 mancante")
        
        audiobase64 = data.get("audio_base64") or data.get("audiobase64")
        raw_script = (data.get("script") or data.get("script_chunk") or data.get("script_audio") or data.get("script_completo") or "")
        script = (" ".join(str(p).strip() for p in raw_script) if isinstance(raw_script, list) else str(raw_script).strip())
        raw_keywords = data.get("keywords", "")
        sheet_keywords = (", ".join(str(k).strip() for k in raw_keywords) if isinstance(raw_keywords, list) else str(raw_keywords).strip())
        
        row_number_raw = data.get("row_number")
        if isinstance(row_number_raw, dict):
            row_number = int(row_number_raw.get('row', row_number_raw.get('row_number', 1)))
        elif isinstance(row_number_raw, str):
            row_number = int(row_number_raw) if row_number_raw.isdigit() else 1
        elif isinstance(row_number_raw, (int, float)):
            row_number = int(row_number_raw)
        else:
            row_number = 1

        print("=" * 80, flush=True)
        print(f"✨ START SIGNIFICATO DEI SOGNI: {len(script)} char script, keywords: '{sheet_keywords}', row: {row_number}", flush=True)
        print(f"🔍 DEBUG row_number RAW: '{row_number_raw}' → PARSED: '{row_number}'", flush=True)
        
        if not audiobase64:
            raise RuntimeError("audiobase64 mancante")
        
        audio_bytes = base64.b64decode(audiobase64)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(audio_bytes)
        audiopath_tmp = f.name
        
        audio_wav_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        audio_wav_path = audio_wav_tmp.name
        audio_wav_tmp.close()
        
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", audiopath_tmp,
            "-acodec", "pcm_s16le", "-ar", "48000", audio_wav_path
        ], timeout=MAX_DURATION, check=True)
        os.unlink(audiopath_tmp)
        audiopath = audio_wav_path
        
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audiopath
        ], stdout=subprocess.PIPE, text=True, timeout=10)
        real_duration = float(probe.stdout.strip() or 720.0)
        print(f"⏱️ Durata audio: {real_duration/60:.1f}min ({real_duration:.0f}s)", flush=True)
        
        script_words = script.lower().split()
        words_per_second = (len(script_words) / real_duration if real_duration > 0 else 2.5)
        num_scenes = MAX_CLIPS
        avg_scene_duration = real_duration / num_scenes
        scene_assignments = []
        
        for i in range(num_scenes):
            if i % 10 == 0:
                print(f"🔧 Clip {i}/{num_scenes}", flush=True)
            timestamp = i * avg_scene_duration
            word_index = int(timestamp * words_per_second)
            scene_context = " ".join(script_words[word_index: word_index + 7]) if word_index < len(script_words) else "dreamlike night sky surreal clouds moonlight"
            scene_query = pick_visual_query(scene_context, sheet_keywords)
            scene_assignments.append({
                "scene": i + 1, "timestamp": round(timestamp, 1),
                "context": scene_context[:60], "query": scene_query[:80]
            })
        
        for assignment in scene_assignments:
            clip_path, clip_dur = fetch_clip_for_scene(
                assignment["scene"], assignment["query"], avg_scene_duration
            )
            if clip_path and clip_dur:
                scene_paths.append((clip_path, clip_dur))
        
        print(f"✅ CLIPS SCARICATE: {len(scene_paths)}/{num_scenes}", flush=True)
        if len(scene_paths) < 5:
            raise RuntimeError(f"Troppe poche clip: {len(scene_paths)}/{num_scenes}")
        
        normalized_clips = []
        for i, (clip_path, _dur) in enumerate(scene_paths):
            try:
                normalized_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                normalized_path = normalized_tmp.name
                normalized_tmp.close()
                subprocess.run([
                    "ffmpeg", "-y", "-loglevel", "error", "-i", clip_path,
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", normalized_path
                ], timeout=MAX_DURATION, check=True)
                if os.path.exists(normalized_path) and os.path.getsize(normalized_path) > 1000:
                    normalized_clips.append(normalized_path)
            except Exception:
                pass
        
        if not normalized_clips:
            raise RuntimeError("Nessuna clip normalizzata")
        
        def get_duration(p):
            out = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", p
            ], stdout=subprocess.PIPE, text=True, timeout=10).stdout.strip()
            return float(out or 4.0)
        
        total_clips_duration = sum(get_duration(p) for p in normalized_clips)
        concat_list_tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt")
        entries_written = 0
        MAX_CONCAT_ENTRIES = 150
        
        if total_clips_duration < real_duration and len(normalized_clips) > 1:
            loops_needed = math.ceil(real_duration / total_clips_duration)
            for _ in range(loops_needed):
                for norm_path in normalized_clips:
                    if entries_written >= MAX_CONCAT_ENTRIES:
                        break
                    concat_list_tmp.write(f"file '{norm_path}'\n")
                    entries_written += 1
                if entries_written >= MAX_CONCAT_ENTRIES:
                    break
        else:
            for norm_path in normalized_clips:
                concat_list_tmp.write(f"file '{norm_path}'\n")
                entries_written += 1
        
        concat_list_tmp.close()
        
        video_looped_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        video_looped_path = video_looped_tmp.name
        video_looped_tmp.close()
        
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", concat_list_tmp.name,
            "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-t", str(real_duration), video_looped_path
        ], timeout=MAX_DURATION, check=True)
        os.unlink(concat_list_tmp.name)
        
        final_video_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        final_video_path = final_video_tmp.name
        final_video_tmp.close()
        
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", video_looped_path, "-i", audiopath,
            "-filter_complex", "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p[v]",
            "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-shortest", final_video_path
        ], timeout=MAX_DURATION, check=True)
        
        s3_client = get_s3_client()
        today = dt.datetime.utcnow().strftime("%Y-%m-%d")
        object_key = f"videos/{today}/{uuid.uuid4().hex}.mp4"
        s3_client.upload_file(
            Filename=final_video_path,
            Bucket=R2_BUCKET_NAME,
            Key=object_key,
            ExtraArgs={"ContentType": "video/mp4"}
        )
        public_url = f"{R2_PUBLIC_BASE_URL.rstrip('/')}/{object_key}"
        cleanup_old_videos(s3_client, object_key)
        
        gc = get_gspread_client()
        print(f"🔍 DEBUG gspread client: {'OK' if gc else 'FAILED'}", flush=True)
        if gc and row_number > 0:
            try:
                sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
                sheet.update_cell(row_number, 13, public_url)
                sheet.update_cell(row_number, 2, "PRODOTTO")
                print(f"📊 ✅ Sheet row {row_number}: M={public_url[:60]} + B=PRODOTTO (anti-loop)", flush=True)
            except Exception as e:
                print(f"❌ Sheets fallito row {row_number}: {str(e)}", flush=True)
        
        paths_to_cleanup = [audiopath, video_looped_path, final_video_path] + normalized_clips + [p[0] for p in scene_paths]
        for path in paths_to_cleanup:
            try:
                os.unlink(path)
            except Exception:
                pass
        
        print(f"✅ ✨ VIDEO SIGNIFICATO DEI SOGNI COMPLETO: {real_duration/60:.1f}min → {public_url}", flush=True)
        
        job.update({
            "status": "completed",
            "video_url": public_url,
            "duration": real_duration,
            "clips_used": len(scene_paths),
            "row_number": row_number
        })

        notify_n8n_flusso2(job)
        
    except Exception as e:
        print(f"❌ ERRORE PROCESSING: {e}", flush=True)
        job.update({"status": "failed", "error": str(e)})
    
    finally:
        Thread(target=lambda: cleanup_job_delayed(job_id), daemon=True).start()

def cleanup_job_delayed(job_id, delay=3600):
    import time
    time.sleep(delay)
    if job_id in jobs:
        del jobs[job_id]

@app.route("/generate", methods=["POST"])
def generate():
    try:
        data = request.get_json(force=True) or {}
        job_id = str(uuid.uuid4())
        
        jobs[job_id] = {
            "status": "queued",
            "created_at": dt.datetime.utcnow().isoformat(),
            "data": data
        }
        
        if len(jobs) > MAX_JOBS:
            old_jobs = sorted(jobs.keys(), key=lambda k: jobs[k]["created_at"])[:len(jobs)-MAX_JOBS]
            for oj in old_jobs:
                del jobs[oj]
        
        Thread(target=process_video_async, args=(job_id, data), daemon=True).start()
        
        print(f"🚀 Job {job_id} QUEUED: raw_row={data.get('row_number')}", flush=True)
        return jsonify({
            "success": True,
            "job_id": job_id,
            "status": "queued",
            "message": "Video generation started (check /status/<job_id>)"
        })
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)
