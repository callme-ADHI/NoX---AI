# Connecting Gmail to NoX

NoX integrates with Gmail via the official Google Workspace REST API using standard OAuth 2.0 PKCE (Proof Key for Code Exchange) flow designed for native/installed desktop applications.

---

## 1. Google Cloud Console Setup

1. **Create or Select a Project:**
   - Go to [Google Cloud Console](https://console.cloud.google.com/).
   - Create a project (e.g. `nox-personal-assistant`).

2. **Enable Gmail API:**
   - Under **APIs & Services > Library**, search for **Gmail API**.
   - Click **Enable**.

3. **Configure OAuth Consent Screen:**
   - Go to **APIs & Services > OAuth consent screen**.
   - Choose **External** (or Internal if using Google Workspace).
   - Enter App Name: `NoX Personal Assistant`.
   - Add your own email under **User support email** and **Developer contact information**.
   - Under **Scopes**, click **Add or Remove Scopes** and select:
     - `.../auth/gmail.readonly` (Read resources)
     - `.../auth/gmail.compose` (Create drafts)
   - Add your Google account under **Test users** so you can authorize without public verification.

4. **Create OAuth Client ID:**
   - Go to **APIs & Services > Credentials > Create Credentials > OAuth client ID**.
   - Application Type: **Desktop app**.
   - Name: `NoX CLI Client`.
   - Click **Create**.
   - Copy the **Client ID** and **Client Secret**.

---

## 2. Authorize via NoX CLI

Run:
```bash
nx connect gmail
```

NoX will prompt you for:
- **Client ID**
- **Client Secret**

NoX will generate a PKCE verifier/challenge pair and display an authorization URL:
1. Open the URL in your browser.
2. Sign in with your Google account.
3. Grant permissions for reading emails and creating drafts.
4. Copy the authorization code and paste it back into your terminal.

---

## 3. Verification & Security Invariants

Once authorized:
- **Zero Raw Secret Exposure:** Tokens and client secrets are stored in NoX's encrypted/chmod 0600 `SecretStore` (`~/.local/share/nox/connectors/secrets.json` or system keyring).
- **Default Read-Only:** Emails synced into NoX are marked `local_only` in SQLite.
- **PII Redaction:** Indian Aadhaar, PAN, UPI IDs, credit cards, and OTPs are redacted into reversible placeholders before storage.
- **Guarded Send:** NoX will never send an email automatically. Any sending action creates an item in `ApprovalsCenter` and requires human approval via `nx approvals approve <id>`.

To test synchronization:
```bash
nx connect list
nx mail sync
nx mail digest --period morning
```

To disconnect:
```bash
nx disconnect gmail
```
