import os
from logging import captureWarnings

import discord
from discord.ext import commands

import yt_dlp
import asyncio

import spotipy

import random

from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

spotify = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri="http://127.0.0.1:8888/callback",
        scope="playlist-read-private",
        cache_path=".spotify_cache"
    )
)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_on_network_error 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn"
}
YDL_OPTIONS = {
    "format": "bestaudio",
    "noplaylist": False
}

class MusicBot(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.queue = []
        self.current_song = None

    def extract_info(self, query):
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            return ydl.extract_info(query, download=False)


    @commands.command()
    async def play(self, ctx, *, search):
        print(f"play command received: {search}")

        voice_channel = ctx.author.voice.channel if ctx.author.voice else None
        if not voice_channel:
            return await ctx.send("You are not in a voice channel.")

        if not ctx.voice_client:
            voice_client = await voice_channel.connect()
        else:
            voice_client = ctx.voice_client

        async with ctx.typing():

            # checks if url is spotify or normal
            is_spotify = "open.spotify.com" in search

            is_url = search.startswith("http")

            print(f"is it spotify?: {is_spotify}")

            if is_spotify and "/track/" in search:

                # gets track data
                track = await asyncio.to_thread(spotify.track,search)

                self.queue.append({
                    "type": "spotify",
                    "title": track["name"],
                    "artist": track["artists"][0]["name"]
                })

                await ctx.send(
                    f"Added to queue: **{track['artists'][0]['name']} - {track['name']}**"
                )

            elif is_spotify and "/playlist/" in search:
                await self.add_spotify_playlist(ctx, search)

            elif is_spotify and "/album/" in search:
                await self.add_spotify_album(ctx, search)

            else:

                # YouTube link logic

                query = search if is_url else f"ytsearch5:{search}"

                info = await asyncio.to_thread(self.extract_info, query)

                if not is_url:
                    # plain text search

                    entries = info.get("entries", [])

                    if not entries:
                        return await ctx.send("No search results found.")

                    results = []

                    for i, entry in enumerate(entries, start=1):
                        results.append(f"{i}. {entry['title']}")

                    await ctx.send(
                        "Choose a song:\n" +
                        "\n".join(results) +
                        f"\n\nReply with a number from 1-{len(entries)}:."
                    )

                    def check(message):
                        return (
                            message.author == ctx.author and
                            message.channel == ctx.channel and
                            message.content.isdigit() and
                            1 <= int(message.content) <= len(entries)
                        )

                    try:
                        response = await self.client.wait_for(
                            "message",
                            timeout=30.0,
                            check=check
                        )
                    except asyncio.TimeoutError:
                        return await ctx.send("Search timed out.")

                    choice = int(response.content)
                    entry = entries[choice - 1]

                    self.queue.append({
                        "type": "youtube",
                        "source": entry.get("webpage_url") or entry.get("url"),
                        "title": entry["title"]
                    })

                    await ctx.send(f"Added to queue: **{entry['title']}**")


                elif 'entries' in info:
                    # playlist URL
                    added = 0
                    for entry in info['entries']:
                        if entry:
                            self.queue.append({
                                "type": "youtube",
                                "source": entry.get("webpage_url") or entry.get("url"),
                                "title": entry["title"]
                            })
                            added += 1
                    await ctx.send(f"Added **{added} songs** to queue")
                else:
                    # single video URL
                    self.queue.append({
                        "type": "youtube",
                        "source": info.get("webpage_url") or search,
                        "title": info["title"]
                    })
                    await ctx.send(f"Added to queue: **{info['title']}**")
        if not voice_client.is_playing():
            await self.play_next(ctx)

    @play.error
    async def play_error(self, ctx, error):
        print(f"Play command error: {repr(error)}")
        await ctx.send(f"Error: `{error}`")

    async def play_next(self, ctx):
        voice_client = ctx.voice_client

        if not voice_client:
            return
        if self.queue:
            # pops value and extracts url and title
            item = self.queue.pop(0)

            if item["type"] == "spotify":
                youtube_item = await self.spotify_to_youtube(item)

                if not youtube_item:
                    await ctx.send(
                        f"Couldn't find **{item['artist']} - {item['title']}** on YouTube."
                    )
                    return await self.play_next(ctx)

                item = youtube_item

            url = item["source"]
            title = item["title"]

            self.current_song = title

            info = await asyncio.to_thread(
                self.extract_info,
                url
            )
            stream_url = info["url"]

            # source = await discord.FFmpegOpus.from_url(url, **FFMPEG_OPTIONS)
            source = discord.FFmpegOpusAudio(
                stream_url,
                **FFMPEG_OPTIONS
            )

            def after_playing(error):
                if error:
                    print(f"Player error: {error}")

                asyncio.run_coroutine_threadsafe(
                    self.play_next(ctx),
                    self.client.loop
                )

            voice_client.play(source, after=after_playing)

            await ctx.send(f"Now playing **{title}**")

        elif not voice_client.is_playing():
            self.current_song = None
            await ctx.send("queue empty")

    @commands.command()
    async def nowplaying(self,ctx):
        if not self.current_song:
            return await ctx.send("Nothing is playing")

        await ctx.send(f"Now playing: **{self.current_song}**")

    @commands.command()
    async def skip(self, ctx):
        voice_client = ctx.voice_client
        if voice_client and  voice_client.is_playing():
            voice_client.stop()
            await ctx.send("Skipped")

    @commands.command()
    async def remove(self,ctx, index:int):
        if not self.queue:
            return await ctx.send("Queue is empty")

        if index < 1 or index > len(self.queue):
            return await ctx.send( f"Please choose a number between 1 and {len(self.queue)}.")

        removed = self.queue.pop(index - 1)

        if removed["type"] == "spotify":
            display = f"{removed['artist']} - {removed['title']}"
        else:
            display = removed["title"]

        await ctx.send(f"Removed **{display}** from the queue.")


    @commands.command()
    async def ping(self, ctx):
        await ctx.send("pong")

    @commands.command()
    async def pause(self, ctx):
        voice_client = ctx.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            await ctx.send("Stopped")

    @commands.command()
    async def resume(self, ctx):
        voice_client = ctx.voice_client
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            await ctx.send("Resuming...")

    @commands.command()
    async def clear(self, ctx):
        self.queue.clear()
        await ctx.send("Queue cleared")

    @commands.command()
    async def stop(self, ctx):
        voice_client = ctx.voice_client

        self.queue.clear()

        self.current_song = None

        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()

        await ctx.send("Stopped playback and cleared queue")

    @commands.command()
    async def shuffle(self, ctx):
        if not self.queue:
            return await ctx.send("Queue empty")

        random.shuffle(self.queue)

        await ctx.send("Queue shuffled")

    @commands.command(name="queue")
    async def display_queue(self, ctx):
        if not self.queue:
            return await ctx.send("Queue empty")

        songs = []

        for i, item in enumerate(self.queue[:10], start=1):
            if item["type"] == "spotify":
                display = f"{item['artist']} - {item['title']}"
            else:
                display = item["title"]

            songs.append(f"{i}. {display}")

        message = "**Current Queue:**\n" + "\n".join(songs)

        if len(self.queue) > 10:
            message += f"\n\n...and **{len(self.queue) - 10} more songs**."

        await ctx.send(message)

    async def spotify_to_youtube(self, item):

        query = f"ytsearch1:{item['artist']} {item['title']} official audio"

        info = await asyncio.to_thread(
            self.extract_info,
            query
        )

        entries = info.get("entries", [])

        if not entries:
            return None

        entry = entries[0]

        return {
            "source": entry.get("webpage_url") or entry.get("url"),
            "title": f"{item['artist']} - {item['title']}"
        }

    async def add_spotify_playlist(self, ctx, search):
        results = await asyncio.to_thread(
            spotify.playlist_items,
            search
        )

        added = 0

        for playlist_item in results['items']:
            track = playlist_item.get("item")

            if not track or track.get("type") != "track":
                continue

            self.queue.append({
                "type": "spotify",
                "title": track["name"],
                "artist": track["artists"][0]["name"]
            })

            added += 1

        # immediately starts playback

        voice_client = ctx.voice_client

        if voice_client and not voice_client.is_playing():
            await self.play_next(ctx)

        # Load pages

        while results.get("next"):
            results = await asyncio.to_thread(
                spotify.next,
                results
            )
            for playlist_item in results['items']:
                track = playlist_item.get("item")

                if not track or track.get("type") != "track":
                    continue

                self.queue.append({
                    "type": "spotify",
                    "title": track["name"],
                    "artist": track["artists"][0]["name"]
                })

                added += 1
        await ctx.send(f"Added **{added} Spotify songs** to queue")

    async def add_spotify_album(self, ctx, search):
        results = await asyncio.to_thread(
            spotify.album_tracks,
            search
        )

        added = 0

        for track in results['items']:
            if track.get("type") != "track":
                continue

            self.queue.append({
                "type": "spotify",
                "title": track["name"],
                "artist": track["artists"][0]["name"]
            })

            added += 1

        # immediately starts playback

        voice_client = ctx.voice_client

        if voice_client and not voice_client.is_playing():
            await self.play_next(ctx)

        # Load pages

        while results.get("next"):
            results = await asyncio.to_thread(
                spotify.next,
                results
            )
            for track in results['items']:
                if track.get("type") != "track":
                    continue

                self.queue.append({
                    "type": "spotify",
                    "title": track["name"],
                    "artist": track["artists"][0]["name"]
                })

                added += 1
        await ctx.send(f"Added **{added} Spotify songs** to queue")


client = commands.Bot(command_prefix="!", intents=intents)

async def main():
    await client.add_cog(MusicBot(client))
    await client.start(os.getenv("DISCORD_TOKEN"))

asyncio.run(main())

