#!/usr/bin/env python3
"""
Quick script to delete all existing rooms
"""
import asyncio
from livekit import api
import os
from dotenv import load_dotenv

load_dotenv(".env.local")

async def cleanup():
    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)

    print("🧹 Cleaning up all rooms...")
    response = await livekit_api.room.list_rooms(api.ListRoomsRequest())
    rooms = response.rooms if response.rooms else []

    if not rooms:
        print("✅ No rooms to clean up")
        return

    print(f"Found {len(rooms)} room(s) to delete:")
    for room in rooms:
        print(f"  - {room.name}")
        await livekit_api.room.delete_room(api.DeleteRoomRequest(room=room.name))
        print(f"    ✅ Deleted")

    print(f"\n✅ Cleaned up {len(rooms)} room(s)")
    await livekit_api.aclose()

if __name__ == "__main__":
    asyncio.run(cleanup())
