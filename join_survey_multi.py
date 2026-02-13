#!/usr/bin/env python3
"""
Join a survey session with multiple participants
Creates a room and generates separate join links for each participant
"""
import asyncio
from livekit import api
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(".env.local")


async def create_multi_participant_survey(num_participants: int = 2):
    """Create room and generate tokens for multiple participants"""

    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)

    # Create room name
    room_name = f"survey-{datetime.now().strftime('%H%M%S')}"

    print(f"\n{'='*80}")
    print(f"Creating survey room: {room_name}")
    print(f"Number of participants: {num_participants}")
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
            api.CreateAgentDispatchRequest(room=room_name, agent_name="")
        )
        print(f"✅ Agent dispatched: {agent_dispatch.id}\n")
    except Exception as e:
        print(f"⚠️  Agent dispatch failed: {e}\n")

    # Default participant names - you can customize these
    default_names = [
        "Alice Johnson",
        "Bob Smith",
        "Carol White",
        "David Brown",
        "Emma Davis",
        "Frank Wilson",
        "Grace Lee",
        "Henry Taylor"
    ]

    # Generate tokens for each participant
    participant_links = []

    print(f"{'='*80}")
    print(f"PARTICIPANT JOIN LINKS")
    print(f"{'='*80}\n")

    for i in range(num_participants):
        participant_name = default_names[i] if i < len(default_names) else f"Participant {i+1}"

        # Generate participant token
        token = api.AccessToken(api_key, api_secret)
        token.with_identity(participant_name)
        token.with_name(participant_name)
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
        direct_url = f"https://meet.livekit.io/custom?liveKitUrl={encoded_url}&token={encoded_token}"

        participant_links.append({
            "name": participant_name,
            "url": direct_url,
            "token": participant_token
        })

        print(f"👤 PARTICIPANT {i+1}: {participant_name}")
        print(f"{'─'*80}")
        print(f"🔗 Click to join:")
        print(f"{direct_url}")
        print(f"\n")

    print(f"{'='*80}")
    print(f"📋 INSTRUCTIONS")
    print(f"{'='*80}")
    print(f"1. Each participant should click their own link above")
    print(f"2. Grant microphone permissions when prompted")
    print(f"3. Make sure to UNMUTE your microphone")
    print(f"4. Wait for the AI moderator to start speaking")
    print(f"5. The agent will call on you by name for each question")
    print(f"\n⏳ The agent will wait 2 minutes after greeting for everyone to join!\n")

    print(f"{'='*80}")
    print(f"🎯 HOW THE SURVEY WORKS")
    print(f"{'='*80}")
    print(f"- Agent will ask each question to participants in random order")
    print(f"- You'll be called by your name when it's your turn")
    print(f"- Each participant has ~20 seconds to respond")
    print(f"- After everyone answers, the agent moves to the next question")
    print(f"\n")

    # Keep script running
    print("Press Ctrl+C to exit and clean up the room...\n")
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
    print("AI SURVEY MODERATOR - Multi-Participant Test Session")
    print("="*80)

    # You can change this number to test with more participants
    num_participants = 2

    # Or accept from command line
    import sys
    if len(sys.argv) > 1:
        try:
            num_participants = int(sys.argv[1])
            if num_participants < 1 or num_participants > 8:
                print("⚠️  Number of participants must be between 1 and 8")
                num_participants = 2
        except ValueError:
            print("⚠️  Invalid number, using default: 2 participants")

    print(f"Setting up survey for {num_participants} participant(s)...")
    asyncio.run(create_multi_participant_survey(num_participants))
