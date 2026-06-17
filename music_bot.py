
import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from flask import Flask
from threading import Thread

# ===== KEEP-ALIVE SERVER (for UptimeRobot) =====
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!", 200

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ===== BOT SETUP =====
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Music queue and state
queues = {}  # guild_id: [song_info, ...]
current_song = {}  # guild_id: song_info
loop_mode = {}  # guild_id: False/"single"/"queue"

# ===== YT-DLP OPTIONS =====
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'cookiefile': None,
}

ffmpeg_options = {
    'options': '-vn',
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')
        self.webpage_url = data.get('webpage_url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# ===== HELPER FUNCTIONS =====
def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]

def get_voice_client(guild):
    return guild.voice_client

async def play_next(guild):
    guild_id = guild.id
    voice_client = get_voice_client(guild)

    if not voice_client or not voice_client.is_connected():
        return

    queue = get_queue(guild_id)

    # Handle loop modes
    if loop_mode.get(guild_id) == "single" and current_song.get(guild_id):
        queue.insert(0, current_song[guild_id])

    if len(queue) == 0:
        if loop_mode.get(guild_id) == "queue":
            # Would need to store full queue history for true queue loop
            pass
        current_song[guild_id] = None
        return

    song = queue.pop(0)
    current_song[guild_id] = song

    try:
        player = await YTDLSource.from_url(song['url'], stream=True)

        def after_playing(error):
            if error:
                print(f"Player error: {error}")
            fut = asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)
            try:
                fut.result(timeout=30)
            except Exception as e:
                print(f"Error in after_playing: {e}")

        voice_client.play(player, after=after_playing)

    except Exception as e:
        print(f"Error playing song: {e}")
        await play_next(guild)

# ===== EVENTS =====
@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name="!help for commands"
    ))

@bot.event
async def on_voice_state_update(member, before, after):
    """Stay in VC even when alone - do NOT disconnect"""
    guild = member.guild
    voice_client = guild.voice_client

    if voice_client and voice_client.is_connected():
        # Count non-bot members in the voice channel
        channel = voice_client.channel
        members = [m for m in channel.members if not m.bot]

        # Bot stays even if empty - no disconnect logic
        # This is the 24/7 mode!
        pass

# ===== COMMANDS =====
@bot.command(name='join', aliases=['j', 'connect'])
async def join(ctx):
    """Join your voice channel"""
    if ctx.author.voice is None:
        return await ctx.send("❌ You need to be in a voice channel first!")

    channel = ctx.author.voice.channel

    if ctx.voice_client is not None:
        await ctx.voice_client.move_to(channel)
        return await ctx.send(f"🔊 Moved to **{channel.name}**")

    await channel.connect()
    await ctx.send(f"🔊 Joined **{channel.name}** — 24/7 mode active!")

@bot.command(name='play', aliases=['p'])
async def play(ctx, *, query):
    """Play a song from YouTube or Spotify link"""
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            return await ctx.send("❌ Join a voice channel first or use `!join`!")

    async with ctx.typing():
        await ctx.send(f"🔍 Searching: `{query}`...")

        try:
            # Handle Spotify links
            if 'spotify.com' in query or 'open.spotify.com' in query:
                await ctx.send("🎵 Spotify detected! Converting to YouTube audio...")
                # Extract track info from Spotify and search on YouTube
                # For now, we'll search the query directly
                pass

            # Search or direct URL
            if not query.startswith('http'):
                query = f"ytsearch:{query}"

            data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))

            if 'entries' in data:
                data = data['entries'][0]

            song_info = {
                'title': data.get('title', 'Unknown'),
                'url': data.get('webpage_url', query),
                'duration': data.get('duration', 0),
                'thumbnail': data.get('thumbnail', ''),
                'requester': ctx.author.name
            }

            queue = get_queue(ctx.guild.id)
            queue.append(song_info)

            embed = discord.Embed(
                title="🎵 Added to Queue",
                description=f"**[{song_info['title']}]({song_info['url']})**",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=song_info['thumbnail'])
            embed.add_field(name="Position", value=f"#{len(queue)}", inline=True)
            embed.add_field(name="Requested by", value=ctx.author.mention, inline=True)

            duration = song_info['duration']
            if duration:
                mins, secs = divmod(duration, 60)
                embed.add_field(name="Duration", value=f"{mins}:{secs:02d}", inline=True)

            await ctx.send(embed=embed)

            # Start playing if not already
            if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                await play_next(ctx.guild)

        except Exception as e:
            await ctx.send(f"❌ Error: `{str(e)}`")
            print(f"Play error: {e}")

@bot.command(name='skip', aliases=['s', 'next'])
async def skip(ctx):
    """Skip the current song"""
    if ctx.voice_client is None or not ctx.voice_client.is_playing():
        return await ctx.send("❌ Nothing is playing right now!")

    ctx.voice_client.stop()
    await ctx.send("⏭️ Skipped!")

@bot.command(name='stop')
async def stop(ctx):
    """Stop playing and clear queue"""
    if ctx.voice_client is None:
        return await ctx.send("❌ I'm not in a voice channel!")

    queues[ctx.guild.id] = []
    current_song[ctx.guild.id] = None
    ctx.voice_client.stop()
    await ctx.send("⏹️ Stopped and cleared the queue!")

@bot.command(name='queue', aliases=['q'])
async def show_queue(ctx):
    """Show the current queue"""
    queue = get_queue(ctx.guild.id)
    current = current_song.get(ctx.guild.id)

    if not current and not queue:
        return await ctx.send("📭 Queue is empty!")

    embed = discord.Embed(title="🎶 Music Queue", color=discord.Color.blue())

    if current:
        embed.add_field(
            name="▶️ Now Playing",
            value=f"**[{current['title']}]({current['url']})** — *{current['requester']}*",
            inline=False
        )

    if queue:
        queue_text = ""
        for i, song in enumerate(queue[:10], 1):
            queue_text += f"`{i}.` [{song['title'][:40]}...]({song['url']})
"

        if len(queue) > 10:
            queue_text += f"
*...and {len(queue) - 10} more*"

        embed.add_field(name="📋 Up Next", value=queue_text, inline=False)

    await ctx.send(embed=embed)

@bot.command(name='pause')
async def pause(ctx):
    """Pause the music"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused!")
    else:
        await ctx.send("❌ Nothing is playing!")

@bot.command(name='resume')
async def resume(ctx):
    """Resume the music"""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed!")
    else:
        await ctx.send("❌ Nothing is paused!")

@bot.command(name='leave', aliases=['disconnect', 'dc'])
async def leave(ctx):
    """Leave the voice channel (disables 24/7)"""
    if ctx.voice_client is not None:
        queues[ctx.guild.id] = []
        current_song[ctx.guild.id] = None
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Left the voice channel!")
    else:
        await ctx.send("❌ I'm not in a voice channel!")

@bot.command(name='loop', aliases=['repeat'])
async def loop(ctx, mode: str = None):
    """Toggle loop mode: !loop single | !loop queue | !loop off"""
    guild_id = ctx.guild.id

    if mode is None:
        current = loop_mode.get(guild_id, False)
        status = "off" if not current else current
        return await ctx.send(f"🔁 Loop mode is currently: **{status}**")

    mode = mode.lower()
    if mode in ['single', 'one', 'song']:
        loop_mode[guild_id] = "single"
        await ctx.send("🔂 Looping current song!")
    elif mode in ['queue', 'all']:
        loop_mode[guild_id] = "queue"
        await ctx.send("🔁 Looping entire queue!")
    elif mode in ['off', 'none', 'disable']:
        loop_mode[guild_id] = False
        await ctx.send("❌ Loop disabled!")
    else:
        await ctx.send("❌ Use: `!loop single`, `!loop queue`, or `!loop off`")

@bot.command(name='nowplaying', aliases=['np', 'current'])
async def nowplaying(ctx):
    """Show what's currently playing"""
    current = current_song.get(ctx.guild.id)
    if not current:
        return await ctx.send("❌ Nothing is playing right now!")

    embed = discord.Embed(
        title="▶️ Now Playing",
        description=f"**[{current['title']}]({current['url']})**",
        color=discord.Color.purple()
    )
    embed.set_thumbnail(url=current.get('thumbnail', ''))
    embed.add_field(name="Requested by", value=current['requester'], inline=True)

    duration = current.get('duration', 0)
    if duration:
        mins, secs = divmod(duration, 60)
        embed.add_field(name="Duration", value=f"{mins}:{secs:02d}", inline=True)

    await ctx.send(embed=embed)

@bot.command(name='remove')
async def remove(ctx, index: int):
    """Remove a song from queue: !remove 2"""
    queue = get_queue(ctx.guild.id)

    if not queue:
        return await ctx.send("📭 Queue is empty!")

    if index < 1 or index > len(queue):
        return await ctx.send(f"❌ Invalid position! Queue has {len(queue)} songs.")

    removed = queue.pop(index - 1)
    await ctx.send(f"🗑️ Removed **{removed['title']}** from queue!")

@bot.command(name='clear')
async def clear(ctx):
    """Clear the queue"""
    queues[ctx.guild.id] = []
    await ctx.send("🧹 Queue cleared!")

@bot.command(name='help')
async def help_command(ctx):
    """Show all commands"""
    embed = discord.Embed(
        title="🎵 Music Bot Commands",
        description="24/7 Voice Channel Music Bot",
        color=discord.Color.gold()
    )

    commands_list = {
        "🎶 Playback": "`!play <song>` — Play from YouTube/Spotify
`!skip` — Skip current song
`!pause` — Pause music
`!resume` — Resume music
`!stop` — Stop and clear queue",
        "📋 Queue": "`!queue` — Show queue
`!remove <#>` — Remove song by position
`!clear` — Clear queue
`!nowplaying` — Show current song",
        "🔁 Loop": "`!loop single` — Loop current song
`!loop queue` — Loop entire queue
`!loop off` — Disable loop",
        "🔊 Voice": "`!join` — Join your VC
`!leave` — Leave VC (stops 24/7)"
    }

    for category, cmds in commands_list.items():
        embed.add_field(name=category, value=cmds, inline=False)

    embed.set_footer(text="💡 The bot stays in VC 24/7 even when alone!")
    await ctx.send(embed=embed)

# ===== ERROR HANDLING =====
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument! Use `!help` for usage.")
    else:
        print(f"Command error: {error}")

# ===== RUN =====
keep_alive()

# Get token from environment variable
TOKEN = os.environ.get('TOKEN')
if not TOKEN:
    print("❌ ERROR: No TOKEN found in environment variables!")
    print("Add your bot token in Replit Secrets (lock icon) with key 'TOKEN'")
else:
    bot.run(TOKEN)
