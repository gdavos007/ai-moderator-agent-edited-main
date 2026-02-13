#!/usr/bin/env python3
"""
Create a room with agent dispatch and generate a participant token
"""
import asyncio
from livekit import api
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(".env.local")


async def create_room_and_join():
    """Create a room that dispatches agent and get participant token"""

    livekit_api = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET"),
    )

    room_name = f"survey-test-{datetime.now().strftime('%H%M%S')}"

    print(f"Creating room: {room_name}")

    # Create the room with agent dispatch
    room = await livekit_api.room.create_room(
        api.CreateRoomRequest(
            name=room_name,
            empty_timeout=300,
            max_participants=10,
        )
    )

    print(f"✅ Room created: {room.name}")
    print(f"   Room SID: {room.sid}")

    # Generate participant token
    token = api.AccessToken(
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET")
    )
    token.with_identity("Srini M")
    token.with_name("Srini M")
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    ))

    participant_token = token.to_jwt()

    print("")
    print("=" * 80)
    print("🎫 PARTICIPANT TOKEN (copy this):")
    print("=" * 80)
    print(participant_token)
    print("=" * 80)
    print("")
    print("📋 To join the room:")
    print(f"   1. Go to: https://agents-playground.livekit.io")
    print(f"   2. LiveKit URL: {os.getenv('LIVEKIT_URL')}")
    print(f"   3. Token: [paste the token above]")
    print("")
    print("⏳ The agent should join automatically when you connect!")
    print("   Check the agent terminal for: 'Agent starting - Room: {}'".format(room_name))
    print("")
    print("Press Ctrl+C to exit...")

    # Keep the script running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Exiting...")


if __name__ == "__main__":
    asyncio.run(create_room_and_join())
