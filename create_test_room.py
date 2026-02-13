#!/usr/bin/env python3
"""
Create a test room to trigger the agent
"""
import asyncio
from livekit import api
import os
from dotenv import load_dotenv

load_dotenv(".env.local")


async def create_test_room():
    """Create a test room for the agent to join"""

    livekit_api = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET"),
    )

    print("Creating test room...")

    # Create a room
    room = await livekit_api.room.create_room(
        api.CreateRoomRequest(
            name="agent-test-room",
            empty_timeout=300,  # 5 minutes
            max_participants=10,
        )
    )

    print(f"✅ Room created successfully!")
    print(f"   Room name: {room.name}")
    print(f"   Room SID: {room.sid}")
    print("")
    print("🔍 Check your agent terminal (running 'python agent.py dev')")
    print("   You should see: 'INFO - new job'")
    print("")
    print("💡 If the agent joins, it will wait for participants.")
    print("   Open your sandbox and connect to see the agent in action!")


if __name__ == "__main__":
    asyncio.run(create_test_room())
