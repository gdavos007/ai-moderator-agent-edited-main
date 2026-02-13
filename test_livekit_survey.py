#!/usr/bin/env python3
"""
Test Script: LiveKit-Direct Survey (No Zoom/Recall.ai)

This script tests your existing survey functionality using direct LiveKit connections.
No external services (Zoom, Recall.ai) required - everything runs on LiveKit + OpenAI.

Usage:
    Terminal 1: python3 agent.py dev
    Terminal 2: python3 test_livekit_survey.py --participants 2

This is the RECOMMENDED approach for:
- Lower costs (no per-minute charges)
- Better privacy (no 3rd party storage)
- Full control over data
"""

import asyncio
from livekit import api
import os
from dotenv import load_dotenv
from datetime import datetime
import sys

load_dotenv('.env.local')


async def create_livekit_survey(num_participants: int = 2):
    """
    Create a LiveKit room and generate join links for participants.

    This is your current working setup - no Zoom/Recall.ai needed!
    """

    livekit_url = os.getenv('LIVEKIT_URL')
    api_key = os.getenv('LIVEKIT_API_KEY')
    api_secret = os.getenv('LIVEKIT_API_SECRET')

    if not all([livekit_url, api_key, api_secret]):
        print("❌ Error: Missing LiveKit credentials in .env.local")
        print("   Required: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET")
        sys.exit(1)

    livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)

    # Create room name
    room_name = f"livekit-survey-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print("\n" + "="*80)
    print("🎯 LIVEKIT DIRECT SURVEY - No Zoom/Recall.ai Required")
    print("="*80)
    print(f"Room: {room_name}")
    print(f"Participants: {num_participants}")
    print("="*80 + "\n")

    # Create the room
    try:
        room = await livekit_api.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                empty_timeout=600,  # 10 minutes
                max_participants=num_participants + 1,  # +1 for agent
            )
        )
        print(f"✅ Room created: {room.name}")
        print(f"   Room SID: {room.sid}\n")
    except Exception as e:
        print(f"⚠️  Room might already exist: {e}")
        print(f"   Continuing with room: {room_name}\n")

    # Default participant names
    default_names = [
        "Alice Johnson",
        "Bob Smith",
        "Carol White",
        "David Brown",
        "Emma Davis",
    ]

    # Generate tokens for each participant
    print("="*80)
    print("🔗 PARTICIPANT JOIN LINKS")
    print("="*80 + "\n")

    participant_links = []

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
        print()

    print("="*80)
    print("📋 INSTRUCTIONS")
    print("="*80)
    print("1. Make sure the agent is running:")
    print("   Terminal 1: python3 agent.py dev")
    print()
    print("2. Each participant should click their own link above")
    print("3. Grant microphone permissions when prompted")
    print("4. Make sure to UNMUTE your microphone")
    print("5. Wait for the AI moderator to start speaking")
    print("6. The agent will call on you by name for each question")
    print()
    print("⏳ The agent will wait 30 seconds after greeting for everyone to join!")
    print()

    print("="*80)
    print("💰 COST COMPARISON")
    print("="*80)
    print("LiveKit Direct (this approach):")
    print("  - LiveKit: Free for development, ~$0.05/participant/hour in production")
    print("  - OpenAI: ~$0.10-0.15 per participant (STT/LLM/TTS)")
    print("  - Total: ~$0.15-0.20 per participant")
    print()
    print("Recall.ai Zoom Integration:")
    print("  - Recall.ai: $0.03/min × participants × duration")
    print(f"  - For {num_participants} participants, 1 hour: ${num_participants * 60 * 0.03:.2f}")
    print("  - Plus OpenAI: ~$0.10-0.15 per participant")
    print(f"  - Total: ~${num_participants * 60 * 0.03 + (num_participants * 0.15):.2f}")
    print()
    print("💡 LiveKit Direct is ~10x cheaper!")
    print()

    print("="*80)
    print("🎯 NEXT STEPS")
    print("="*80)
    print("1. Keep this terminal open")
    print("2. Click the participant links above to join")
    print("3. The survey will start automatically!")
    print()
    print("Press Ctrl+C to stop and clean up the room...")
    print()

    # Keep script running
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
    print("AI SURVEY MODERATOR - LiveKit Direct (Recommended)")
    print("="*80)

    # Get number of participants from command line
    num_participants = 2
    if len(sys.argv) > 1:
        try:
            num_participants = int(sys.argv[1])
            if num_participants < 1 or num_participants > 10:
                print("⚠️  Number of participants must be between 1 and 10")
                num_participants = 2
        except ValueError:
            print("⚠️  Invalid number, using default: 2 participants")

    print(f"\nSetting up survey for {num_participants} participant(s)...\n")
    asyncio.run(create_livekit_survey(num_participants))
