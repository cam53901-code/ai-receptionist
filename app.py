"""
CAWDA Creative — Minimal AI Receptionist
Gets contact form info, says goodbye, sends email. Nothing more.
"""

import os, json, smtplib
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

SYSTEM_PROMPT = """You are Alex at CAWDA Creative (cawdacreates.com). Collect these 6 answers one at a time, then end the call.

Ask in order. One question at a time. Nothing else.

1. "What service are you looking for?"
2. "What's your name?"
3. "What's your email address?"
4. "What's your phone number?" (optional)
5. "What's your approximate budget?"
6. "Briefly describe your project."

When you have ALL six: say exactly "That's everything I need. Cameron will send your custom quote within 24 hours. Thanks for calling CAWDA Creative." Then the word GOODBYE_NOW on its own.

RULES:
- ONE sentence. Always.
- Never explain services unless directly asked. If asked: "Websites, branding, and e-commerce."
- If asked about pricing: "Cameron does custom quotes — you'll get yours within 24 hours."
- If they go off topic: return to the next question.
- NEVER repeat yourself.
- NEVER introduce yourself after the first turn.
- Keep responses under 120 characters."""

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
conversations = {}


def get_ai_response(call_sid, user_text):
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it."]}
        ]

    # Only send last 6 messages for speed
    history = conversations[call_sid]
    if len(history) > 8:
        history = history[:2] + history[-6:]

    history.append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        response = model.generate_content(history)
        reply = response.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        reply = "Could you repeat that?"

    # Simple hard cap — no aggressive sentence splitting
    if len(reply) > 250:
        reply = reply[:250].rsplit(" ", 1)[0]

    conversations[call_sid] = history
    conversations[call_sid].append({"role": "model", "parts": [reply]})
    return reply


def generate_call_summary(call_sid):
    if call_sid not in conversations:
        return {"error": "No data"}
    prompt = f"""Extract JSON. ONLY raw JSON, no backticks:
{{"caller_name":null,"caller_phone":null,"caller_email":null,"service_interest":null,"budget":null,"project_description":null,"key_points":[],"action_needed":null}}

Conversation:
{json.dumps(conversations[call_sid], indent=2)}"""
    try:
        raw = model.generate_content(prompt).text.strip()
        for m in ["```json", "```"]:
            raw = raw.replace(m, "")
        return json.loads(raw.strip())
    except:
        return {"raw_summary": "Could not parse"}


def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = YOUR_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        s = smtplib.SMTP("smtp.gmail.com", 587)
        s.starttls()
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
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

    reply = get_ai_response(sid, text)
    print(f"[AI] {reply}")

    # Check for GOODBYE_NOW BEFORE touching the response text
    should_end = "GOODBYE_NOW" in reply

    if should_end:
        # Remove the signal word from spoken text
        spoken = reply.replace("GOODBYE_NOW", "").strip().rstrip(",.;:")
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

Action:   {summary.get('action_needed', 'Send custom quote within 24 hours')}

Key Points:
{chr(10).join('- '+p for p in summary.get('key_points', ['none']))}
---
cawdacreates.com | hello@cawdacreates.com
"""
        send_email(subj, body)

        resp = VoiceResponse()
        if spoken:
            resp.say(spoken, voice="Polly.Joanna")
        resp.hangup()
        conversations.pop(sid, None)
        return Response(str(resp), mimetype="text/xml")

    # Normal turn
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
