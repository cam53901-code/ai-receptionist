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

Services: custom web design, web development, brand identity, e-commerce (Shopify/WooCommerce), landing pages, maintenance and support.
Process: free discovery call, then design mockups, then build with unlimited revisions, then launch and support.
Pricing: custom-quoted per project. Flat-rate, no hidden fees. About 40% less than agencies.
Hours: Monday to Friday, 11 AM to 10 PM Eastern. Fully online across Canada and the US.
Website: cawdacreates.com
Email: hello@cawdacreates.com

RULES:
- Answer in ONE or TWO short sentences. Never more than two.
- Greet with your name and the business name. Ask how you can help.
- Collect: name, company, phone, email, service they need.
- Pricing questions: say every project is custom-quoted. Offer a free estimate. Mention cawdacreates.com.
- Discovery calls: get their availability. Cameron follows up within 24 hours.
- End every response with a question.
- Do not ramble. Do not list services unless asked.
- Use contractions. Sound like a real person."""

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
    except Exception as e:
        print(f"Gemini error: {e}")
        reply = "Sorry, could you repeat that?"

    # Hard guardrails against rambling
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

    print(f"[CALL] '{text}' (conf={conf})")

    if not text or conf < 0.3:
        resp = VoiceResponse()
        resp.say("Sorry, I missed that. Could you say it again?", voice="Polly.Joanna")
        gather = Gather(
            input="speech", action="/handle-speech",
            speech_timeout="5", speech_model="default", enhanced=True
        )
        resp.append(gather)
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
    resp.pause(length=0.5)
    resp.say(reply, voice="Polly.Joanna")

    gather = Gather(
        input="speech", action="/handle-speech",
        speech_timeout="5", speech_model="default", enhanced=True
    )
    resp.append(gather)
    resp.redirect("/voice")
    return Response(str(resp), mimetype="text/xml")


@app.route("/status")
def status():
    return {"ok": True, "calls": len(conversations)}


if __name__ == "__main__":
    app.run(debug=True, port=5000)
