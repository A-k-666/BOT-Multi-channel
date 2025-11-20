import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from rag_chat_helpers import get_rag_chat_response

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN")
PREFIX = os.getenv("DISCORD_COMMAND_PREFIX", "!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready to respond!")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if bot.user in message.mentions:
        content = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if not content:
            content = "Hello!"
        try:
            reply = await get_rag_chat_response(content)
        except Exception as exc:
            reply = f"Sorry, something went wrong: {exc}"
        await message.channel.send(reply)

    await bot.process_commands(message)


@bot.command()
async def ping(ctx: commands.Context):
    await ctx.send("Pong! 🏓")


if __name__ == "__main__":
    if TOKEN == "YOUR_BOT_TOKEN":
        raise RuntimeError("Set DISCORD_BOT_TOKEN in environment or .env file.")
    bot.run(TOKEN)

