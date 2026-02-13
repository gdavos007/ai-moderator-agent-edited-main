#!/usr/bin/env python3
"""
Start Fresh Multi-Participant Survey with Auto-Cleanup

This script AUTOMATICALLY handles everything:
0. Kills any old agent.py dev processes (prevents dispatch conflicts!)
1. Cleans up ALL old rooms automatically
2. Creates a fresh new room
3. Dispatches agent to the new room
4. Generates clickable join links for participants

Usage:
    Step 1: Run this script first (it will kill old agents and prompt you)
            python3 start_fresh_survey.py --participants 3

    Step 2: Start fresh agent when prompted
            python3 agent.py dev

    Step 3: Run this script again after "registered worker" appears
            python3 start_fresh_survey.py --participants 3

OR if no agent is running:
    Terminal 1: python3 agent.py dev
    Terminal 2: python3 start_fresh_survey.py --participants 3

This ensures you ALWAYS start with a clean slate - no more dispatch conflicts!
"""

import asyncio
from livekit import api
import os
from dotenv import load_dotenv
from datetime import datetime
import sys
import argparse
import urllib.parse
import subprocess
import signal

load_dotenv(".env.local")


def cleanup_old_agents():
    """Kill any old agent.py dev processes to prevent dispatch conflicts

    Strategy:
    - 0 agents: Tell user to start one
    - 1 agent: Perfect! Keep it running
    - 2+ agents: Kill ALL (conflict situation) and tell user to start fresh

    Also cleans up orphaned multiprocessing forkserver child processes
    """
    print(f"\n{'='*80}")
    print(f"🔧 CHECKING FOR AGENT PROCESSES")
    print(f"{'='*80}\n")

    try:
        # Find all agent.py dev processes
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True
        )

        agent_processes = []
        for line in result.stdout.split('\n'):
            # Match any line with 'agent.py dev' (ignoring case and whitespace variations)
            # Exclude grep processes and this cleanup script itself
            if ('agent.py' in line and
                'dev' in line and
                'grep' not in line and
                'start_fresh_survey.py' not in line):
                parts = line.split()
                if len(parts) >= 2:
                    pid = parts[1]
                    # Verify this is a valid PID (numeric)
                    if pid.isdigit():
                        agent_processes.append(pid)

        num_agents = len(agent_processes)

        if num_agents == 0:
            print("❌ No agent process found!")
            print("⏳ Please start the agent first:")
            print("   Terminal 1: python3 agent.py dev")
            print("   Wait for 'registered worker' message")
            print("   Then run this script again\n")
            sys.exit(0)

        elif num_agents == 1:
            print(f"✅ Found 1 agent process (PID: {agent_processes[0]})")
            print(f"✅ Perfect! Only one agent running - proceeding...\n")
            return  # Keep the single agent running

        else:  # Multiple agents found - CONFLICT!
            print(f"⚠️  Found {num_agents} agent processes:")
            for pid in agent_processes:
                print(f"  - PID: {pid}")

            print(f"\n⚠️  WARNING: Multiple agents cause dispatch conflicts!")
            print(f"🔧 Killing ALL agent processes...\n")

            killed = 0
            failed = []
            for pid in agent_processes:
                try:
                    # Try SIGTERM first (graceful)
                    os.kill(int(pid), signal.SIGTERM)
                    print(f"  🔄 Sent SIGTERM to process: {pid}")
                    killed += 1
                except ProcessLookupError:
                    print(f"  ⚠️  Process {pid} already gone")
                    killed += 1
                except PermissionError:
                    print(f"  ❌ Cannot kill process {pid} (permission denied)")
                    failed.append(pid)
                except Exception as e:
                    print(f"  ⚠️  Error killing {pid}: {e}")
                    failed.append(pid)

            # Wait a moment for processes to terminate
            if killed > 0:
                print(f"\n  ⏳ Waiting 2 seconds for processes to terminate...")
                import time
                time.sleep(2)

                # Check which processes are still alive and force kill them
                still_alive = []
                for pid in agent_processes:
                    if pid in failed:
                        continue
                    try:
                        # Check if process still exists
                        os.kill(int(pid), 0)  # Signal 0 just checks existence
                        still_alive.append(pid)
                    except ProcessLookupError:
                        # Process is gone, good!
                        pass

                # Force kill any remaining processes
                if still_alive:
                    print(f"\n  ⚠️  {len(still_alive)} process(es) still running, using SIGKILL...")
                    for pid in still_alive:
                        try:
                            os.kill(int(pid), signal.SIGKILL)
                            print(f"  💀 Force killed process: {pid}")
                        except Exception as e:
                            print(f"  ❌ Could not force kill {pid}: {e}")
                            failed.append(pid)

            if killed > 0:
                print(f"\n✅ Killed {killed} agent process(es)")

                # Also clean up orphaned multiprocessing forkserver child processes
                print(f"\n🧹 Cleaning up orphaned child processes...")
                try:
                    # Find orphaned forkserver processes
                    orphan_result = subprocess.run(
                        ["ps", "aux"],
                        capture_output=True,
                        text=True
                    )

                    orphan_pids = []
                    for line in orphan_result.stdout.split('\n'):
                        if 'multiprocessing.forkserver' in line and 'livekit' in line:
                            parts = line.split()
                            if len(parts) >= 2 and parts[1].isdigit():
                                orphan_pids.append(parts[1])

                    if orphan_pids:
                        print(f"  Found {len(orphan_pids)} orphaned child process(es)")
                        for pid in orphan_pids:
                            try:
                                os.kill(int(pid), signal.SIGKILL)
                                print(f"  💀 Killed orphaned process: {pid}")
                            except Exception as e:
                                print(f"  ⚠️  Could not kill orphan {pid}: {e}")
                        print(f"  ✅ Orphaned processes cleaned up")
                    else:
                        print(f"  ✅ No orphaned processes found")
                except Exception as e:
                    print(f"  ⚠️  Error cleaning orphaned processes: {e}")

                print(f"\n⏳ Now start ONE fresh agent:")
                print(f"   Terminal 1: python3 agent.py dev")
                print(f"   Wait for 'registered worker' message")
                print(f"   Then run this script again\n")
                sys.exit(0)

    except Exception as e:
        print(f"⚠️  Error checking for agent processes: {e}\n")


async def cleanup_all_rooms(livekit_api):
    """Delete all existing rooms to ensure clean start"""
    print(f"\n{'='*80}")
    print(f"🧹 CLEANING UP OLD ROOMS")
    print(f"{'='*80}\n")

    try:
        # List all rooms
        rooms = await livekit_api.room.list_rooms(api.ListRoomsRequest())

        if not rooms:
            print("✅ No old rooms to clean up\n")
            return

        print(f"Found {len(rooms)} room(s) to clean up:")
        for room in rooms:
            print(f"  - {room.name} (created: {room.creation_time})")

        # Delete all rooms
        deleted = 0
        for room in rooms:
            try:
                await livekit_api.room.delete_room(api.DeleteRoomRequest(room=room.name))
                print(f"  ✅ Deleted: {room.name}")
                deleted += 1
            except Exception as e:
                print(f"  ⚠️  Could not delete {room.name}: {e}")

        print(f"\n✅ Cleaned up {deleted}/{len(rooms)} room(s)\n")

    except Exception as e:
        print(f"⚠️  Error listing rooms: {e}\n")


async def start_fresh_survey(num_participants: int = 2):
    """Clean up old rooms and start fresh survey"""

    # Step 0: Kill any old agent processes (must be done first!)
    cleanup_old_agents()

    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)

    # Step 1: Clean up all old rooms
    await cleanup_all_rooms(livekit_api)

    # Step 2: Create fresh room
    room_name = f"survey-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print(f"{'='*80}")
    print(f"🆕 CREATING FRESH SURVEY ROOM")
    print(f"{'='*80}\n")
    print(f"Room Name: {room_name}")
    print(f"Participants: {num_participants}\n")

    room = await livekit_api.room.create_room(
        api.CreateRoomRequest(
            name=room_name,
            empty_timeout=3600,  # 1 hour
            max_participants=num_participants + 2,
        )
    )

    print(f"✅ Room created: {room.name}")
    print(f"   Room SID: {room.sid}\n")

    # Step 3: Dispatch agent
    print(f"🤖 Dispatching agent to room...")
    try:
        agent_dispatch = await livekit_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(room=room_name, agent_name="")
        )
        print(f"✅ Agent dispatched: {agent_dispatch.id}")
        print(f"   The agent should join within 2-3 seconds\n")
    except Exception as e:
        print(f"⚠️  Agent dispatch failed: {e}")
        print(f"   Make sure 'python3 agent.py dev' is running in Terminal 1\n")
        return

    # Step 4: Generate participant tokens
    participant_names = [
        "Alice Johnson", "Bob Smith", "Carol Williams", "David Brown",
        "Emma Davis", "Frank Miller", "Grace Wilson", "Henry Moore"
    ]

    print(f"{'='*80}")
    print(f"🔗 PARTICIPANT JOIN LINKS")
    print(f"{'='*80}\n")

    for i in range(num_participants):
        name = participant_names[i] if i < len(participant_names) else f"Participant {i+1}"
        identity = name.lower().replace(" ", "_")

        # Create token
        token = api.AccessToken(api_key, api_secret)
        token.with_identity(identity)
        token.with_name(name)
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))

        participant_token = token.to_jwt()

        # Create clickable URL using the WORKING format
        encoded_url = urllib.parse.quote(livekit_url, safe='')
        encoded_token = urllib.parse.quote(participant_token, safe='')
        direct_url = f"https://meet.livekit.io/custom?liveKitUrl={encoded_url}&token={encoded_token}"

        print(f"{'─'*80}")
        print(f"👤 PARTICIPANT {i+1}: {name}")
        print(f"{'─'*80}")
        print(f"\n📧 Send this link to {name}:\n")
        print(f"{direct_url}\n")
        print(f"✅ Just click the link to join!")
        print(f"✅ Works on any laptop/device\n")

    print(f"{'='*80}")
    print(f"📋 WHAT TO DO NOW")
    print(f"{'='*80}")
    print(f"1. Check Terminal 1 - you should see 'received job request'")
    print(f"2. Copy each participant's link and send it to them")
    print(f"3. Participants click their links to join")
    print(f"4. Agent will greet them and start the survey\n")

    print(f"{'='*80}")
    print(f"⚙️  TROUBLESHOOTING")
    print(f"{'='*80}")
    print(f"If agent doesn't join:")
    print(f"1. Make sure Terminal 1 is running: python3 agent.py dev")
    print(f"2. Look for 'received job request' in Terminal 1")
    print(f"3. Try running this script again (it will clean up automatically)\n")

    print(f"Press Ctrl+C to exit and clean up the room...\n")

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
    parser = argparse.ArgumentParser(
        description="Start fresh multi-participant survey with auto-cleanup"
    )
    parser.add_argument(
        "--participants",
        type=int,
        default=2,
        help="Number of participants (default: 2)"
    )
    args = parser.parse_args()

    if args.participants < 1 or args.participants > 8:
        print("⚠️  Number of participants must be between 1 and 8")
        sys.exit(1)

    print("\n" + "="*80)
    print("🚀 AI SURVEY MODERATOR - Fresh Start with Auto-Cleanup")
    print("="*80)

    asyncio.run(start_fresh_survey(args.participants))
