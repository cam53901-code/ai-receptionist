"""
CAWDA Creative — AI Receptionist
cawdacreates.com | Solo creative studio
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

SYSTEM_PROMPT = """You are Alex, receptionist for CAWDA Creative — a solo-run studio that builds custom websites, brands, and e-commerce stores. No agency bloat, no middlemen. Clients work directly with Cameron, the founder.

Services: custom web design, web development, brand identity, e-commerce (Shopify/WooCommerce), landing pages, maintenance & support.
Process: free discovery call → design mockups → build with unlimited revisions → launch & support.
Pricing: custom-quoted per project. Flat-rate, no hidden fees. About 40% less than agencies.
Hours: Mon-Fri, 11AM-10PM Eastern. Fully online across Canada & US.
Website: cawdacreates.com — has the portfolio, pricing, and contact form.
Email: hello@cawdacreates.com

YOUR RULES:
- Answer in ONE to TWO short sentences only. Never more.
- Greet: "CAWDA Creative, this is Alex. How can I help?"
- Collect: name, company, phone, email, and what service they need.
- For pricing: "Every project is custom-quoted. I'll have Cameron send you a free estimate — you can also fill out the form at cawdacreates.com."
- For discovery calls: get their availability, Cameron follows up within 24 hours.
- End every response with a question to keep them talking.
- If you don't know: "Great question — Cameron can answer that directly. I'll have him reach out."
- Do not ramble. Do not list every service unless asked."""

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
conversations = {}


def get_ai_response(call_sid, user_text):
    if call_sid not in conversations:
        conversations[call_sid] = [
            {"role": "user", "parts": [SYSTEM_PROMPT]},
            {"role": "model", "parts": ["Got it. I'm Alex at CAWDA Creative."]}
        ]
    conversations[call_sid].append({"role": "user", "parts": [f"Caller: {user_text}"]})
    try:
        response = model.generate_content(conversations[call_sid])
        reply = response.text.strip()
        # Hard truncation — never let the AI ramble
        if len(reply) > 400:
            reply = reply[:397] + "..."
        sentences = reply.split(". ")
        if len(sentences) > 3:
            reply = ". ".join(sentences[:3]) + "."
    except Exception as e:
        print(f"AI error: {e}")
        reply = "I'm sorry, could you repeat that?"
    conversations[call_sid].append({"role": "model", "parts": [reply]})
    return reply


def generate_call_summary(call_sid):
    if call_sid not in conversations:
        return {"error": "No data"}
    prompt = f"""Extract JSON from this call. ONLY raw JSON, no backticks:
{{"caller_name":null,"caller_company":null,"caller_phone":null,"caller_email":null,"service_interest":null,"budget":null,"reason":"","key_points":[],"action_needed":null,"urgency":"low"}}

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


@app.route("/voice", methods=["GET", "POST"])
def voice():
    sid = request.form.get("CallSid", "unknown")
    if sid not in conversations:
        conversations[sid] = []
    resp = VoiceResponse()
    resp.say("CAWDA Creative, this is Alex. How can I help you?", voice="Polly.Joanna")
    g = Gather(input="speech", action="/handle-speech", speech_timeout="auto",
               speech_model="phone_call", enhanced=True)
    resp.append(g)
    resp.redirect("/voice")
    return Response(str(resp), mimetype="text/xml")


@app.route("/handle-speech", methods=["POST"])
def handle_speech():
    sid = request.form.get("CallSid", "?")
    text = request.form.get("SpeechResult", "").strip()
    conf = float(request.form.get("Confidence", "0"))

    print(f"[CALL] '{text}' (conf={conf})")

    if not text or conf < 0.3:
        resp = VoiceResponse()
        resp.say("Sorry, I missed that. Could you say it again?", voice="Polly.Joanna")
        g = Gather(input="speech", action="/handle-speech", speech_timeout="auto",
                   speech_model="phone_call", enhanced=True)
        resp.append(g)
        return Response(str(resp), mimetype="text/xml")

    goodbyes = ["goodbye", "bye", "thank you", "thanks", "that's all",
                "have a good day", "take care", "talk soon", "speak soon", "bye bye"]
    if any(g in text.lower() for g in goodbyes):
        summary = generate_call_summary(sid)
        subj = f"CAWDA — {summary.get('caller_name', 'Caller')} — {datetime.now().strftime('%b %d, %I:%M %p')}"
        body = f"""CALL SUMMARY
============
Caller:    {summary.get('caller_name', '?')}
Company:   {summary.get('caller_company', '?')}
Phone:     {summary.get('caller_phone', '?')}
Email:     {summary.get('caller_email', '?')}
Service:   {summary.get('service_interest', '?')}
Budget:    {summary.get('budget', '?')}
Urgency:   {summary.get('urgency', 'low')}
Reason:    {summary.get('reason', '?')}
Action:    {summary.get('action_needed', 'none')}

Points:
{chr(10).join('- '+p for p in summary.get('key_points', ['none']))}

cawdacreates.com | hello@cawdacreates.com
"""
        send_email(subj, body)
        send_sms(f"CAWDA: {summary.get('caller_name','?')} — {summary.get('service_interest', summary.get('reason','call'))}"[:160])

        resp = VoiceResponse()
        resp.say("Thanks for calling! Cameron will follow up soon. Check out the portfolio at cawdacreates.com. Have a great day!", voice="Polly.Joanna")
        resp.hangup()
        conversations.pop(sid, None)
        return Response(str(resp), mimetype="text/xml")

    reply = get_ai_response(sid, text)
    resp = VoiceResponse()
    resp.say(reply, voice="Polly.Joanna")
    g = Gather(input="speech", action="/handle-speech", speech_timeout="auto",
               speech_model="phone_call", enhanced=True)
    resp.append(g)
    resp.redirect("/voice")
    return Response(str(resp), mimetype="text/xml")


@app.route("/status")
def status():
    return {"ok": True, "calls": len(conversations)}


if __name__ == "__main__":
    app.run(debug=True, port=5000)
