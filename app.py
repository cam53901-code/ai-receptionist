"""
CAWDA Creative — AI Receptionist
=================================
Answers calls for cawdacreates.com | Solo-run creative studio
Web Design · Branding · E-Commerce · Landing Pages · Maintenance
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
# CAWDA CREATIVE — BUSINESS PERSONALITY
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are a friendly, knowledgeable receptionist for CAWDA Creative — a solo-run creative studio that builds beautiful websites and digital experiences without corporate bloat or agency pricing.

Your name is Alex. You work directly alongside Cameron, the founder and designer/developer. When clients hire CAWDA Creative, they work directly with Cameron — no middlemen, no account managers, no surprise invoices.

─── WHAT CAWDA CREATIVE DOES ───

Custom Web Design — Bespoke websites designed from scratch. Fully responsive, mobile-first, SEO-optimized. No templates, no cookie-cutter layouts.

Web Development — Clean, performant code. Modern tech stack, blazing fast, CMS integration.

Brand Identity — Logo design, color palettes, typography, brand guidelines, visual identity systems.

E-Commerce — Online stores via Shopify or WooCommerce. Payment integration, conversion optimization.

Landing Pages — High-conversion pages for campaigns, launches, and promotions. A/B testing ready, analytics integrated.

Maintenance & Support — Monthly updates, security patches, performance monitoring, content changes.

─── HOW IT WORKS ───

1. Discovery Call — Free, no-pressure conversation about the client's vision, goals, and budget. No sales pitch.
2. Design & Plan — Wireframes and mockups for approval before any code is written.
3. Build & Refine — Clean development with progress updates and unlimited revisions.
4. Launch & Support — Deployment, testing, and post-launch support.

─── PRICING ───

Flat-rate, transparent pricing. No hidden fees. Every project gets a custom quote because every project is different. Typically 40% less than agencies because there's no corporate overhead.

─── CONTACT INFO ───

Website: cawdacreates.com
Email: cawdacreates@gmail.com
Phone: (705) 994-7249
Hours: Monday through Friday, 11 AM to 10 PM Eastern Time
Location: 100% online — serving clients across Canada and the US

─── YOUR JOB ON EVERY CALL ───

1. Greet warmly: "Thank you for calling CAWDA Creative, this is Alex. How can I help you today?"
2. Ask what kind of project or service they're interested in
3. Collect: full name, company (if applicable), phone number, email, and a brief description of their project
4. If they ask about pricing: explain that every project is custom-quoted, offer to have Cameron send a free quote, and direct them to cawdacreates.com/contact to fill out the project form
5. If they want to book a discovery call: collect their availability and promise Cameron will reach out within 24 hours
6. For existing clients: ask for their name and project, let them know Cameron will follow up directly
7. Always mention the website (cawdacreates.com) at least once — it has the portfolio and contact form

─── TONE RULES ───

- Sound like a real human, not a corporate robot. CAWDA Creative is small, personal, and genuine.
- Be warm but efficient. Get to the point.
- Never over-promise on timelines or exact prices.
- If you don't know something: "Let me have Cameron reach out to you directly about that."
- For spam or sales calls: politely end the call quickly.
- Keep responses to 1-3 sentences maximum. This is a phone call."""

# ═══════════════════════════════════════════════════════════
# END BUSINESS PERSONALITY
# ═══════════════════════════════════════════════════════════

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
conversations = {}


def get_ai_response(call_sid, user_text):
    """Send full conversation to Gemini, get AI response back."""
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it. I'm Alex, ready to answer calls for CAWDA Creative."]}
        ]

    conversations[call_sid].append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        response = model.generate_content(conversations[call_sid])
        reply_text = response.text
    except Exception as e:
        print(f"Gemini error: {e}")
        reply_text = "I'm sorry, I'm having a bit of trouble on my end. Could you say that again?"

    conversations[call_sid].append({"role": "model", "parts": [reply_text]})
    return reply_text


def generate_call_summary(call_sid):
    """Ask Gemini to extract structured info from the full transcript."""
    if call_sid not in conversations:
        return {"error": "No conversation data"}

    prompt = f"""Extract a structured summary from this conversation.
Return ONLY valid JSON. No markdown, no backticks, no extra text.
Use this exact structure:
{{"caller_name": null, "caller_company": null, "caller_phone": null,
  "caller_email": null, "service_interest": null, "budget_mentioned": null,
  "reason": "", "key_points": [], "action_needed": null,
  "urgency": "low"}}

Conversation:
{json.dumps(conversations[call_sid], indent=2)}"""

    try:
        raw = model.generate_content(prompt).text.strip()
        for marker in ["```json", "```"]:
            raw = raw.replace(marker, "")
        return json.loads(raw.strip())
    except Exception as e:
        print(f"Summary parse error: {e}")
        return {"raw_summary": "Could not parse — check logs"}


# ─── NOTIFICATIONS ───────────────────────────────────────

def send_email_notification(subject, body):
    """Send email via Gmail SMTP."""
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
        print("Email sent OK")
    except Exception as e:
        print(f"Email failed: {e}")


def send_sms_notification(message_body):
    """Send SMS via email-to-SMS gateway (free)."""
    if not YOUR_SMS_GATEWAY:
        return
    try:
        msg = MIMEText(message_body[:160])
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = YOUR_SMS_GATEWAY
        msg["Subject"] = ""

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("SMS sent OK")
    except Exception as e:
        print(f"SMS failed: {e}")


# ─── TWILIO WEBHOOKS ─────────────────────────────────────

@app.route("/voice", methods=["POST"])
def voice():
    """Called by Twilio when someone dials the number."""
    call_sid = request.form.get("CallSid", "unknown")

    if call_sid not in conversations:
        conversations[call_sid] = []

    response = VoiceResponse()
    response.say(
        "Thank you for calling CAWDA Creative, this is Alex. How can I help you today?",
        voice="Polly.Joanna"
    )

    gather = Gather(
        input="speech",
        action="/handle-speech",
        speech_timeout="auto",
        speech_model="phone_call",
        enhanced=True,
    )
    response.append(gather)
    response.redirect("/voice")
    return Response(str(response), mimetype="text/xml")


@app.route("/handle-speech", methods=["POST"])
def handle_speech():
    """Called by Twilio every time the caller finishes speaking."""
    call_sid = request.form.get("CallSid", "unknown")
    speech_result = request.form.get("SpeechResult", "").strip()
    confidence = float(request.form.get("Confidence", "0"))

    print(f"[CALL {call_sid}] '{speech_result}' (confidence: {confidence})")

    # ── Poor recognition → ask to repeat ──
    if not speech_result or confidence < 0.3:
        response = VoiceResponse()
        response.say("Sorry, I missed that. Could you say that again?",
                     voice="Polly.Joanna")
        gather = Gather(
            input="speech", action="/handle-speech",
            speech_timeout="auto", speech_model="phone_call", enhanced=True
        )
        response.append(gather)
        return Response(str(response), mimetype="text/xml")

    # ── Goodbye → summarize, notify, hang up ──
    goodbye_phrases = [
        "goodbye", "bye", "thank you, goodbye", "thanks, bye",
        "that's all", "have a good day", "take care", "talk soon",
        "speak soon", "bye bye", "have a great day", "thanks for your help"
    ]
    if any(phrase in speech_result.lower() for phrase in goodbye_phrases):
        summary = generate_call_summary(call_sid)

        subject = f"CAWDA Call — {summary.get('caller_name', 'Unknown')} — {datetime.now().strftime('%b %d, %I:%M %p')}"
        body = f"""📞 NEW CALL SUMMARY
{'='*45}
Caller:       {summary.get('caller_name', 'Unknown')}
Company:      {summary.get('caller_company', 'N/A')}
Phone:        {summary.get('caller_phone', 'Unknown')}
Email:        {summary.get('caller_email', 'N/A')}
Interested In:{summary.get('service_interest', 'Not specified')}
Budget:       {summary.get('budget_mentioned', 'Not discussed')}
Urgency:      {summary.get('urgency', 'low')}

Reason:       {summary.get('reason', 'N/A')}
Action Needed:{summary.get('action_needed', 'None')}

Key Points:
{chr(10).join('- ' + p for p in summary.get('key_points', ['None']))}

---
CAWDA Creative · cawdacreates.com · hello@cawdacreates.com
"""
        send_email_notification(subject, body)

        # SMS (if gateway configured)
        sms_text = (
            f"CAWDA: {summary.get('caller_name', 'Caller')} — "
            f"{summary.get('service_interest', summary.get('reason', 'No details'))}"
        )[:160]
        send_sms_notification(sms_text)

        response = VoiceResponse()
        response.say(
            "Thanks for calling CAWDA Creative! Cameron will follow up with you soon. "
            "In the meantime, visit cawdacreates.com to see the portfolio. Have a great day!",
            voice="Polly.Joanna"
        )
        response.hangup()

        conversations.pop(call_sid, None)
        return Response(str(response), mimetype="text/xml")

    # ── Normal conversation turn ──
    ai_reply = get_ai_response(call_sid, speech_result)

    response = VoiceResponse()
    response.say(ai_reply, voice="Polly.Joanna")

    gather = Gather(
        input="speech", action="/handle-speech",
        speech_timeout="auto", speech_model="phone_call", enhanced=True
    )
    response.append(gather)
    response.redirect("/voice")

    return Response(str(response), mimetype="text/xml")


@app.route("/status", methods=["GET"])
def status():
    """Health check endpoint for UptimeRobot."""
    return {"status": "ok", "active_calls": len(conversations)}


if __name__ == "__main__":
    print("Starting CAWDA Creative AI Receptionist...")
    app.run(debug=True, port=5000)
