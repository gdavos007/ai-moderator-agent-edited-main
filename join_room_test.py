#!/usr/bin/env python3
"""
Join a room as a participant to trigger agent dispatch
"""
import asyncio
from livekit import api, rtc
import os
from dotenv import load_dotenv

load_dotenv(".env.local")


async def join_room_as_participant():
    """Join a room as a participant to trigger the agent"""

    # Create API client
    livekit_api = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET"),
    )

    room_name = "agent-test-with-participant"

    print(f"Creating room: {room_name}")

    # Create room
    await livekit_api.room.create_room(
        api.CreateRoomRequest(name=room_name, empty_timeout=300)
    )

    # Generate token for a test participant
    token = api.AccessToken(
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET"),
    )
    token.with_identity("test-user")
    token.with_name("Test User")
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    ))

    join_token = token.to_jwt()

    print(f"✅ Room created: {room_name}")
    print(f"🔑 Token generated for test user")
    print("")
    print("Connecting to room as a participant...")

    # Connect to the room
    room = rtc.Room()

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        print(f"✅ Participant joined: {participant.identity}")
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            print(f"🤖 AGENT JOINED! Identity: {participant.identity}")

    try:
        await room.connect(os.getenv("LIVEKIT_URL"), join_token)
        print(f"✅ Connected to room as Test User")
        print("")
        print("🔍 NOW CHECK YOUR AGENT TERMINAL!")
        print("   You should see: 'INFO - new job'")
        print("")
        print("⏳ Waiting 10 seconds for agent to join...")

        await asyncio.sleep(10)

        # Check participants
        print(f"\n📋 Participants in room:")
        for p in room.remote_participants.values():
            kind = "🤖 AGENT" if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT else "👤 User"
            print(f"   {kind}: {p.identity}")

        if len(room.remote_participants) == 0:
            print("   ⚠️  No other participants (agent didn't join)")

        print("\nDisconnecting...")
        await room.disconnect()

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(join_room_as_participant())
