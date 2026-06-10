"""
AI Receptionist - DIY Phone Answering Bot
=========================================
Uses: Twilio (telephony) + Google Gemini (AI) + Gmail (email/SMS notifications)

Set these environment variables:
  GEMINI_API_KEY         - Free from https://aistudio.google.com/apikey
  TWILIO_ACCOUNT_SID     - From Twilio console
  TWILIO_AUTH_TOKEN      - From Twilio console
  GMAIL_ADDRESS          - Your Gmail address (for sending notifications)
  GMAIL_APP_PASSWORD     - Gmail app password (NOT your regular password)
  YOUR_EMAIL             - Where to send call summaries
  YOUR_PHONE_CARRIER_GATEWAY - Optional: e.g., 5551234567@vtext.com for SMS
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import google.generativeai as genai

app = Flask(__name__)

# ─── CONFIGURATION ───────────────────────────────────────
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
YOUR_EMAIL = os.environ["YOUR_EMAIL"]
YOUR_PHONE_CARRIER_GATEWAY = os.environ.get("YOUR_PHONE_CARRIER_GATEWAY", "")

# ─── CUSTOMIZE THIS FOR YOUR BUSINESS ────────────────────
BUSINESS_PROMPT = """You are a friendly receptionist for CAWDA Creative Ltd.
Your job is to:
1. Greet the caller warmly
2. Ask what they're calling about
3. Collect their name, phone number, email, and reason for calling
4. Answer basic questions about the business
5. Promise a callback or follow-up

Business info:
- Name: CAWDA Creative Ltd.
- Hours: Mon – Sun, 11AM – 10PM EST
- Services: Web Design, Marketing, and AI Automation/integration
- Pricing: Varies; get a free quote at cawdacreates.com
- Location: 100% online

Be concise, warm, and professional. Speak like a human, not a robot.
Keep responses to 2-3 sentences maximum. This is a phone call, not an email."""

# ─── GEMINI AI SETUP ─────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# In-memory conversation store (use Redis or DB in production)
conversations = {}


def get_ai_response(call_sid, user_text):
    """Get AI response using Gemini, maintaining conversation context."""
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [BUSINESS_PROMPT]},
            {"role": "model", "parts": ["Understood. I'll act as the receptionist."]}
        ]

    conversations[call_sid].append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        response = model.generate_content(conversations[call_sid])
        reply_text = response.text
    except Exception as e:
        print(f"Gemini error: {e}")
        reply_text = "I'm sorry, I'm having trouble right now. Can you repeat that?"

    conversations[call_sid].append({"role": "model", "parts": [reply_text]})
    return reply_text


def generate_call_summary(call_sid):
    """Generate a structured JSON summary of the call."""
    if call_sid not in conversations:
        return {"error": "No conversation data"}

    summary_prompt = f"""Based on this conversation, extract a structured summary.
Return ONLY valid JSON (no markdown, no backticks) with these fields:
{{"caller_name": null or string, "caller_phone": null or string, 
 "caller_email": null or string, "reason": string, 
 "key_points": [string, ...], "action_needed": null or string}}

Conversation:
{json.dumps(conversations[call_sid], indent=2)}"""

    try:
        response = model.generate_content(summary_prompt)
        text = response.text.strip()
        # Strip any markdown code block markers
        for marker in ["```json", "```"]:
            text = text.replace(marker, "")
        return json.loads(text.strip())
    except Exception as e:
        print(f"Summary generation error: {e}")
        return {"raw_summary": response.text if 'response' in dir() else str(e)}


# ─── NOTIFICATIONS ───────────────────────────────────────

def send_email_notification(subject, body):
    """Send email via Gmail SMTP (free, using app password)."""
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
        print(f"[OK] Email sent to {YOUR_EMAIL}")
    except Exception as e:
        print(f"[FAIL] Email: {e}")


def send_sms_notification(message_body):
    """Send SMS via email-to-SMS gateway (free)."""
    if not YOUR_PHONE_CARRIER_GATEWAY:
        print("[SKIP] No SMS gateway configured")
        return

    try:
        msg = MIMEText(message_body[:160])  # Truncate to SMS limit
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = YOUR_PHONE_CARRIER_GATEWAY
        msg["Subject"] = ""

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[OK] SMS sent to {YOUR_PHONE_CARRIER_GATEWAY}")
    except Exception as e:
        print(f"[FAIL] SMS: {e}")


# ─── TWILIO WEBHOOKS ─────────────────────────────────────

@app.route("/voice", methods=["POST"])
def voice():
    """Main entry point for incoming calls."""
    call_sid = request.form.get("CallSid", "unknown")

    # Initialize conversation
    if call_sid not in conversations:
        conversations[call_sid] = []

    response = VoiceResponse()
    response.say(
        "Hello! Thank you for calling. I'm the automated receptionist. "
        "How can I help you today?",
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
    response.redirect("/voice")  # Loop back if no input

    return Response(str(response), mimetype="text/xml")


@app.route("/handle-speech", methods=["POST"])
def handle_speech():
    """Process speech input and generate AI response."""
    call_sid = request.form.get("CallSid", "unknown")
    speech_result = request.form.get("SpeechResult", "").strip()
    confidence = float(request.form.get("Confidence", "0"))

    print(f"[CALL {call_sid}] '{speech_result}' (confidence: {confidence})")

    # ── Handle poor recognition ──────────────────────────
    if not speech_result or confidence < 0.3:
        response = VoiceResponse()
        response.say("I'm sorry, I didn't catch that. Could you say it again?",
                     voice="Polly.Joanna")
        gather = Gather(
            input="speech", action="/handle-speech",
            speech_timeout="auto", speech_model="phone_call", enhanced=True
        )
        response.append(gather)
        return Response(str(response), mimetype="text/xml")

    # ── Handle goodbye / end of call ─────────────────────
    goodbye_phrases = ["goodbye", "bye", "thank you, goodbye", "thanks, bye",
                       "that's all", "have a good day", "take care"]
    if any(phrase in speech_result.lower() for phrase in goodbye_phrases):
        # Generate summary and send notifications
        summary = generate_call_summary(call_sid)

        subject = f"New Call Summary - {datetime.now().strftime('%b %d, %I:%M %p')}"
        body = f"""NEW CALL SUMMARY
==================
Caller:  {summary.get('caller_name', 'Unknown')}
Phone:   {summary.get('caller_phone', 'Unknown')}
Email:   {summary.get('caller_email', 'N/A')}
Reason:  {summary.get('reason', 'N/A')}
Action:  {summary.get('action_needed', 'None')}

Key Points:
{chr(10).join('- ' + p for p in summary.get('key_points', []))}
"""
        send_email_notification(subject, body)
        send_sms_notification(
            f"Call: {summary.get('caller_name', 'Unknown')} - "
            f"{summary.get('reason', 'N/A')[:80]}"
        )

        # Hang up gracefully
        response = VoiceResponse()
        response.say("Thank you for calling! We'll follow up shortly. Have a great day!",
                     voice="Polly.Joanna")
        response.hangup()

        # Cleanup
        conversations.pop(call_sid, None)
        return Response(str(response), mimetype="text/xml")

    # ── Normal conversation turn ─────────────────────────
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


# ─── LOCAL TESTING ───────────────────────────────────────
if __name__ == "__main__":
    print("Starting AI Receptionist (local mode)...")
    print("Use ngrok to expose: ngrok http 5000")
    app.run(debug=True, port=5000)
