# WhatsApp Business Cloud API setup

Meta's dashboard labels change from time to time. The steps below follow the official "Get started with the Cloud API" flow.

1. Create an app at developers.facebook.com (type **Business**) and add the **WhatsApp** product.
2. In **WhatsApp → API Setup**, copy the **Phone number ID** and a **temporary access token**. Add your own number as a test recipient.
3. Put them in `.env`:
   ```
   WHATSAPP_TOKEN=...
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_VERIFY_TOKEN=any-long-random-string
   WHATSAPP_APP_SECRET=...        # App settings → Basic → App secret
   ```
4. In **WhatsApp → Configuration → Webhook**, set:
   - Callback URL: `https://desk.example.com/webhooks/whatsapp`
   - Verify token: the same `WHATSAPP_VERIFY_TOKEN`

   Then subscribe to the **messages** field.
5. Send a WhatsApp message to the test number and watch `docker compose logs -f agent`.
6. **Production:** create a System User in Meta Business settings for a permanent token, and verify the business.
7. **Reminders (n8n workflow 02):** create a message template named `appointment_reminder` with three body variables (name, date-time, address) and wait for approval. Meta only allows templates for business-initiated messages outside the 24-hour customer-service window.

**Local testing without Meta:** leave `WHATSAPP_TOKEN` empty. Replies are logged instead of sent, and you can post a sample payload:

```bash
curl -X POST localhost:8000/webhooks/whatsapp -H 'Content-Type: application/json' -d '{"entry":[{"changes":[{"value":{"messages":[{"id":"wamid.test1","from":"8801712345678","type":"text","text":{"body":"What are your fees?"}}]}}]}]}'
```

## Things that tripped me up

Found while connecting the live demo to a Meta test number:

- **Link the WhatsApp account to your app.** Saving the webhook URL is not enough. Until you call `POST /{WABA_ID}/subscribed_apps` (Graph API Explorer works), real messages only show up in Meta's "Check test webhooks" panel and never reach your server.
- **Publish the app.** While it is unpublished, Meta sends only dashboard test events. Publishing needs a privacy policy URL. The demo serves one at `/privacy`.
- **Temporary tokens replace each other.** Every "Generate token" click cancels the previous one, and the server then gets `401 Authentication Error`. For anything long-lived, use a System User token.
- **Keep the token and the app secret apart.** The token is long and starts with `EAA`. The app secret is 32 hex characters. If they get swapped, every webhook fails the signature check with `401`.
- **Error 131030** means the reply went to a number that isn't on the test number's allowed list.
