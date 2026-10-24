# Testing with a real model

The automated tests and conversation checks run on the offline `mock` model, which always behaves. Before calling the project done, I ran the agent against real models on Groq (`openai/gpt-oss-20b` for sorting messages and writing sales emails, `openai/gpt-oss-120b` for conversations that use tools) to see what actually happens.

Date: 17 October 2026. 7 patient messages and one sales email, 22 model calls in total, about ৳0.35 all together.

## What I sent and what came back

| Patient message | Result |
| --- | --- |
| Do I need an MRI for my lower back pain? | Searched the approved pages. "Usually an MRI isn't needed for typical low back pain unless there are red-flag warning signs... (Source: KB-LBP v1.0)" |
| আমার গোড়ালিতে সকালে ব্যথা হয়, কী করব? | Found the heel pain page and answered in Bangla, with the source |
| How do I treat neck pain? | That page is still a draft. It said there is no approved information yet and offered an assessment. No general advice |
| I want to book an appointment | Listed four free slots, numbered 1 to 4 |
| 2, my name is Rahim Uddin, 01712345678 | Booked option 2 (Sunday 18 October, 10:45), confirmed the fee and address. The booking was saved |
| Can I talk to a real person? | Handed over to staff. The escalation was saved |
| What are your fees and opening hours on Friday? | "Friday: closed", then the three fees |

The sales email for a sample clinic mentioned that its website lists WhatsApp but has no online booking, and asked for a 15-minute demo. It stayed under the word limit and made nothing up.

## Problems the real model found

The mock model could not have caught any of these. Each one is fixed, and each has a test in `agent/tests/test_real_model_fixes.py` so it stays fixed.

1. **English question, Bangla answer.** The search results included both languages, and the model answered in the language it read last. Now the search returns text only in the patient's language, and every turn tells the model which language to use.
2. **"2" did not book slot 2.** The model listed the slots as bullet points, so when the patient replied "2", it did not know which slot was meant and listed them again. The instructions now ask for a numbered list, and a reply with a number refers to that list.
3. **Wrong weekday in the booking confirmation.** It confirmed "Tuesday, 18 October", which is a Sunday. The booking result only had `2026-10-18 10:45`, so the model worked out the day itself and got it wrong. The result now spells out "Sunday 18 October 2026, 10:45".
4. **Friday hours made up.** The clinic is closed on Friday, but the opening hours simply left Friday out, and the model filled the gap with Thursday's hours. Now every day of the week is listed, closed days included.

Two smaller ones: the model used `**bold**`, which WhatsApp shows as stars (the instructions now ask for plain text), and it wrote `KB‑LBP` with a non-breaking hyphen (replies are now cleaned to plain characters).

One more change: Groq no longer serves `llama-3.1-8b-instant` on this account, so the small tier now uses `openai/gpt-oss-20b`.

## The rest of the stack

- **Docker on a normal PC:** on 24 October I ran the full stack with Docker Desktop 29.8 on a Windows 11 laptop. The app, Postgres and n8n were all started with `docker compose`. All 11 checks passed: a health answer with its source, a warning sign handed to staff, prompt injection blocked, a booking saved and visible both in the admin API and directly in Postgres, the admin API refusing requests without a token, and all 4 n8n workflows imported.
- **Docker:** a GitHub Actions job builds the full stack (app, Postgres, n8n) on every push. It runs `deploy/smoke-test.sh` against it (a health answer, a warning sign, an injection attempt, a booking saved in Postgres, the admin API refusing requests without a token) and then imports all four n8n workflows into the running n8n.
- **n8n:** I imported the four workflows into a normal n8n install and ran test copies of them:
  - The daily report pulled real numbers from the live demo and formatted the email. The send step was switched off.
  - In the events workflow, a wrong webhook secret was dropped at the first check. A correct one was routed to the calendar step.
  - That run also showed that n8n rejects `primary` as a calendar ID, so the docs now say to use the calendar's email-style ID.
  - With a real Google account connected, the calendar step created the booking as a calendar event with the right service, time and Dhaka time zone. The test event was then deleted. The daily report was also sent once through Gmail, to my own address, and Gmail confirmed it as sent.

## Live Google Maps data

I also ran the sales pipeline on real data: Apify's Google Maps actor, the four search queries in `agent/gtm/icp.yaml`, five places each. That gave 20 real clinics in Dhaka. Their websites were fetched through n8n to look for a WhatsApp link, an online booking form and a contact email. 7 of 11 sites loaded. The real clinics are not listed here, because the repository is public.

Real data showed two mistakes in the scoring that the made-up sample could not:

1. **Drug rehab counted as physio.** On Google Maps, "Rehabilitation center" also covers addiction treatment centres. One scored 85 and would have been emailed. Addiction centres are now disqualified.
2. **A physio clinic disqualified as a hospital.** Many small clinics put "Hospital" in their name. The check used to look at the name. Now it looks at the category Google lists first, so a "Physical therapy clinic" named "... Hospital" is scored normally, and a real hospital is still skipped.

Both have a test in `agent/tests/test_integrations.py`.

## WhatsApp, end to end

On 18 October I connected the live demo to a Meta WhatsApp test number and sent "Do I need an MRI for my lower back pain?" from a real phone. The message reached the demo through the webhook and passed the signature check. Groq wrote the answer from the approved page, and it came back on WhatsApp. Getting there took four fixes on the Meta side, listed in [whatsapp-setup.md](whatsapp-setup.md).
