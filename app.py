"""
CAWDA Creative — AI Receptionist (SSML Enhanced)
=================================================
cawdacreates.com | Solo creative studio
Natural voice with pauses, emphasis, and pacing.
"""

import os, json, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import google.generativeai as genai

app = Flask(__name__)

# ── Environment variables (set in Render) ──
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
YOUR_EMAIL = os.environ["YOUR_EMAIL"]
YOUR_SMS_GATEWAY = os.environ.get("YOUR_SMS_GATEWAY", "")

# ═══════════════════════════════════════════════════════════
# CAWDA CREATIVE — SHORT, NATURAL PERSONALITY
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are Alex, receptionist for CAWDA Creative — a solo-run studio that builds custom websites, brands, and e-commerce stores. No agency bloat, no middlemen. Clients work directly with Cameron, the founder and designer/developer.

Services: custom web design, web development, brand identity, e-commerce (Shopify/WooCommerce), landing pages, maintenance and support.
Process: free discovery call, then design mockups for approval, then build with unlimited revisions, then launch and support.
Pricing: custom-quoted per project. Flat-rate, no hidden fees. About 40% less than agencies.
Hours: Monday to Friday, 11 AM to 10 PM Eastern. Fully online across Canada and the US.
Website: cawdacreates.com — portfolio, pricing form, and contact info.
Email: hello@cawdacreates.com

YOUR RULES:
- Answer in ONE or TWO short sentences only. Never more than two.
- Greet callers with your name and the business name. Ask how you can help.
- Collect the caller's name, company, phone number, email, and what service they need.
- When they ask about pricing: say every project is custom-quoted. Offer to have Cameron send a free estimate. Mention the contact form at cawdacreates.com.
- For discovery calls: get their availability. Cameron follows up within 24 hours.
- End every response with a question. Keep them talking.
- If you do not know something: say Cameron can answer that directly and you will have him reach out.
- Do not ramble. Do not list every service unless specifically asked. Do not sound like you are reading from a brochure.
- Use contractions. Say "you're" not "you are." Sound like a real person."""

# ═══════════════════════════════════════════════════════════

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
conversations = {}


def get_ai_response(call_sid, user_text):
    """Send conversation to Gemini, return a concise response."""
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it. I'm Alex at CAWDA Creative. Ready to help."]}
        ]

    conversations[call_sid].append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        response = model.generate_content(conversations[call_sid])
        reply = response.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        reply = "Sorry, could you repeat that?"

    # ── Hard guardrails against rambling ──
    sentences = [s.strip() for s in reply.replace("? ", "?. ").replace("! ", "!. ").split(". ") if s.strip()]
    if len(sentences) > 3:
        reply = ". ".join(sentences[:3])
        if not reply.endswith((".", "?", "!")):
            reply += "."

    if len(reply) > 400:
        reply = reply[:397].rsplit(" ", 1)[0] + "."

    conversations[call_sid].append({"role": "model", "parts": [reply]})
    return reply


def generate_call_summary(call_sid):
    """Extract structured JSON summary from the transcript."""
    if call_sid not in conversations:
        return {"error": "No conversation data"}

    prompt = f"""Extract JSON from this phone call. ONLY raw JSON — no backticks, no markdown:
{{"caller_name":null,"caller_company":null,"caller_phone":null,"caller_email":null,"service_interest":null,"budget":null,"reason":"","key_points":[],"action_needed":null,"urgency":"low"}}

Conversation:
{json.dumps(conversations[call_sid], indent=2)}"""

    try:
        raw = model.generate_content(prompt).text.strip()
        for marker in ["```json", "```"]:
            raw = raw.replace(marker, "")
        return json.loads(raw.strip())
    except Exception as e:
        print(f"Summary error: {e}")
        return {"raw_summary": str(e)}


# ─── NOTIFICATIONS ───────────────────────────────────────

def send_email(subject, body):
    """Send call summary via Gmail SMTP."""
    try:
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = YOUR_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Email sent")
    except Exception as e:
        print(f"Email failed: {e}")


def send_sms(text):
    """Send SMS via carrier email-to-SMS gateway."""
    if not YOUR_SMS_GATEWAY:
        return
    try:
        msg = MIMEText(text[:160])
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = YOUR_SMS_GATEWAY
        msg["Subject"] = ""

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("SMS sent")
    except Exception as e:
        print(f"SMS failed: {e}")


# ─── TWILIO WEBHOOKS ─────────────────────────────────────

@app.route("/voice", methods=["GET", "POST"])
def voice():
    """Called by Twilio when someone dials in."""
    sid = request.form.get("CallSid", "unknown")
    if sid not in conversations:
        conversations[sid] = []

    resp = VoiceResponse()
    resp.say(
        "<speak>"
        "<prosody rate='medium' pitch='+3%'>"
        "CAWDA Creative"
        "</prosody>"
        "<break time='0.3s'/>"
        "this is Alex."
        "<break time='0.4s'/>"
        "How can I help you?"
        "</speak>",
        voice="Polly.Ruth",
        language="en-US"
    )

    gather = Gather(
        input="speech",
        action="/handle-speech",
        speech_timeout="auto",
        speech_model="phone_call",
        enhanced=True,
    )
    resp.append(gather)
    resp.redirect("/voice")
    return Response(str(resp), mimetype="text/xml")


@app.route("/handle-speech", methods=["POST"])
def handle_speech():
    """Called by Twilio each time the caller speaks."""
    sid = request.form.get("CallSid", "?")
    text = request.form.get("SpeechResult", "").strip()
    conf = float(request.form.get("Confidence", "0"))

    print(f"[CALL] '{text}' (conf={conf})")

    # ── Poor recognition → ask to repeat ──
    if not text or conf < 0.3:
        resp = VoiceResponse()
        resp.pause(length=0.3)
        resp.say(
            "<speak>"
            "<prosody rate='medium'>Sorry, I missed that.</prosody>"
            "<break time='0.3s'/>"
            "Could you say it again?"
            "</speak>",
            voice="Polly.Ruth",
            language="en-US"
        )
        gather = Gather(
            input="speech", action="/handle-speech",
            speech_timeout="auto", speech_model="phone_call", enhanced=True
        )
        resp.append(gather)
        return Response(str(resp), mimetype="text/xml")

    # ── Goodbye → summarize, notify, hang up ──
    goodbyes = [
        "goodbye", "bye", "thank you", "thanks", "that's all",
        "have a good day", "take care", "talk soon", "speak soon",
        "bye bye", "have a great day", "thanks for your help"
    ]
    if any(g in text.lower() for g in goodbyes):
        summary = generate_call_summary(sid)

        subject = (
            f"CAWDA Call — {summary.get('caller_name', 'Unknown')} "
            f"— {datetime.now().strftime('%b %d, %I:%M %p')}"
        )
        body = f"""📞 CALL SUMMARY
{'='*40}
Caller:     {summary.get('caller_name', 'Unknown')}
Company:    {summary.get('caller_company', 'N/A')}
Phone:      {summary.get('caller_phone', 'Unknown')}
Email:      {summary.get('caller_email', 'N/A')}
Service:    {summary.get('service_interest', 'Not specified')}
Budget:     {summary.get('budget', 'Not discussed')}
Urgency:    {summary.get('urgency', 'low')}

Reason:     {summary.get('reason', 'N/A')}
Action:     {summary.get('action_needed', 'None')}

Key Points:
{chr(10).join('- ' + p for p in summary.get('key_points', ['None']))}

---
CAWDA Creative · cawdacreates.com · hello@cawdacreates.com
"""
        send_email(subject, body)

        sms_text = (
            f"CAWDA: {summary.get('caller_name', 'Caller')} — "
            f"{summary.get('service_interest', summary.get('reason', 'call'))}"
        )[:160]
        send_sms(sms_text)

        resp = VoiceResponse()
        resp.pause(length=0.3)
        resp.say(
            "<speak>"
            "<prosody rate='medium'>"
            "Thanks for calling!"
            "</prosody>"
            "<break time='0.3s'/>"
            "Cameron will follow up with you soon."
            "<break time='0.4s'/>"
            "Check out the portfolio at "
            "<emphasis level='moderate'>cawdacreates.com</emphasis>."
            "<break time='0.5s'/>"
            "Have a great day!"
            "</speak>",
            voice="Polly.Ruth",
            language="en-US"
        )
        resp.hangup()

        conversations.pop(sid, None)
        return Response(str(resp), mimetype="text/xml")

    # ── Normal conversation turn ──
    ai_reply = get_ai_response(sid, text)

    resp = VoiceResponse()
    resp.pause(length=0.4)  # micro-pause before responding — feels human
    resp.say(
        f"<speak><prosody rate='medium'>{ai_reply}</prosody></speak>",
        voice="Polly.Ruth",
        language="en-US"
    )

    gather = Gather(
        input="speech", action="/handle-speech",
        speech_timeout="auto", speech_model="phone_call", enhanced=True
    )
    resp.append(gather)
    resp.redirect("/voice")

    return Response(str(resp), mimetype="text/xml")


@app.route("/status")
def status():
    """Health check for UptimeRobot."""
    return {"ok": True, "calls": len(conversations)}


if __name__ == "__main__":
    print("CAWDA Creative AI Receptionist — starting...")
    app.run(debug=True, port=5000)
