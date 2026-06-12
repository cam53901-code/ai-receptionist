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

SYSTEM_PROMPT = """You are Alex at CAWDA Creative (cawdacreates.com). Your ONLY job: collect these 6 pieces of info, one at a time, then end the call.

Ask these questions in ORDER. Do NOT skip ahead. Do NOT ask anything else.

1. "What service are you looking for?"
2. "What's your name?"
3. "What's your email?"
4. "What's your phone number?" (optional, skip if they prefer not to give it)
5. "What's your budget?" (under $500, $500-1k, $1k-2k, $2k-3.5k, $3.5k-5k, $5k+)
6. "Briefly, what's the project?"

After you have all answers: say "That's everything. Cameron will send you a quote within 24 hours. Thanks for calling CAWDA Creative." Then ALWAYS end with the exact word: GOODBYE_NOW

ABSOLUTE RULES:
- ONE sentence per response. Always. Never two.
- If they ask about pricing/services: "Cameron covers that in your custom quote."
- If they ramble: gently move to the next question.
- NEVER explain what CAWDA Creative does unless directly asked. If asked: "We build custom websites, brands, and e-commerce stores."
- NEVER repeat yourself.
- NEVER introduce yourself after the first question.
- Each response must be under 150 characters."""

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
conversations = {}
turn_counts = {}


def get_ai_response(call_sid, user_text):
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it."]}
        ]
        turn_counts[call_sid] = 0

    turn_counts[call_sid] += 1

    # Only send last 8 messages to keep it fast
    history = conversations[call_sid]
    if len(history) > 10:
        history = history[:2] + history[-8:]

    history.append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        response = model.generate_content(history)
        reply = response.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        reply = "Could you repeat that?"

    # Aggressive truncation — prevent cut-off sentences
    if len(reply) > 200:
        # Cut at last complete sentence under 200 chars
        cut = reply[:200].rsplit(". ", 1)[0]
        cut = cut.rsplit("? ", 1)[0]
        cut = cut.rsplit("! ", 1)[0]
        reply = cut + "." if not cut.endswith((".", "?", "!")) else cut

    conversations[call_sid] = history
    conversations[call_sid].append({"role": "model", "parts": [reply]})
    return reply


def generate_call_summary(call_sid):
    if call_sid not in conversations:
        return {"error": "No data"}
    prompt = f"""Extract JSON from this call. ONLY raw JSON, no backticks:
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
        turn_counts[sid] = 0
        resp = VoiceResponse()
        resp.say("CAWDA Creative, this is Alex. What service are you looking for?",
                 voice="Polly.Joanna")
    else:
        resp = VoiceResponse()
        resp.say("Go ahead.",
                 voice="Polly.Joanna")

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

    print(f"[{sid[:10]}] '{text}' (conf={conf})")

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

    if "GOODBYE_NOW" in reply:
        spoken = reply.replace("GOODBYE_NOW", "").strip()
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

Action:   {summary.get('action_needed', 'Send quote within 24hrs')}

Points:
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
        turn_counts.pop(sid, None)
        return Response(str(resp), mimetype="text/xml")

    resp = VoiceResponse()
    # No pause — respond immediately
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
