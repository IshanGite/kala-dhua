# Connector setup: where each key comes from

Console layouts change; the names below are the stable ones. Everything is stored in the OS keychain (macOS Keychain, Windows Credential
Manager), never in a file. Use a test account or workspace first. Read tokens and write tokens are separate: start with read only.

## Slack (about 5 min)
1. Open https://api.slack.com/apps and choose **Create New App**, then **From scratch**. Pick a workspace you own.
2. **OAuth & Permissions**, **Bot Token Scopes**, add: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `mpim:history`, `mpim:read`, `users:read`.
3. Click **Install to Workspace** and allow. Copy the **Bot User OAuth Token** (starts `xoxb-`). This is the read token.
4. In Slack, open the channels you want read and run `/invite @YourAppName`. A bot only sees conversations it belongs to.
5. Sending: the manifest in `docs/slack-app-manifest.json` now includes `chat:write`. For an existing app: OAuth & Permissions → add `chat:write` → **Reinstall to Workspace** → copy the (possibly new) Bot User OAuth Token → `ragnarok connect slack`. The bot must be invited to the channel it posts in.
6. Live push (optional, later): **Socket Mode** on, then **Basic Information**, **App-Level Tokens**, scope `connections:write` (starts `xapp-`).

## Telegram (about 2 min)
1. In Telegram, open @BotFather, send `/newbot`, choose a name and a username ending in `bot`.
2. Copy the token it prints (like `123456:ABC...`).
3. Open your bot and press Start, then send a message. A bot only sees messages sent to it or posted where it is a member.
4. For groups: add the bot, and in @BotFather `/setprivacy` choose Disable if it must see all group messages.
5. A bot cannot read your personal chats with other people. Forward messages to it, or use the notification reader below.

## Discord (about 5 min)
1. https://discord.com/developers/applications, **New Application**.
2. **Bot** tab, **Reset Token**, copy it. Turn on **Message Content Intent** (without it message text arrives empty).
3. **OAuth2**, **URL Generator**: scope `bot`, permissions *View Channels* and *Read Message History*. Open the URL and add the bot to a server you own.
4. A bot cannot read your personal DMs. Only servers it joined.

## Google: Gmail, Calendar, Drive, Meet (about 10 min, one setup covers all four)
1. https://console.cloud.google.com, create a project (top bar, **New project**).
2. **APIs & Services**, **Library**: enable **Gmail API**, **Google Calendar API**, **Google Drive API**, **Google Meet REST API**.
3. **APIs & Services**, **OAuth consent screen** (may be called Google Auth Platform): user type **External**, fill the app name and your email, then under **Audience / Test users** add your own Gmail address. Leave it in **Testing**.
4. **Data Access / Scopes**: add `gmail.readonly`, `calendar.readonly`, `drive.readonly`, `meetings.space.readonly`.
5. **Credentials**, **Create credentials**, **OAuth client ID**, application type **Desktop app**. Download the JSON.
6. Run `ragnarok connect google --client-file <that json>`. A browser opens, you sign in once, and the refresh token goes to the keychain.
7. Testing mode refresh tokens expire after 7 days; reconnect with the same command. Meet transcripts exist only if transcription was on in the meeting, on a Workspace plan.

## Outlook / Microsoft 365 (about 8 min)
1. https://entra.microsoft.com (or portal.azure.com), **App registrations**, **New registration**. Name it; accounts: *any organizational directory and personal Microsoft accounts*.
2. **Authentication**, **Add a platform**, **Mobile and desktop applications**, tick the `nativeclient` redirect, and set **Allow public client flows** to **Yes**.
3. **API permissions**, **Add**, Microsoft Graph, **Delegated**: `Mail.Read`, `Calendars.Read`, `offline_access`, `User.Read`.
4. Copy the **Application (client) ID** from Overview. There is no secret.
5. Run `ragnarok connect outlook --client-id <id>`; it prints a code and a URL (device sign-in). Work/school tenants may need an admin to approve the permissions.

## Local files
No key. `ragnarok connect files --folder <path>`; only named folders are read.

## Notifications (desktop)
No key. macOS asks for permission to read the notification store; Windows asks for notification access. See the notification section of the design.
