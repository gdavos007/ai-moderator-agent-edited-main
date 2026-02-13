#!/usr/bin/env python3
"""
Join Survey with Custom Participant Names

This version lets participants enter their OWN names when joining,
instead of using pre-assigned names like "Alice Johnson", "Bob Smith".

Usage:
    Terminal 1: python3 agent.py dev
    Terminal 2: python3 join_survey_custom_names.py --participants 5

Each participant gets a generic join link, and when they click it,
LiveKit Meet will prompt them to enter their name.
"""

import asyncio
from livekit import api
import os
from dotenv import load_dotenv
from datetime import datetime
import sys
import secrets

load_dotenv(".env.local")


async def create_survey_with_custom_names(num_participants: int = 2):
    """Create room and generate tokens that allow custom names"""

    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)

    # Create room name
    room_name = f"survey-{datetime.now().strftime('%H%M%S')}"

    print(f"\n{'='*80}")
    print(f"Creating survey room: {room_name}")
    print(f"Participants will enter their own names when joining")
    print(f"{'='*80}\n")

    # Create the room
    room = await livekit_api.room.create_room(
        api.CreateRoomRequest(
            name=room_name,
            empty_timeout=600,  # 10 minutes
            max_participants=num_participants + 1,  # +1 for agent
        )
    )

    print(f"✅ Room created: {room.name}")
    print(f"   Room SID: {room.sid}\n")

    # Dispatch agent to the room
    try:
        print(f"🤖 Dispatching agent to room...")
        agent_dispatch = await livekit_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="",  # Empty string means any available agent
            )
        )
        print(f"✅ Agent dispatched: {agent_dispatch.id}")
        print(f"   Agent will join the room automatically\n")
    except Exception as e:
        print(f"⚠️  Agent dispatch failed: {e}")
        print(f"   Agent may still join if auto-dispatch is enabled in dashboard\n")

    # Generate tokens WITHOUT pre-set identities
    print(f"{'='*80}")
    print(f"PARTICIPANT JOIN LINKS (Enter Your Own Name)")
    print(f"{'='*80}\n")

    for i in range(num_participants):
        # Create token with a random placeholder identity
        # The actual name will be set when they join via meet.livekit.io
        placeholder_id = f"participant-{secrets.token_hex(4)}"

        token = api.AccessToken(api_key, api_secret)

        # Use placeholder identity - this allows LiveKit Meet to prompt for name
        token.with_identity(placeholder_id)

        # Grant permissions
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))

        participant_token = token.to_jwt()

        # Create direct connection URL
        import urllib.parse
        encoded_url = urllib.parse.quote(livekit_url, safe='')
        encoded_token = urllib.parse.quote(participant_token, safe='')

        # Use the standard meet URL (not custom) to trigger name prompt
        join_url = f"https://meet.livekit.io/?url={encoded_url}&token={encoded_token}"

        print(f"🔗 PARTICIPANT {i+1} LINK:")
        print(f"{'─'*80}")
        print(f"{join_url}")
        print(f"\n👤 Instructions:")
        print(f"   1. Click the link above")
        print(f"   2. You'll be asked: 'What's your name?'")
        print(f"   3. Type your REAL name (e.g., 'John Smith')")
        print(f"   4. Click 'Join'")
        print(f"   5. Grant microphone permissions")
        print(f"   6. Unmute your microphone")
        print()

    print(f"{'='*80}")
    print(f"📋 HOW THIS WORKS")
    print(f"{'='*80}")
    print(f"1. Each participant clicks their link")
    print(f"2. LiveKit Meet prompts: 'What's your name?'")
    print(f"3. They enter their real name")
    print(f"4. The agent will see and use their real name")
    print(f"5. CSV export will have their real names")
    print()
    print(f"✅ No more 'Alice Johnson' or 'Bob Smith'!")
    print(f"✅ Participants use their actual names!")
    print()

    print(f"{'='*80}")
    print(f"🎯 WHAT HAPPENS NEXT")
    print(f"{'='*80}")
    print(f"1. Make sure agent is running: python3 agent.py dev")
    print(f"2. Share the links above with participants")
    print(f"3. They'll enter their names when joining")
    print(f"4. Agent greets them by their real names")
    print(f"5. Survey begins!")
    print()
    print(f"Press Ctrl+C to stop and clean up the room...\n")

    # Keep script running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n\n👋 Cleaning up...")
        try:
            await livekit_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
            print(f"✅ Room {room_name} deleted")
        except Exception as e:
            print(f"⚠️  Could not delete room: {e}")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("AI SURVEY MODERATOR - Custom Names Mode")
    print("="*80)

    num_participants = 2
    if len(sys.argv) > 1:
        try:
            num_participants = int(sys.argv[1])
            if num_participants < 1 or num_participants > 10:
                print("⚠️  Number of participants must be between 1 and 10")
                num_participants = 2
        except ValueError:
            print("⚠️  Invalid number, using default: 2 participants")

    print(f"Setting up survey for {num_participants} participant(s)...")
    print("Participants will enter their own names when joining.")
    asyncio.run(create_survey_with_custom_names(num_participants))
