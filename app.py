"""
CAWDA Creative — AI Receptionist (Final)
Hangs up immediately. Summary + email happen in background.
"""

import os, json, smtplib, threading, traceback
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

SYSTEM_PROMPT = """You are Alex at CAWDA Creative. Collect these 6 answers, one at a time. One sentence per response.

Questions in order:
1. "What service are you looking for?"
2. "What's your name?"
3. "What's your email address?"
4. "What's your phone number?" (optional)
5. "What's your approximate budget?"
6. "Briefly describe your project."

After question 6, the call ends automatically. On the 6th response, simply acknowledge what they said in one sentence.

RULES:
- ONE sentence. Always.
- Never explain services unless asked: "Websites, branding, and e-commerce."
- If asked about pricing: "Cameron does custom quotes."
- If they go off topic: return to the next question.
- Never introduce yourself after the first turn.
- Keep responses under 150 characters."""

genai.configure(api_key=GEMINI_API_KEY)
conversations = {}
question_counts = {}

CLOSING = "That's everything. Cameron will send your custom quote within 24 hours. Thanks for calling CAWDA Creative."


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
            {"role": "user", "parts": [
                "Final answer. Acknowledge in one sentence. No questions. "
                f"Caller: {user_text}"
            ]},
        ]
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(prompt)
            reply = response.text.strip()
        except Exception as e:
            print(f"Gemini error: {e}")
            reply = "Got it, thanks for those details."

        if len(reply) > 300:
            reply = reply[:300].rsplit(" ", 1)[0]
        if not reply.endswith((".", "?", "!")):
            reply += "."
        return reply, True

    history = conversations[call_sid]
    if len(history) > 8:
        history = history[:2] + history[-6:]

    history.append({"role": "user", "parts": [f"Caller: {user_text}"]})

    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(history)
        reply = response.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        reply = "Could you repeat that?"

    if len(reply) > 250:
        reply = reply[:250].rsplit(" ", 1)[0]
    if not reply.endswith((".", "?", "!")):
        reply += "."

    question_counts[call_sid] = q_num + 1
    conversations[call_sid] = history
    conversations[call_sid].append({"role": "model", "parts": [reply]})
    return reply, False


def process_summary_and_email(sid, caller_text):
    """Runs in background thread. Generates summary, sends email.
    Does NOT affect the call — call already hung up."""
    try:
        # Save the final turn to conversation
        conversations[sid].append(
            {"role": "user", "parts": [f"Caller: {caller_text}"]}
        )

        # Generate summary
        prompt = f"""Extract JSON. ONLY raw JSON, no backticks:
{{"caller_name":null,"caller_phone":null,"caller_email":null,"service_interest":null,"budget":null,"project_description":null,"key_points":[],"action_needed":null}}

Conversation:
{json.dumps(conversations[call_sid], indent=2)}"""
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(prompt)
            raw = response.text.strip()
            for m in ["```json", "```"]:
                raw = raw.replace(m, "")
            summary = json.loads(raw.strip())
        except Exception as e:
            print(f"Summary failed: {e}")
            summary = {
                "caller_name": "Unknown",
                "caller_phone": "Unknown",
                "caller_email": "Unknown",
                "service_interest": "Unknown",
                "budget": "Unknown",
                "project_description": caller_text[:200],
                "key_points": [],
                "action_needed": "Send custom quote within 24 hours",
            }

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
        # Send email
        try:
            s = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
            s.starttls()
            s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            msg = MIMEMultipart()
            msg["From"] = GMAIL_ADDRESS
            msg["To"] = YOUR_EMAIL
            msg["Subject"] = subj
            msg.attach(MIMEText(body, "plain"))
            s.send_message(msg)
            s.quit()
            print("Email sent successfully")
        except Exception as e:
            print(f"Email failed (will retry on next call): {e}")
            # Save to a file as backup
            try:
                with open("/tmp/cawda_lead_backup.json", "a") as f:
                    f.write(json.dumps({"summary": summary, "timestamp": str(datetime.now())}) + "\n")
                print("Saved backup to /tmp/cawda_lead_backup.json")
            except:
                pass
    except Exception as e:
        print(f"Background processing error: {e}")
        traceback.print_exc()
    finally:
        conversations.pop(sid, None)
        question_counts.pop(sid, None)


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
    q = min(question_counts.get(sid, 0) + 1, 6)
    print(f"[AI Q{q}] {reply} {'(END)' if should_end else ''}")

    if should_end:
        # STEP 1: Build the TwiML response IMMEDIATELY
        resp = VoiceResponse()
        resp.say(reply, voice="Polly.Joanna")
        resp.pause(length=0.3)
        resp.say(CLOSING, voice="Polly.Joanna")
        resp.hangup()

        # STEP 2: Fire summary+email in background — AFTER we return
        # This thread will never block the response
        thread = threading.Thread(
            target=process_summary_and_email,
            args=(sid, text),
            daemon=True
        )
        thread.start()

        # STEP 3: Return TwiML immediately — caller hears goodbye now
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
