#!/usr/bin/env python3
"""
Create a room with explicit agent dispatch
"""
import asyncio
from livekit import api
import os
from dotenv import load_dotenv

load_dotenv(".env.local")


async def create_room_with_agent():
    """Create a room that explicitly requests the CommunityModerator agent"""

    livekit_api = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET"),
    )

    print("Creating room with agent dispatch...")

    # Create the room first
    room = await livekit_api.room.create_room(
        api.CreateRoomRequest(
            name="moderator-test-dispatch",
            empty_timeout=300,
            max_participants=10,
        )
    )

    print(f"Room created: {room.name}")
    print("Waiting 2 seconds for agent to join...")
    await asyncio.sleep(2)

    print(f"✅ Room created with agent dispatch!")
    print(f"   Room name: {room.name}")
    print(f"   Room SID: {room.sid}")
    print(f"   Agent requested: CommunityModerator")
    print("")
    print("🔍 NOW check your agent terminal!")
    print("   You SHOULD see: 'INFO - new job'")
    print("")
    print("⏳ Waiting 5 seconds...")
    await asyncio.sleep(5)
    print("")
    print("If agent joined, you can connect to this room from the sandbox!")


if __name__ == "__main__":
    asyncio.run(create_room_with_agent())
