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
                    print(f"🗑️  Cancellato vecchio video: {key}", flush=True)

        if deleted_count > 0:
            print(f"✅ Rotazione completata: {deleted_count} video vecchi rimossi", flush=True)
        else:
            print("✅ Nessun video vecchio da rimuovere", flush=True)

    except Exception as e:
        print(f"⚠️  Errore rotazione R2 (video vecchi restano): {str(e)}", flush=True)


# -------------------------------------------------
# Mapping SCENA → QUERY visiva (canale Investimenti e Finanza Facile)
# -------------------------------------------------
def pick_visual_query(context: str, keywords_text: str = "") -> str:
    """
    Query ottimizzate per B‑roll Finanza Personale:
    grafici, città finanziarie, calcoli, risparmi, famiglia serena, pianificazione.
    """
    ctx = (context or "").lower()
    kw = (keywords_text or "").lower()

    base = "financial charts data analysis, business growth graphs, investment planning, money savings"

    # ETF / Investimenti / Azioni / Obbligazioni
    if any(w in ctx for w in ["etf", "investiment", "azion", "obbligaz", "portafogli", "diversific"]):
        return "stock market charts rising, financial graphs business analysis, investment portfolio laptop screen"

    # Risparmio / Budget / Soldi
    if any(w in ctx for w in ["risparmio", "budget", "soldi", "denaro", "euro", "risorse", "finanze"]):
        return "person saving money piggy bank, budget planning calculator notebook, coins euro bills counting"

    # Pensione / Previdenza / Futuro / TFR
    if any(w in ctx for w in ["pension", "previdenz", "futuro", "tfr", "vecchiaia", "anzian"]):
        return "elderly couple planning retirement happy, pension savings calculator, senior people relaxed financial freedom"

    # Broker / Piattaforma / App / Trading
    if any(w in ctx for w in ["broker", "piattaforma", "app", "trading", "compravendita", "acquist"]):
        return "person using financial app smartphone, stock trading platform laptop, online banking mobile interface"

    # Fiscalità / Tasse / Dichiarazione / Detrazioni
    if any(w in ctx for w in ["fiscalit", "tasse", "dichiaraz", "detrazion", "fiscal", "tribut"]):
        return "tax forms documents calculator, person filling tax declaration paper, accountant reviewing financial documents"

    # Conto deposito / Banca / Interessi
    if any(w in ctx for w in ["conto", "banca", "deposit", "interest", "rendiment", "guadagn"]):
        return "bank building modern architecture, savings account interest growth chart, person checking bank statement laptop"

    # Carte credito / Cashback / Pagamenti
    if any(w in ctx for w in ["carta", "cashback", "pagament", "credit", "bancomat", "pos"]):
        return "credit card payment contactless, cashback rewards shopping, person using card terminal store"

    # Robo-advisor / Automatico / Tecnologia
    if any(w in ctx for w in ["robo", "automat", "tecnolog", "digital", "AI", "algoritm"]):
        return "AI technology financial automation, digital investment platform interface, automated trading algorithms visualization"

    # Famiglia / Casa / Vita quotidiana
    if any(w in ctx for w in ["famigli", "casa", "figli", "coppia", "genitori", "quotidian"]):
        return "happy family home financial planning, couple reviewing budget documents together, parents saving for children future"

    # Crescita / Rendimento / Performance / Guadagno
    if any(w in ctx for w in ["crescita", "rendiment", "performance", "guadagn", "profit", "ritorni"]):
        return "rising financial graph success, business growth chart upward trend, profit increase stock market screen"

    # Errori / Rischi / Perdite / Attenzione
    if any(w in ctx for w in ["error", "rischi", "perdita", "attenzione", "pericol", "evitar"]):
        return "warning sign financial risk, stock market crash red charts, person worried looking at declining graph"

    # Strategia / Piano / Obiettivi
    if any(w in ctx for w in ["strategi", "piano", "obiettiv", "meta", "pianific", "progett"]):
        return "business strategy planning board, financial goals checklist, person writing investment plan notebook"

    # Giovani / Millennials / Principianti / Iniziare
    if any(w in ctx for w in ["giovan", "millennial", "principiant", "iniziar", "cominciar", "primo"]):
        return "young person learning investment smartphone, millennial planning finances laptop cafe, beginner reading financial book"

    # Libertà finanziaria / Indipendenza / Passive income
    if any(w in ctx for w in ["libertà", "indipendenz", "passive", "rendita", "autonomi"]):
        return "person relaxing financial freedom beach, passive income laptop remote work, independent financially free lifestyle"

    # Inflazione / Economia / Mercato
    if any(w in ctx for w in ["inflazion", "economi", "mercato", "crisi", "prezzi"]):
        return "inflation economic charts rising prices, market economy news financial data, cost of living increase graph"

    # Educazione / Imparare / Conoscenza
    if any(w in ctx for w in ["educazion", "impara", "conoscenz", "studia", "formazio"]):
        return "person studying financial education books, learning investment online course, financial literacy training"

    # Se abbiamo keywords specifiche dallo Sheet
    if kw and kw != "none":
        return f"{kw}, financial planning, investment charts, business growth, money management"

    # Fallback Finanza generico
    return base


def fetch_clip_for_scene(scene_number: int, query: str, avg_scene_duration: float):
    """
    🎯 Canale Finanza: B‑roll professionale, pulito, positivo.
    Priorità: grafici, persone che pianificano, città business, calcoli.
    Filtro anti‑content inappropriato (animali, sport, cucina, fitness, party).
    """
    target_duration = min(4.0, avg_scene_duration)

    def is_finance_video_metadata(video_data, source):
        banned = [
            "dog", "cat", "animal", "wildlife", "bird", "fish", "horse",
            "fitness", "yoga", "workout", "gym", "exercise",
            "kitchen", "cooking", "food", "recipe", "chef",
            "wedding", "party", "celebration", "festival",
            "sports", "game", "soccer", "football", "basketball",
            "gaming", "videogame", "esports"
        ]
        
        # Keywords finanza che vogliamo
        finance_keywords = [
            "business", "finance", "money", "investment", "stock", "chart",
            "graph", "data", "analysis", "banking", "savings", "planning",
            "budget", "calculator", "laptop", "office", "city", "growth"
        ]
        
        if source == "pexels":
            text = (video_data.get("description", "") + " " +
                    " ".join(video_data.get("tags", []))).lower()
        else:
            text = " ".join(video_data.get("tags", [])).lower()

        has_banned = any(kw in text for kw in banned)
        has_finance = any(kw in text for kw in finance_keywords)
        
        status = "✅ FINANCE OK" if (not has_banned and has_finance) else ("❌ OFF‑TOPIC" if has_banned else "⚠️ NEUTRAL")
        print(f"🔍 [{source}] '{text[:60]}...' → {status}", flush=True)
        
        # Accetta se: (1) ha keywords finanza E non banned, OPPURE (2) non ha banned (neutrale OK)
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

    # --- PEXELS: query finanza ---
    def try_pexels():
        if not PEXELS_API_KEY:
            return None
        headers = {"Authorization": PEXELS_API_KEY}
        params = {
            "query": f"{query} business finance charts investment planning",
            "orientation": "landscape",
            "per_page": 25,
            "page": random.randint(1, 3),
        }
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params=params,
            timeout=20,
        )
        if resp.status_code != 200:
            return None

        videos = resp.json().get("videos", [])
        finance_videos = [v for v in videos if is_finance_video_metadata(v, "pexels")]

        print(f"🎯 Pexels: {len(videos)} totali → {len(finance_videos)} FINANCE OK", flush=True)
        if finance_videos:
            video = random.choice(finance_videos)
            for vf in video.get("video_files", []):
                if vf.get("width", 0) >= 1280:
                    return download_file(vf["link"])
        return None

    # --- PIXABAY: query finanza ---
    def try_pixabay():
        if not PIXABAY_API_KEY:
            return None
        params = {
            "key": PIXABAY_API_KEY,
            "q": f"{query} business finance charts investment planning",
            "per_page": 25,
            "safesearch": "true",
            "min_width": 1280,
        }
        resp = requests.get("https://pixabay.com/api/videos/", params=params, timeout=20)
        if resp.status_code != 200:
            return None

        hits = resp.json().get("hits", [])
        for hit in hits:
            if is_finance_video_metadata(hit, "pixabay"):
                videos = hit.get("videos", {})
                for quality in ["large", "medium", "small"]:
                    if quality in videos and "url" in videos[quality]:
                        return download_file(videos[quality]["url"])
        return None

    # Priorità: Pexels → Pixabay
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


@app.route("/ffmpeg-test", methods=["GET"])
def ffmpeg_test():
    result = subprocess.run(
        ["ffmpeg", "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    firstline = result.stdout.splitlines()[0] if result.stdout else "no output"
    return jsonify({"ffmpeg_output": firstline})


@app.route("/generate", methods=["POST"])
def generate():
    audiopath = None
    audio_wav_path = None
    video_looped_path = None
    final_video_path = None
    scene_paths = []

    try:
        if not all(
            [R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_PUBLIC_BASE_URL]
        ):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Config R2 mancante",
                        "video_url": None,
                    }
                ),
                500,
            )

        data = request.get_json(force=True) or {}
        audiobase64 = data.get("audio_base64") or data.get("audiobase64")

        raw_script = (
            data.get("script")
            or data.get("script_chunk")
            or data.get("script_audio")
            or data.get("script_completo")
            or ""
        )
        script = (
            " ".join(str(p).strip() for p in raw_script)
            if isinstance(raw_script, list)
            else str(raw_script).strip()
        )

        raw_keywords = data.get("keywords", "")
        sheet_keywords = (
            ", ".join(str(k).strip() for k in raw_keywords)
            if isinstance(raw_keywords, list)
            else str(raw_keywords).strip()
        )

        print("=" * 80, flush=True)
        print(
            f"🎬 START FINANZA: {len(script)} char script, keywords: '{sheet_keywords}'",
            flush=True,
        )

        if not audiobase64:
            return (
                jsonify({"success": False, "error": "audiobase64 mancante"}),
                400,
            )

        # Audio processing
        audio_bytes = base64.b64decode(audiobase64)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(audio_bytes)
            audiopath_tmp = f.name

        audio_wav_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        audio_wav_path = audio_wav_tmp.name
        audio_wav_tmp.close()

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                audiopath_tmp,
                "-acodec",
                "pcm_s16le",
                "-ar",
                "48000",
                audio_wav_path,
            ],
            timeout=60,
            check=True,
        )
        os.unlink(audiopath_tmp)
        audiopath = audio_wav_path

        # Real duration
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                audiopath,
            ],
            stdout=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        real_duration = (
            float(probe.stdout.strip()) if probe.stdout.strip() else 720.0
        )

        print(
            f"⏱️  Durata audio: {real_duration/60:.1f}min ({real_duration:.0f}s)",
            flush=True,
        )

        # Scene sync
        script_words = script.lower().split()
        words_per_second = (
            len(script_words) / real_duration if real_duration > 0 else 2.5
        )
        avg_scene_duration = real_duration / 25

        scene_assignments = []
        for i in range(25):
            timestamp = i * avg_scene_duration
            word_index = int(timestamp * words_per_second)
            scene_context = (
                " ".join(script_words[word_index: word_index + 7])
                if word_index < len(script_words)
                else "financial charts investment planning business growth"
            )
            scene_query = pick_visual_query(scene_context, sheet_keywords)
            scene_assignments.append(
                {
                    "scene": i + 1,
                    "timestamp": round(timestamp, 1),
                    "context": scene_context[:60],
                    "query": scene_query[:80],
                }
            )

        # Download clips
        for assignment in scene_assignments:
            print(
                f"📍 Scene {assignment['scene']}: {assignment['timestamp']}s → '{assignment['context']}'",
                flush=True,
            )
            clip_path, clip_dur = fetch_clip_for_scene(
                assignment["scene"], assignment["query"], avg_scene_duration
            )
            if clip_path and clip_dur:
                scene_paths.append((clip_path, clip_dur))

        print(f"✅ CLIPS SCARICATE: {len(scene_paths)}/25", flush=True)

        if len(scene_paths) < 5:
            raise RuntimeError(f"Troppe poche clip: {len(scene_paths)}/25")

        # Normalize + concat + merge
        normalized_clips = []
        for i, (clip_path, _dur) in enumerate(scene_paths):
            try:
                normalized_tmp = tempfile.NamedTemporaryFile(
                    delete=False, suffix=".mp4"
                )
                normalized_path = normalized_tmp.name
                normalized_tmp.close()

                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        clip_path,
                        "-vf",
                        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-crf",
                        "23",
                        "-an",
                        normalized_path,
                    ],
                    timeout=120,
                    check=True,
                )

                if os.path.exists(normalized_path) and os.path.getsize(
                    normalized_path
                ) > 1000:
                    normalized_clips.append(normalized_path)
            except Exception:
                pass

        if not normalized_clips:
            raise RuntimeError("Nessuna clip normalizzata")

        # Concat
        def get_duration(p):
            out = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    p,
                ],
                stdout=subprocess.PIPE,
                text=True,
                timeout=10,
            ).stdout.strip()
            return float(out or 4.0)

        total_clips_duration = sum(get_duration(p) for p in normalized_clips)

        concat_list_tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".txt"
        )
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

        video_looped_tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".mp4"
        )
        video_looped_path = video_looped_tmp.name
        video_looped_tmp.close()

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_list_tmp.name,
                "-vf",
                "fps=30,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-t",
                str(real_duration),
                video_looped_path,
            ],
            timeout=600,
            check=True,
        )
        os.unlink(concat_list_tmp.name)

        # Final merge
        final_video_tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".mp4"
        )
        final_video_path = final_video_tmp.name
        final_video_tmp.close()

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                video_looped_path,
                "-i",
                audiopath,
                "-filter_complex",
                "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p[v]",
                "-map",
                "[v]",
                "-map",
                "1:a",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                final_video_path,
            ],
            timeout=600,
            check=True,
        )

        # R2 upload
        s3_client = get_s3_client()
        today = dt.datetime.utcnow().strftime("%Y-%m-%d")
        object_key = f"videos/{today}/{uuid.uuid4().hex}.mp4"

        s3_client.upload_file(
            Filename=final_video_path,
            Bucket=R2_BUCKET_NAME,
            Key=object_key,
            ExtraArgs={"ContentType": "video/mp4"},
        )
        public_url = f"{R2_PUBLIC_BASE_URL.rstrip('/')}/{object_key}"
        cleanup_old_videos(s3_client, object_key)

        # Cleanup
        for path in (
            [audiopath, video_looped_path, final_video_path]
            + normalized_clips
            + [p[0] for p in scene_paths]
        ):
            try:
                os.unlink(path)
            except Exception:
                pass

        print(
            f"✅ VIDEO FINANZA COMPLETO: {real_duration/60:.1f}min → {public_url}",
            flush=True,
        )

        return jsonify(
            {
                "success": True,
                "clips_used": len(scene_paths),
                "duration": real_duration,
                "video_url": public_url,
                "scenes": scene_assignments[:3],
            }
        )

    except Exception as e:
        print(f"❌ ERRORE: {e}", flush=True)
        for path in (
            [audiopath, audio_wav_path, video_looped_path, final_video_path]
            + [p[0] for p in scene_paths]
        ):
            try:
                os.unlink(path)
            except Exception:
                pass
        return (
            jsonify({"success": False, "error": str(e), "video_url": None}),
            500,
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
