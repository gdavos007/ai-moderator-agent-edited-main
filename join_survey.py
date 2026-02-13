#!/usr/bin/env python3
"""
Join a survey session - this creates a room that will automatically dispatch the agent
"""
import asyncio
from livekit import api, rtc
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(".env.local")


async def join_survey():
    """Create room, dispatch agent, and join as participant"""

    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)

    # Create room name
    room_name = f"survey-{datetime.now().strftime('%H%M%S')}"

    print(f"\n{'='*80}")
    print(f"Creating survey room: {room_name}")
    print(f"{'='*80}\n")

    # Create the room
    room = await livekit_api.room.create_room(
        api.CreateRoomRequest(
            name=room_name,
            empty_timeout=600,  # 10 minutes
            max_participants=10,
        )
    )

    print(f"✅ Room created: {room.name}")
    print(f"   Room SID: {room.sid}")

    # Dispatch agent to the room
    try:
        print(f"\n🤖 Dispatching agent to room...")
        agent_dispatch = await livekit_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="",  # Empty string means any available agent
            )
        )
        print(f"✅ Agent dispatched: {agent_dispatch.id}")
        print(f"   Agent will join the room automatically")
    except Exception as e:
        print(f"⚠️  Agent dispatch failed: {e}")
        print(f"   Agent may still join if auto-dispatch is enabled in dashboard")

    # Generate participant token
    token = api.AccessToken(api_key, api_secret)
    token.with_identity("Srini M")
    token.with_name("Srini M")
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    ))

    participant_token = token.to_jwt()

    # Create direct connection URL for meet.livekit.io
    import urllib.parse
    encoded_url = urllib.parse.quote(livekit_url, safe='')
    encoded_token = urllib.parse.quote(participant_token, safe='')
    direct_url = f"https://meet.livekit.io/custom?liveKitUrl={encoded_url}&token={encoded_token}"

    print(f"\n✨ CLICK THIS LINK to join (opens in browser):")
    print(f"{'='*80}")
    print(direct_url)
    print(f"{'='*80}\n")

    print(f"🎫 Or use this token in the playground:")
    print(f"{'='*80}")
    print(participant_token)
    print(f"{'='*80}\n")

    print(f"📋 To join via playground:")
    print(f"   1. Go to: https://agents-playground.livekit.io")
    print(f"   2. Manual tab")
    print(f"   3. URL: {livekit_url}")
    print(f"   4. Token: [paste from above]")
    print(f"   5. Click Connect")
    print(f"\n⏳ The agent will wait 20 seconds for you to join!\n")

    # Keep script running
    print("Press Ctrl+C to exit...\n")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n\n👋 Cleaning up...")
        # Delete the room
        try:
            await livekit_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
            print(f"✅ Room {room_name} deleted")
        except Exception as e:
            print(f"⚠️  Could not delete room: {e}")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("AI SURVEY MODERATOR - Test Session")
    print("="*80)
    asyncio.run(join_survey())
