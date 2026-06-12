"""
CAWDA Creative — AI Receptionist (fixed)
Deterministically collects lead details and emails them after the final answer.
"""

import os
import json
import smtplib
import threading
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
YOUR_EMAIL = os.environ["YOUR_EMAIL"]

VOICE = "Polly.Joanna"
CLOSING = "That's everything. Cameron will send your custom quote within 24 hours. Thanks for calling CAWDA Creative."

QUESTIONS = [
    ("service_interest", "What service are you looking for?"),
    ("caller_name", "What's your name?"),
    ("caller_email", "What's your email address?"),
    ("caller_phone", "What's your phone number? You can say skip if you prefer."),
    ("budget", "What's your approximate budget?"),
    ("project_description", "Briefly describe your project."),
]

# In-memory per-call state. This is OK for a single Render worker.
# If you scale to multiple workers/instances, move this to Redis/Postgres.
call_state = {}


def say_and_gather(message):
    """Say a prompt, then listen for speech."""
    resp = VoiceResponse()
    resp.say(message, voice=VOICE)
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

    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = YOUR_EMAIL
    msg["Subject"] = subj
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
        s.starttls()
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.send_message(msg)


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

        # Backup first so the lead is not lost if Gmail fails.
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

    return say_and_gather("CAWDA Creative, this is Alex. " + QUESTIONS[0][1])


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
        resp.say("Got it, thanks for those details.", voice=VOICE)
        resp.pause(length=0.3)
        resp.say(CLOSING, voice=VOICE)
        resp.hangup()

        thread = threading.Thread(target=process_summary_and_email, args=(sid,))
        thread.start()

        return Response(str(resp), mimetype="text/xml")

    # Move to next required question.
    state["question_index"] = idx + 1
    next_question = QUESTIONS[idx + 1][1]
    return say_and_gather(next_question)


@app.route("/status")
def status():
    return {"ok": True}


if __name__ == "__main__":
    app.run(debug=True, port=5000)
