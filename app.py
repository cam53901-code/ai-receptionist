"""
CAWDA Creative — AI Receptionist
cawdacreates.com | Solo creative studio
Efficient: collects contact form info, then ends the call.
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

SYSTEM_PROMPT = """You are Alex, receptionist for CAWDA Creative — a solo-run studio (cawdacreates.com) that builds custom websites, brands, and e-commerce stores. Clients work directly with Cameron, the founder. No middlemen, no agency bloat.

Your ONLY job is to fill out the contact form. Ask questions in this exact order:

1. "What service are you interested in?" Options: custom web design, web development, brand identity, e-commerce, landing page, maintenance and support, or something else.

2. "What's your name?"

3. "What's your email address?"

4. "What's your phone number?" (say it's optional)

5. "What's your estimated budget?" Options: under 500, 500 to 1000, 1000 to 2000, 2000 to 3500, 3500 to 5000, or over 5000.

6. "Tell me a bit about your project." Keep this brief — one or two sentences is plenty.

Once you have ALL six answers, say: "That's everything I need. Cameron will review this and send you a personalized quote within 24 hours. You can also check out the portfolio at cawdacreates.com. Thanks for calling!"

Then say: "GOODBYE_NOW"

That exact phrase — GOODBYE_NOW — is your signal to end the call. Say it as a separate line when you are ready to hang up.

CRITICAL RULES:
- One question at a time. Never ask multiple questions in one response.
- One or two short sentences per response, maximum.
- If the caller asks a question instead of answering: answer it briefly, then return to your next question.
- If the caller goes off topic: politely steer them back to the next question.
- If you already have all the info: thank them and end with GOODBYE_NOW.
- Do NOT introduce yourself again mid-call. Only on the very first turn.
- Do NOT ramble. Do NOT list services unless specifically asked."""

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
conversations = {}


def get_ai_response(call_sid, user_text):
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Understood. I'll work through the contact form questions one at a time and end with GOODBYE_NOW when done."]}
        ]

    conversations[call_sid].append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        response = model.generate_content(conversations[call_sid])
        reply = response.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        reply = "Sorry, could you repeat that?"

    # Hard guardrails
    sentences = [s.strip() for s in reply.replace("? ", "?. ").replace("! ", "!. ").split(". ") if s.strip()]
    if len(sentences) > 4:
        reply = ". ".join(sentences[:4])
        if not reply.endswith((".", "?", "!")):
            reply += "."
    if len(reply) > 400:
        reply = reply[:397].rsplit(" ", 1)[0] + "."

    conversations[call_sid].append({"role": "model", "parts": [reply]})
    return reply


def generate_call_summary(call_sid):
    if call_sid not in conversations:
        return {"error": "No data"}
    prompt = f"""Extract JSON from this call. ONLY raw JSON, no backticks:
{{"caller_name":null,"caller_company":null,"caller_phone":null,"caller_email":null,"service_interest":null,"budget":null,"project_description":null,"reason":"","key_points":[],"action_needed":null,"urgency":"low"}}

Conversation:
{json.dumps(conversations[call_sid], indent=2)}"""
    try:
        raw = model.generate_content(prompt).text.strip()
        for m in ["```json", "```"]:
            raw = raw.replace(m, "")
        return json.loads(raw.strip())
    except Exception as e:
        print(f"Summary error: {e}")
        return {"raw_summary": str(e)}


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


def send_sms(text):
    if not YOUR_SMS_GATEWAY:
        return
    try:
        msg = MIMEText(text[:160])
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = YOUR_SMS_GATEWAY
        msg["Subject"] = ""
        s = smtplib.SMTP("smtp.gmail.com", 587)
        s.starttls()
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.send_message(msg)
        s.quit()
        print("SMS OK")
    except Exception as e:
        print(f"SMS fail: {e}")


# ─── ROUTES ──────────────────────────────────────────────

@app.route("/voice", methods=["GET", "POST"])
def voice():
    """Initial greeting only. Called once at start, or on Gather timeout."""
    sid = request.form.get("CallSid", "unknown")
    is_new = sid not in conversations or len(conversations.get(sid, [])) <= 2

    if is_new:
        conversations[sid] = []
        resp = VoiceResponse()
        resp.say("CAWDA Creative, this is Alex. What service are you interested in?",
                 voice="Polly.Joanna")
    else:
        # Timed out mid-conversation — prompt them to continue, don't re-greet
        resp = VoiceResponse()
        resp.say("I'm still here. Go ahead.",
                 voice="Polly.Joanna")

    gather = Gather(
        input="speech",
        action="/handle-speech",
        speech_timeout="5",
        speech_model="default",
        enhanced=True,
    )
    resp.append(gather)
    resp.redirect("/voice")
    return Response(str(resp), mimetype="text/xml")


@app.route("/handle-speech", methods=["POST"])
def handle_speech():
    sid = request.form.get("CallSid", "?")
    text = request.form.get("SpeechResult", "").strip()
    conf = float(request.form.get("Confidence", "0"))

    print(f"[CALL {sid[:12]}...] '{text}' (conf={conf})")

    if not text or conf < 0.3:
        resp = VoiceResponse()
        resp.say("Sorry, I missed that. Could you say it again?", voice="Polly.Joanna")
        gather = Gather(input="speech", action="/handle-speech",
                        speech_timeout="5", speech_model="default", enhanced=True)
        resp.append(gather)
        resp.redirect("/voice")
        return Response(str(resp), mimetype="text/xml")

    # ── AI response ──
    reply = get_ai_response(sid, text)
    print(f"[AI REPLY] {reply}")

    # ── Check if AI signaled to end the call ──
    if "GOODBYE_NOW" in reply:
        # Remove the signal phrase from what we speak
        spoken = reply.replace("GOODBYE_NOW", "").strip()
        summary = generate_call_summary(sid)

        subj = f"CAWDA — {summary.get('caller_name', 'Caller')} — {datetime.now().strftime('%b %d, %I:%M %p')}"
        body = f"""📞 NEW LEAD FROM AI RECEPTIONIST
{'='*40}
Name:       {summary.get('caller_name', 'Unknown')}
Company:    {summary.get('caller_company', 'N/A')}
Phone:      {summary.get('caller_phone', 'N/A')}
Email:      {summary.get('caller_email', 'N/A')}
Service:    {summary.get('service_interest', 'Not specified')}
Budget:     {summary.get('budget', 'Not discussed')}
Project:    {summary.get('project_description', 'N/A')}
Urgency:    {summary.get('urgency', 'low')}

Reason:     {summary.get('reason', 'N/A')}
Action:     {summary.get('action_needed', 'None')}

Points:
{chr(10).join('- '+p for p in summary.get('key_points', ['None']))}

---
CAWDA Creative · cawdacreates.com · hello@cawdacreates.com
"""
        send_email(subj, body)
        send_sms(f"CAWDA lead: {summary.get('caller_name','?')} — {summary.get('service_interest', summary.get('reason','call'))}"[:160])

        resp = VoiceResponse()
        if spoken:
            resp.say(spoken, voice="Polly.Joanna")
        resp.pause(length=0.3)
        resp.hangup()
        conversations.pop(sid, None)
        return Response(str(resp), mimetype="text/xml")

    # ── Normal conversation: speak reply, gather next input ──
    resp = VoiceResponse()
    resp.pause(length=0.4)
    resp.say(reply, voice="Polly.Joanna")
    gather = Gather(input="speech", action="/handle-speech",
                    speech_timeout="5", speech_model="default", enhanced=True)
    resp.append(gather)
    resp.redirect("/voice")  # safe now — /voice won't re-greet if conversation exists
    return Response(str(resp), mimetype="text/xml")


@app.route("/status")
def status():
    return {"ok": True, "calls": len(conversations)}


if __name__ == "__main__":
    app.run(debug=True, port=5000)
