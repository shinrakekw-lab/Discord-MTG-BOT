import asyncio
import json
import logging
import os
import re
from datetime import time as datetime_time
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks

# ---- Configuration ----
# Set these environment variables in Bot-Hosting.net's startup/settings panel.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = int(os.getenv("CHANNEL_ID") or os.getenv("DISCORD_CHANNEL_ID") or "0")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")
POST_HOUR = int(os.getenv("POST_HOUR", "6"))
POST_MINUTE = int(os.getenv("POST_MINUTE", "0"))

# Your Bot-Hosting.net public domain is intentionally hard-coded here.
PUBLIC_WEBSITE_URL = os.getenv("PUBLIC_WEBSITE_URL") or "https://commanderoftheday.apps.bot-hosting.cloud"

SCRYFALL_RANDOM_URL = "https://api.scryfall.com/cards/random"
SCRYFALL_QUERY = "is:commander legal:commander"
USER_AGENT = "CommanderOfTheDayDiscordBot/1.0 (contact: configure-your-bot)"
STATE_FILE = Path("bot_state.json")

# All five MTG colours plus colourless, in Scryfall's canonical order.
#   W = White   U = Blue   B = Black   R = Red   G = Green   C = Colorless
COLOR_CODE_RE = re.compile(r"\{([^{}]*)\}")
COLOR_ORDER = "wubrgc"
VALID_COLORS = set(COLOR_ORDER)


def parse_color_identity(value: str) -> str:
    """Turn '{U}', '{W}{U}' or '{u}{b}' into Scryfall's colour code 'wu'.

    Braces are required, as in Scryfall's own notation, and every code holds
    exactly one of W U B R G C. Anything else raises ValueError so the command
    can tell the user what it expected.

    The result is always returned in canonical WUBRGC order regardless of the
    order the user typed the symbols in, e.g. '{U}{W}' -> 'wu'.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("empty")

    codes = COLOR_CODE_RE.findall(raw)
    leftover = COLOR_CODE_RE.sub("", raw).strip(" \t,/+-&")
    if not codes or leftover:
        raise ValueError(raw)

    letters: list[str] = []
    seen: set[str] = set()
    for code in codes:
        letter = code.strip().lower()
        if len(letter) != 1 or letter not in VALID_COLORS:
            raise ValueError("{" + code.strip() + "}")
        if letter not in seen:
            letters.append(letter)
            seen.add(letter)

    return "".join(char for char in COLOR_ORDER if char in seen)


def build_query(color: str | None = None, creature_type: str | None = None) -> str:
    """Combine the base Commander query with the optional filters.

    Colours match the exact colour identity (id=), so '{W}{U}' finds only
    commanders whose colour identity is precisely white+blue (Azorius), while
    '{B}' finds only mono-black commanders. '{C}' finds colourless commanders.

    All five MTG colours are supported: {W} {U} {B} {R} {G}, plus {C} for
    colourless. They can be freely combined, e.g. '{W}{U}{B}' for Esper.
    """
    query = SCRYFALL_QUERY
    if color and color.strip():
        query += f" id={parse_color_identity(color)}"
    if creature_type and creature_type.strip():
        cleaned = creature_type.strip().replace('"', "")
        if cleaned:
            query += f' t:"{cleaned}"'
    return query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("commander-of-the-day")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

http_session: aiohttp.ClientSession | None = None
last_card_id: str | None = None
last_post_date: str | None = None


def load_state() -> None:
    global last_card_id, last_post_date
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        last_card_id = data.get("last_card_id")
        last_post_date = data.get("last_post_date")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def save_state() -> None:
    STATE_FILE.write_text(
        json.dumps(
            {"last_card_id": last_card_id, "last_post_date": last_post_date},
            indent=2,
        ),
        encoding="utf-8",
    )


async def fetch_commander(query: str | None = None) -> dict:
    """Fetch a random Commander-legal commander from Scryfall."""
    assert http_session is not None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    scryfall_query = query or SCRYFALL_QUERY

    # Try a few times to avoid posting the same card on consecutive days.
    for _ in range(5):
        async with http_session.get(
            SCRYFALL_RANDOM_URL,
            params={"q": scryfall_query},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            card = await response.json()

        if card.get("object") == "error":
            raise RuntimeError(card.get("details", "Scryfall returned an error"))

        if card.get("id") != last_card_id:
            return card

    return card


def card_image_url(card: dict) -> str | None:
    images = card.get("image_uris") or {}
    if images.get("normal"):
        return images["normal"]

    # Double-faced cards often keep image URLs inside card_faces.
    for face in card.get("card_faces", []):
        face_images = face.get("image_uris") or {}
        if face_images.get("normal"):
            return face_images["normal"]
    return None


def make_embed(card: dict) -> discord.Embed:
    name = card.get("name", "Unknown card")
    embed = discord.Embed(
        title=f"🧙 Commander of the Day: {name}",
        url=card.get("scryfall_uri", "https://scryfall.com"),
        description=card.get("oracle_text") or "No rules text available.",
        color=discord.Color.from_rgb(125, 85, 190),
        timestamp=discord.utils.utcnow(),
    )

    if card.get("mana_cost"):
        embed.add_field(name="Mana cost", value=card["mana_cost"], inline=True)
    if card.get("type_line"):
        embed.add_field(name="Type", value=card["type_line"], inline=True)
    identity = card.get("color_identity", [])
    embed.add_field(
        name="Color identity",
        value="".join(identity) if identity else "Colorless",
        inline=True,
    )

    image = card_image_url(card)
    if image:
        embed.set_image(url=image)

    embed.add_field(
        name="Explore the card",
        value=f"[View on Scryfall]({card.get('scryfall_uri', 'https://scryfall.com')})"
              f" · [Bot website]({PUBLIC_WEBSITE_URL})",
        inline=False,
    )
    embed.set_footer(text="Card data and artwork via Scryfall")
    return embed


async def post_daily_commander() -> None:
    global last_card_id, last_post_date

    if not CHANNEL_ID:
        log.error("CHANNEL_ID is not configured; skipping today's post.")
        return

    tz = ZoneInfo(TIMEZONE)
    today = discord.utils.utcnow().astimezone(tz).date().isoformat()
    if last_post_date == today:
        log.info("Already posted today's Commander; skipping duplicate.")
        return

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except discord.DiscordException:
            log.exception("Could not access Discord channel ID %s", CHANNEL_ID)
            return

    try:
        card = await fetch_commander()
        await channel.send(embed=make_embed(card))
        last_card_id = card.get("id")
        last_post_date = today
        save_state()
        log.info("Posted Commander of the Day: %s", card.get("name"))
    except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, discord.DiscordException):
        log.exception("Could not fetch or post the Commander of the Day.")


@tasks.loop(time=datetime_time(hour=POST_HOUR, minute=POST_MINUTE, tzinfo=ZoneInfo(TIMEZONE)))
async def daily_commander() -> None:
    # Defaults to 13:00 Europe/Berlin; override with TIMEZONE, POST_HOUR, POST_MINUTE.
    await post_daily_commander()


@daily_commander.before_loop
async def before_daily_commander() -> None:
    await bot.wait_until_ready()


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "?")
    try:
        synced = await bot.tree.sync()
        log.info("Synced %d application command(s).", len(synced))
    except discord.HTTPException:
        log.exception("Could not sync application commands.")
    if not daily_commander.is_running():
        daily_commander.start()


@bot.command(name="commander")
@commands.guild_only()
async def commander_now(
    ctx: commands.Context,
    color: str | None = None,
    creature_type: str | None = None,
) -> None:
    """Manually post a Commander of the Day, e.g. !commander {W}{U} Elf."""
    try:
        query = build_query(color, creature_type)
    except ValueError as exc:
        await ctx.send(
            f"`{exc}` is not a colour code. Use Scryfall notation in braces, "
            "like {U} for blue, {W}{U} for Azorius or {C} for colorless. "
            "All five colours are supported: {W} {U} {B} {R} {G}, plus {C} for colorless."
        )
        return

    await ctx.typing()
    try:
        card = await fetch_commander(query)
        await ctx.send(embed=make_embed(card))
    except aiohttp.ClientResponseError as exc:
        if exc.status == 404:
            await ctx.send(
                "No commander matches those filters. Try a broader colour or creature type."
            )
        else:
            log.exception("!commander could not fetch a card from Scryfall.")
            await ctx.send("Sorry, I couldn't retrieve a card from Scryfall right now.")
    except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
        await ctx.send("Sorry, I couldn't retrieve a card from Scryfall right now.")


@bot.tree.command(
    name="commander",
    description="Post a random Commander of the Day, optionally filtered by colour and creature type",
)
@app_commands.describe(
    color="Colour identity in Scryfall braces, e.g. {U}, {W}{U}, {B}{R}{G}, {C}",
    creature_type="Creature type to filter by, e.g. Elf, Dragon",
)
async def commander_slash(
    interaction: discord.Interaction,
    color: str | None = None,
    creature_type: str | None = None,
) -> None:
    """Slash-command version of !commander.

    Works with the default intents, so it does not depend on the
    privileged message content intent.
    """
    await interaction.response.defer()
    try:
        query = build_query(color, creature_type)
    except ValueError as exc:
        await interaction.followup.send(
            f"`{exc}` is not a colour code. Use Scryfall notation in braces, "
            "like {U} for blue, {W}{U} for Azorius or {C} for colorless. "
            "All five colours are supported: {W} {U} {B} {R} {G}, plus {C} for colorless."
        )
        return

    try:
        card = await fetch_commander(query)
    except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
        log.exception("Slash command could not fetch a card from Scryfall.")
        await interaction.followup.send(
            "Sorry, I couldn't retrieve a card from Scryfall right now."
        )
        return

    await interaction.followup.send(embed=make_embed(card))


# Small HTTP server so the hosting platform has a health-check endpoint.
async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "service": "Commander of the Day Discord Bot",
            "website": PUBLIC_WEBSITE_URL,
            "discord_connected": not bot.is_closed() and bot.is_ready(),
        }
    )


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    # The platform hands the app its public port in SERVER_PORT.
    port = int(os.getenv("SERVER_PORT") or os.getenv("PORT") or "8080")
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health website listening on port %s (configured public URL: %s)", port, PUBLIC_WEBSITE_URL)
    return runner


async def main() -> None:
    global http_session
    if not DISCORD_TOKEN:
        raise RuntimeError("Missing DISCORD_TOKEN environment variable.")
    if not CHANNEL_ID:
        raise RuntimeError("Missing CHANNEL_ID environment variable.")

    load_state()
    http_session = aiohttp.ClientSession()
    runner = await start_web_server()
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    finally:
        await runner.cleanup()
        await http_session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
