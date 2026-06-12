"""
CAWDA Creative — AI Receptionist (Timeout-Proof)
"""

import os, json, smtplib, signal
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import google.generativeai as genai

app = Flask(__name__)

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
YOUR_EMAIL = os.environ["YOUR_EMAIL"]
YOUR_SMS_GATEWAY = os.environ.get("YOUR_SMS_GATEWAY", "")

SYSTEM_PROMPT = """You are Alex at CAWDA Creative. Collect these 6 answers, one at a time. One sentence per response.

Questions in order:
1. "What service are you looking for?"
2. "What's your name?"
3. "What's your email address?"
4. "What's your phone number?" (optional)
5. "What's your approximate budget?"
6. "Briefly describe your project."

After question 6, the call ends automatically. On the 6th response, simply acknowledge what they said in one sentence. Do not ask another question.

RULES:
- ONE sentence. No exceptions.
- Never explain services unless directly asked: "Websites, branding, and e-commerce."
- If asked about pricing: "Cameron does custom quotes."
- If they go off topic: return to the next question.
- Never introduce yourself after the first turn.
- Keep responses under 150 characters."""

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
conversations = {}
question_counts = {}

CLOSING = "That's everything. Cameron will send your custom quote within 24 hours. Thanks for calling CAWDA Creative."


class TimeoutError(Exception):
    pass


def with_timeout(func, args=(), kwargs=None, seconds=25):
    """Run a function with a timeout. Returns (result, True) or (None, False)."""
    if kwargs is None:
        kwargs = {}
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            error[0] = e

    import threading
    t = threading.Thread(target=target)
    t.daemon = True
    t.start()
    t.join(seconds)

    if t.is_alive():
        return None, False
    if error[0]:
        raise error[0]
    return result[0], True


def get_ai_response(call_sid, user_text):
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it."]}
        ]
        question_counts[call_sid] = 0

    q_num = question_counts[call_sid]

    if q_num >= 6:
        prompt = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it."]},
            {"role": "user", "parts": [
                "Final answer. Acknowledge in one sentence. No questions. "
                f"Caller: {user_text}"
            ]},
        ]
        try:
            response, ok = with_timeout(model.generate_content, (prompt,), seconds=25)
            if ok:
                reply = response.text.strip()
            else:
                reply = "Got it, thanks for those details."
        except Exception as e:
            print(f"Gemini error on q6: {e}")
            reply = "Got it, thanks for those details."

        if len(reply) > 250:
            reply = reply[:250].rsplit(" ", 1)[0]
        return reply, True

    history = conversations[call_sid]
    if len(history) > 8:
        history = history[:2] + history[-6:]

    history.append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        response, ok = with_timeout(model.generate_content, (history,), seconds=20)
        if ok:
            reply = response.text.strip()
        else:
            reply = "Could you repeat that?"
    except Exception as e:
        print(f"Gemini error: {e}")
        reply = "Could you repeat that?"

    if len(reply) > 250:
        reply = reply[:250].rsplit(" ", 1)[0]

    question_counts[call_sid] = q_num + 1
    conversations[call_sid] = history
    conversations[call_sid].append({"role": "model", "parts": [reply]})
    return reply, False


def generate_call_summary(call_sid):
    if call_sid not in conversations:
        return {"error": "No data"}
    prompt = f"""Extract JSON. ONLY raw JSON, no backticks:
{{"caller_name":null,"caller_phone":null,"caller_email":null,"service_interest":null,"budget":null,"project_description":null,"key_points":[],"action_needed":null}}

Conversation:
{json.dumps(conversations[call_sid], indent=2)}"""
    try:
        response, ok = with_timeout(model.generate_content, (prompt,), seconds=20)
        if ok:
            raw = response.text.strip()
            for m in ["```json", "```"]:
                raw = raw.replace(m, "")
            return json.loads(raw.strip())
        else:
            return {"raw_summary": "Timed out"}
    except Exception as e:
        print(f"Summary error: {e}")
        # Fallback: extract what we can from raw conversation
        msgs = conversations[call_sid]
        return {
            "caller_name": "Unknown",
            "caller_phone": "Unknown",
            "caller_email": "Unknown",
            "service_interest": "Unknown",
            "budget": "Unknown",
            "project_description": str(msgs[-2].get("parts", ["Unknown"])[0]) if len(msgs) >= 2 else "Unknown",
            "key_points": [],
            "action_needed": "Send custom quote within 24 hours",
            "raw_summary": "Fallback — AI summary timed out"
        }


def send_email(subject, body):
    """Send email with SMTP timeout — never hang the worker."""
    try:
        s = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        s.starttls()
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = YOUR_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        s.send_message(msg)
        s.quit()
        print("Email OK")
    except Exception as e:
        print(f"Email fail: {e}")


@app.route("/voice", methods=["GET", "POST"])
def voice():
    sid = request.form.get("CallSid", "unknown")
    is_new = sid not in conversations

    if is_new:
        conversations[sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it."]}
        ]
        question_counts[sid] = 0
        resp = VoiceResponse()
        resp.say("CAWDA Creative, this is Alex. What service are you looking for?",
                 voice="Polly.Joanna")
    else:
        resp = VoiceResponse()
        resp.say("Go ahead.", voice="Polly.Joanna")

    gather = Gather(input="speech", action="/handle-speech",
                    speech_timeout="4", speech_model="default", enhanced=True)
    resp.append(gather)
    resp.redirect("/voice")
    return Response(str(resp), mimetype="text/xml")


@app.route("/handle-speech", methods=["POST"])
def handle_speech():
    sid = request.form.get("CallSid", "?")
    text = request.form.get("SpeechResult", "").strip()
    conf = float(request.form.get("Confidence", "0"))

    print(f"[{sid[:10]}] '{text[:80]}...' (conf={conf})")

    if not text or conf < 0.3:
        resp = VoiceResponse()
        resp.say("Sorry, say that again?", voice="Polly.Joanna")
        gather = Gather(input="speech", action="/handle-speech",
                        speech_timeout="4", speech_model="default", enhanced=True)
        resp.append(gather)
        resp.redirect("/voice")
        return Response(str(resp), mimetype="text/xml")

    reply, should_end = get_ai_response(sid, text)
    prefix = f"Q#{min(question_counts.get(sid, 0) + 1, 6)}"
    print(f"[AI {prefix}] {reply} ({'END' if should_end else ''})")

    if should_end:
        # Generate summary with timeout protection
        summary = generate_call_summary(sid)

        subj = f"CAWDA Lead — {summary.get('caller_name', 'Unknown')} — {datetime.now().strftime('%b %d, %I:%M %p')}"
        body = f"""NEW LEAD
=========
Name:     {summary.get('caller_name', '?')}
Phone:    {summary.get('caller_phone', '?')}
Email:    {summary.get('caller_email', '?')}
Service:  {summary.get('service_interest', '?')}
Budget:   {summary.get('budget', '?')}
Project:  {summary.get('project_description', '?')}

Action:   Send custom quote within 24 hours

Key Points:
{chr(10).join('- '+p for p in summary.get('key_points', ['none']))}
---
cawdacreates.com | hello@cawdacreates.com
"""
        # Send email with timeout — won't block hangup if it fails
        send_email(subj, body)

        resp = VoiceResponse()
        resp.say(reply, voice="Polly.Joanna")
        resp.pause(length=0.3)
        resp.say(CLOSING, voice="Polly.Joanna")
        resp.hangup()

        conversations.pop(sid, None)
        question_counts.pop(sid, None)
        return Response(str(resp), mimetype="text/xml")

    resp = VoiceResponse()
    resp.say(reply, voice="Polly.Joanna")
    gather = Gather(input="speech", action="/handle-speech",
                    speech_timeout="4", speech_model="default", enhanced=True)
    resp.append(gather)
    resp.redirect("/voice")
    return Response(str(resp), mimetype="text/xml")


@app.route("/status")
def status():
    return {"ok": True}


if __name__ == "__main__":
    app.run(debug=True, port=5000)
