"""
CAWDA Creative — AI Receptionist (Deterministic)
Counts 6 questions, then ends the call. No AI signal needed.
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

SYSTEM_PROMPT = """You are Alex at CAWDA Creative. Collect these 6 answers, one at a time. One sentence per response.

Questions in order:
1. "What service are you looking for?"
2. "What's your name?"
3. "What's your email address?"
4. "What's your phone number?" (optional)
5. "What's your approximate budget?"
6. "Briefly describe your project."

After question 6, the call will end automatically. On the 6th response, simply acknowledge what they said. Keep it to one sentence. Do NOT say GOODBYE_NOW or anything about ending the call — the system handles that.

RULES:
- ONE sentence. No exceptions.
- Never explain services unless directly asked. Say: "Websites, branding, and e-commerce."
- If asked about pricing: "Cameron does custom quotes."
- If they go off topic: return to the next question.
- Never introduce yourself after the first turn.
- Keep every response under 150 characters."""

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
conversations = {}
question_counts = {}

QUESTIONS = [
    "What service are you looking for?",
    "What's your name?",
    "What's your email address?",
    "What's your phone number?",
    "What's your approximate budget?",
    "Briefly describe your project.",
]

CLOSING = "That's everything. Cameron will send your custom quote within 24 hours. Thanks for calling CAWDA Creative."


def get_ai_response(call_sid, user_text):
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it."]}
        ]
        question_counts[call_sid] = 0

    q_num = question_counts[call_sid]

    # On the last question (6th), ask AI to just acknowledge, then we end
    if q_num >= 6:
        prompt = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it."]},
            {"role": "user", "parts": [
                "This is the final answer. Acknowledge what they said in one sentence. "
                "Do NOT ask another question. Do NOT say GOODBYE_NOW. "
                f"Caller said: {user_text}"
            ]},
        ]
        try:
            response = model.generate_content(prompt)
            reply = response.text.strip()
        except:
            reply = "Got it. Thanks for those details."
        # Truncate safely
        if len(reply) > 250:
            reply = reply[:250].rsplit(" ", 1)[0]
        return reply, True  # True = end call

    # Normal question flow
    history = conversations[call_sid]
    if len(history) > 8:
        history = history[:2] + history[-6:]

    history.append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        response = model.generate_content(history)
        reply = response.text.strip()
    except:
        reply = QUESTIONS[q_num] if q_num < 6 else "Got it."

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

    print(f"[{sid[:10]}] '{text[:80]}' (conf={conf})")

    if not text or conf < 0.3:
        resp = VoiceResponse()
        resp.say("Sorry, say that again?", voice="Polly.Joanna")
        gather = Gather(input="speech", action="/handle-speech",
                        speech_timeout="4", speech_model="default", enhanced=True)
        resp.append(gather)
        resp.redirect("/voice")
        return Response(str(resp), mimetype="text/xml")

    reply, should_end = get_ai_response(sid, text)
    question_counts[sid] = question_counts.get(sid, 0)
    print(f"[AI] Q#{question_counts[sid]} | {reply}")

    if should_end:
        # Question 6 answered — end the call
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
        send_email(subj, body)

        resp = VoiceResponse()
        resp.say(reply, voice="Polly.Joanna")
        resp.pause(length=0.3)
        resp.say(CLOSING, voice="Polly.Joanna")
        resp.hangup()

        conversations.pop(sid, None)
        question_counts.pop(sid, None)
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
