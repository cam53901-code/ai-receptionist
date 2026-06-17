"""
CAWDA Creative — AI Receptionist
Deterministically collects lead details, sends email via SendGrid Web API,
and uses ElevenLabs TTS for more human-sounding call audio with Twilio <Play>.

Required Render environment variables:
- SENDGRID_API_KEY
- FROM_EMAIL
- YOUR_EMAIL

Required for ElevenLabs voice:
- ELEVENLABS_API_KEY

Recommended optional environment variables:
- PUBLIC_BASE_URL=https://ai-receptionist-zjar.onrender.com
- ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
- ELEVENLABS_MODEL_ID=eleven_flash_v2_5
- FALLBACK_TWILIO_VOICE=Polly.Joanna-Neural
"""

import os
import json
import hashlib
import threading
import traceback
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, request, Response, send_from_directory, abort
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
FROM_EMAIL = os.environ.get("FROM_EMAIL", "hello@cawdacreates.com")
YOUR_EMAIL = os.environ["YOUR_EMAIL"]

# ElevenLabs settings. The app falls back to Twilio <Say> if ElevenLabs is missing or fails.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel-style default voice
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
FALLBACK_TWILIO_VOICE = os.environ.get("FALLBACK_TWILIO_VOICE", "Polly.Joanna-Neural")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

AUDIO_DIR = Path("/tmp/cawda_tts_audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

CLOSING = "Alright, and that, is everything. Cameron will send your custom quote within 24 hours. Thank you for calling CAWDA Creative."

# The fields are still collected in a fixed order so the lead email is reliable,
# but the wording is more conversational.
QUESTIONS = [
    ("service_interest", "So, what are we looking to get done today?"),
    ("caller_name", "And who do I have the pleasure of speaking with?"),
    ("caller_email", "Now, What's the best email for Cameron to reach you at?"),
    ("caller_phone", "What's the best phone number for you? You can skip this if you'd rather not share it."),
    ("budget", "Do you happen to have a rough budget in mind for the project?"),
    ("project_description", "And before we wrap things up, could you tell me a little bit about what you're hoping to build?"),
]

ACKNOWLEDGEMENTS = [
    "Got it.",
    "Perfect.",
    "Thanks.",
    "Awesome.",
    "Sounds good.",
]

# In-memory per-call state. This is OK for a single Render worker.
# If you scale to multiple workers/instances, move this to Redis/Postgres.
call_state = {}


def get_public_base_url():
    """Return the public URL Twilio can use to fetch generated audio."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL

    # Render usually forwards the public host/proto correctly, but PUBLIC_BASE_URL is safer.
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "https")
    host = request.headers.get("Host", "")
    if host:
        return f"{forwarded_proto}://{host}".rstrip("/")

    return request.url_root.rstrip("/")


def tts_cache_filename(text):
    """Stable filename so repeated prompts reuse the same generated MP3."""
    key = f"{ELEVENLABS_VOICE_ID}|{ELEVENLABS_MODEL_ID}|{text}".encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    return f"{digest}.mp3"


def generate_elevenlabs_audio_url(text):
    """
    Generate speech with ElevenLabs and return a public URL that Twilio can <Play>.
    Falls back to None if ElevenLabs is not configured or fails.
    """
    if not ELEVENLABS_API_KEY:
        print("ElevenLabs not configured; falling back to Twilio <Say>")
        return None

    filename = tts_cache_filename(text)
    file_path = AUDIO_DIR / filename

    # Reuse cached audio to reduce ElevenLabs character usage and latency.
    if file_path.exists() and file_path.stat().st_size > 0:
        return f"{get_public_base_url()}/audio/{filename}"

    try:
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            params={"output_format": "mp3_22050_32"},
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": ELEVENLABS_MODEL_ID,
                "voice_settings": {
                    # Lower stability = more expressive. Too low can sound inconsistent.
                    "stability": 0.42,
                    "similarity_boost": 0.78,
                    "style": 0.18,
                    "use_speaker_boost": True,
                },
            },
            timeout=20,
        )

        if response.status_code != 200:
            print(f"ElevenLabs TTS failed: {response.status_code} {response.text[:500]}")
            return None

        file_path.write_bytes(response.content)
        print(f"Generated ElevenLabs audio: {filename} ({len(response.content)} bytes)")
        return f"{get_public_base_url()}/audio/{filename}"

    except Exception as e:
        print(f"ElevenLabs TTS exception: {e}")
        traceback.print_exc()
        return None


def speak(resp, message):
    """Speak a message using ElevenLabs <Play>, with Twilio <Say> fallback."""
    audio_url = generate_elevenlabs_audio_url(message)
    if audio_url:
        resp.play(audio_url)
    else:
        resp.say(message, voice=FALLBACK_TWILIO_VOICE)


def say_and_gather(message):
    """Speak a prompt, then listen for speech."""
    resp = VoiceResponse()
    speak(resp, message)

    gather = Gather(
        input="speech",
        action="/handle-speech",
        method="POST",
        timeout=8,
        speech_timeout="auto",
        speech_model="phone_call",
        enhanced=True,
        language="en-US",
        action_on_empty_result=True,
    )
    resp.append(gather)
    resp.redirect("/repeat-question", method="POST")
    return Response(str(resp), mimetype="text/xml")


def normalize_answer(key, text):
    text = (text or "").strip()
    if key == "caller_phone" and text.lower() in {"skip", "no", "none", "no thanks", "rather not"}:
        return "Skipped"
    return text


def get_acknowledgement(idx, state):
    """Return a short human-style acknowledgement before the next question."""
    # After the caller gives their name, make the next transition slightly warmer.
    if idx == 1:
        name = state.get("answers", {}).get("caller_name", "").strip()
        if name:
            return "Thanks."

    return ACKNOWLEDGEMENTS[idx % len(ACKNOWLEDGEMENTS)]


def send_email(summary):
    subj = f"CAWDA Lead — {summary.get('caller_name') or 'Unknown'} — {datetime.now().strftime('%b %d, %I:%M %p')}"

    body = f"""NEW LEAD
=========
Name: {summary.get('caller_name', '?')}
Phone: {summary.get('caller_phone', '?')}
Email: {summary.get('caller_email', '?')}
Service: {summary.get('service_interest', '?')}
Budget: {summary.get('budget', '?')}
Project: {summary.get('project_description', '?')}

Twilio From: {summary.get('twilio_from', '?')}
Call SID: {summary.get('call_sid', '?')}
Timestamp: {summary.get('timestamp', '?')}

Raw transcript:
{json.dumps(summary.get('transcript', []), indent=2)}

Action: Send custom quote within 24 hours
---
cawdacreates.com | hello@cawdacreates.com
"""

    payload = {
        "personalizations": [
            {
                "to": [{"email": YOUR_EMAIL}],
                "subject": subj,
            }
        ],
        "from": {
            "email": FROM_EMAIL,
            "name": "CAWDA AI Receptionist",
        },
        "content": [
            {
                "type": "text/plain",
                "value": body,
            }
        ],
    }

    response = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    if response.status_code not in (200, 202):
        raise RuntimeError(f"SendGrid failed: {response.status_code} {response.text}")


def process_summary_and_email(sid):
    """Runs after final answer. Saves a backup and sends the lead email."""
    try:
        state = call_state.get(sid)
        if not state:
            print(f"[{sid[:10]}] No call state found; cannot send email")
            return

        answers = state.get("answers", {})
        summary = {
            "caller_name": answers.get("caller_name") or "Unknown",
            "caller_phone": answers.get("caller_phone") or state.get("twilio_from") or "Unknown",
            "caller_email": answers.get("caller_email") or "Unknown",
            "service_interest": answers.get("service_interest") or "Unknown",
            "budget": answers.get("budget") or "Unknown",
            "project_description": answers.get("project_description") or "Unknown",
            "twilio_from": state.get("twilio_from"),
            "call_sid": sid,
            "timestamp": str(datetime.now()),
            "transcript": state.get("transcript", []),
        }

        # Backup first so the lead is not lost if email fails.
        try:
            with open("/tmp/cawda_lead_backup.jsonl", "a") as f:
                f.write(json.dumps(summary) + "\n")
            print(f"[{sid[:10]}] Saved backup to /tmp/cawda_lead_backup.jsonl")
        except Exception as e:
            print(f"[{sid[:10]}] Backup failed: {e}")

        send_email(summary)
        print(f"[{sid[:10]}] Email sent successfully to {YOUR_EMAIL}")

    except Exception as e:
        print(f"[{sid[:10]}] Email/background processing failed: {e}")
        traceback.print_exc()
    finally:
        call_state.pop(sid, None)


@app.route("/audio/<filename>", methods=["GET"])
def audio(filename):
    """Serve generated ElevenLabs MP3 files to Twilio."""
    if not filename.endswith(".mp3") or "/" in filename or ".." in filename:
        abort(404)
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg", max_age=86400)


@app.route("/voice", methods=["GET", "POST"])
def voice():
    sid = request.form.get("CallSid", "unknown")
    twilio_from = request.form.get("From", "Unknown")

    call_state[sid] = {
        "question_index": 0,
        "answers": {},
        "transcript": [],
        "twilio_from": twilio_from,
        "started_at": str(datetime.now()),
    }

    return say_and_gather("Hey! CAWDA Creative, this is Alex. " + QUESTIONS[0][1])


@app.route("/repeat-question", methods=["GET", "POST"])
def repeat_question():
    sid = request.form.get("CallSid", "unknown")
    state = call_state.get(sid)
    if not state:
        return say_and_gather("CAWDA Creative, this is Alex. " + QUESTIONS[0][1])

    idx = state.get("question_index", 0)
    prompt = QUESTIONS[min(idx, len(QUESTIONS) - 1)][1]
    return say_and_gather("Sorry, I didn't catch that. " + prompt)


@app.route("/handle-speech", methods=["POST"])
def handle_speech():
    sid = request.form.get("CallSid", "unknown")
    text = request.form.get("SpeechResult", "").strip()
    conf_raw = request.form.get("Confidence", "0") or "0"

    try:
        conf = float(conf_raw)
    except ValueError:
        conf = 0.0

    state = call_state.get(sid)
    if not state:
        # Twilio may hit this if the app restarted mid-call.
        call_state[sid] = {
            "question_index": 0,
            "answers": {},
            "transcript": [],
            "twilio_from": request.form.get("From", "Unknown"),
            "started_at": str(datetime.now()),
        }
        state = call_state[sid]

    idx = state.get("question_index", 0)
    key, current_question = QUESTIONS[min(idx, len(QUESTIONS) - 1)]

    print(f"[{sid[:10]}] Q{idx + 1} {key}: '{text[:120]}' (conf={conf})")

    # If Twilio heard nothing or was very unsure, repeat the SAME question.
    if not text or conf < 0.20:
        return say_and_gather("Sorry, I didn't catch that. " + current_question)

    answer = normalize_answer(key, text)
    state["answers"][key] = answer
    state["transcript"].append({
        "question": current_question,
        "field": key,
        "answer": answer,
        "confidence": conf,
    })

    # If this was the project-description answer, close and send the email.
    if idx >= len(QUESTIONS) - 1:
        resp = VoiceResponse()
        speak(resp, "Got it, thanks for those details.")
        resp.pause(length=0.3)
        speak(resp, CLOSING)
        resp.hangup()

        thread = threading.Thread(target=process_summary_and_email, args=(sid,))
        thread.start()

        return Response(str(resp), mimetype="text/xml")

    # Move to next required question with a short natural acknowledgement.
    state["question_index"] = idx + 1
    ack = get_acknowledgement(idx, state)
    next_question = QUESTIONS[idx + 1][1]
    return say_and_gather(f"{ack} {next_question}")


@app.route("/status")
def status():
    return {"ok": True, "elevenlabs_configured": bool(ELEVENLABS_API_KEY)}


if __name__ == "__main__":
    app.run(debug=True, port=5000)
