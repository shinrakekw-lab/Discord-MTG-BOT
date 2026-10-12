# Commander of the Day Discord Bot

A Python Discord bot that posts one random Commander-legal Magic: The Gathering card every day at **6:00 Europe/Berlin**. Card data and artwork are fetched from Scryfall.

Your public website domain is hard-coded in `bot.py`:
`https://mj1cx6qutv.apps.bot-hosting.cloud`

The bot also serves `/` and `/health` on the port supplied by Bot-Hosting.net (the `PORT` environment variable, default `8080`). The hosting provider must route the configured domain to this process/port for the URL to be reachable. Hard-coding a URL alone cannot configure DNS or hosting-side routing.

## Files

- `bot.py` — Discord bot, Scryfall request, daily scheduler, and health endpoint.
- `requirements.txt` — Python dependencies.
- `bot_state.json` — created automatically to remember the last posted card/date.

## 1. Create and invite the Discord bot

1. Open the Discord Developer Portal: https://discord.com/developers/applications
2. Create an application, then open **Bot** and create a bot user.
3. Copy the bot token. Keep it secret; do not put it in public code or share it.
4. Under **OAuth2 → URL Generator**, select `bot` and the `Send Messages` and `Embed Links` permissions. Use the generated URL to invite the bot to your server.
5. Copy the ID of the channel where it should post: enable Discord Developer Mode, right-click the channel, and choose **Copy Channel ID**.

## 2. Configure Bot-Hosting.net

Upload `bot.py` and `requirements.txt` to your Python bot/server. Configure these environment variables in the hosting panel:

- `DISCORD_TOKEN` — your bot token
- `CHANNEL_ID` — numeric ID of the Discord channel to post in
- `PORT` — use the port required by the host (often provided automatically)
- `TIMEZONE` — optional; defaults to `Europe/Berlin`
- `POST_HOUR` — optional 24-hour hour; defaults to `13`
- `POST_MINUTE` — optional minute; defaults to `0`

Install dependencies with:

```bash
pip install -r requirements.txt
```

Set the startup file/command to:

```bash
python bot.py
```

Use a persistent/always-on process. The bot must remain running for the scheduled post to happen.

## 3. Schedule and test

The daily scheduler is set to **13:00 Europe/Berlin**, observing daylight-saving time automatically. The post is sent to the configured channel. To test manually after the bot is online, type:

```text
!commander
```

The manual command posts a card but does not change the scheduled daily-post state.

## 4. Website / health check

The bot responds to `GET /` and `GET /health` with JSON status. For example:

```json
{
  "status": "ok",
  "service": "Commander of the Day Discord Bot",
  "website": "https://mj1cx6qutv.apps.bot-hosting.cloud",
  "discord_connected": true
}
```

The code binds to `0.0.0.0` and the host's `PORT`. The Bot-Hosting.net panel must be configured to expose that port and attach the given domain. I cannot verify or change that hosting-side configuration from this project.

## Notes

- The bot uses Scryfall's random-card endpoint with the query `is:commander legal:commander`.
- Scryfall asks API clients to send a descriptive `User-Agent` and `Accept` header. Update the contact text in `bot.py` to a real contact or project identifier if desired.
- If the host restarts the process, the scheduler starts again and the state file prevents duplicate posting on the same local date, provided `bot_state.json` is stored persistently.
- Do not commit your bot token to a public repository.
