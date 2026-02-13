#!/usr/bin/env python3
"""
Start Fresh Multi-Participant Survey with Auto-Cleanup and Dynamic Names

This script AUTOMATICALLY handles everything:
0. Kills any old agent.py dev processes (prevents dispatch conflicts!)
1. Cleans up ALL old rooms automatically
2. Creates a fresh new room
3. Dispatches agent to the new room
4. Generates clickable join links where participants enter their OWN names

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

Key Features:
- Participants enter their REAL names when joining (no more "Alice Johnson", "Bob Smith")
- Agent greets them by their actual names
- CSV export contains their real names
- Clean slate every time - no more dispatch conflicts!
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
import secrets

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
        # Find all agent.py dev processes AND forkserver child processes
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True
        )

        agent_processes = []
        forkserver_processes = []
        for line in result.stdout.split('\n'):
            # Match agent.py dev processes
            if ('agent.py' in line and
                'dev' in line and
                'grep' not in line and
                'start_fresh_survey.py' not in line):
                parts = line.split()
                if len(parts) >= 2:
                    pid = parts[1]
                    if pid.isdigit():
                        agent_processes.append(pid)
            # Also match forkserver child processes from LiveKit agents
            elif ('multiprocessing.forkserver' in line and
                  'livekit' in line and
                  'grep' not in line):
                parts = line.split()
                if len(parts) >= 2:
                    pid = parts[1]
                    if pid.isdigit():
                        forkserver_processes.append(pid)

        num_agents = len(agent_processes)
        num_forkservers = len(forkserver_processes)
        total_processes = num_agents + num_forkservers

        if num_agents == 0:
            print("❌ No agent process found!")
            print("⏳ Please start the agent first:")
            print("   Terminal 1: python3 agent.py dev")
            print("   Wait for 'registered worker' message")
            print("   Then run this script again\n")
            sys.exit(0)

        elif num_agents == 1 and num_forkservers <= 1:
            print(f"✅ Found 1 agent process (PID: {agent_processes[0]})")
            if num_forkservers == 1:
                print(f"✅ Found 1 forkserver process (PID: {forkserver_processes[0]})")
            print(f"✅ Perfect! Only one agent running - proceeding...\n")
            return  # Keep the single agent running

        else:  # Multiple agents or forkservers found - CONFLICT!
            print(f"⚠️  Found {num_agents} agent process(es) and {num_forkservers} forkserver process(es):")
            for pid in agent_processes:
                print(f"  - Agent PID: {pid}")
            for pid in forkserver_processes:
                print(f"  - Forkserver PID: {pid}")

            print(f"\n⚠️  WARNING: Multiple processes cause dispatch conflicts!")
            print(f"🔧 Killing ALL agent and forkserver processes...\n")

            killed = 0
            failed = []
            all_processes = agent_processes + forkserver_processes
            for pid in all_processes:
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
                for pid in all_processes:
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
                print(f"\n✅ Killed {killed} process(es) (agents + forkservers)")
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
        response = await livekit_api.room.list_rooms(api.ListRoomsRequest())
        rooms = response.rooms if response.rooms else []

        if not rooms:
            print("✅ No old rooms to clean up\n")
            return

        print(f"Found {len(rooms)} room(s) to clean up:")
        for room in rooms:
            print(f"  - {room.name}")

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


async def start_fresh_survey(num_participants: int = 2, observer: bool = False, observer_name: str = "Observer"):
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

    # Build room metadata
    room_metadata = {
        "expected_participants": num_participants,
        "has_observer": observer
    }
    import json

    room = await livekit_api.room.create_room(
        api.CreateRoomRequest(
            name=room_name,
            empty_timeout=3600,  # 1 hour
            max_participants=num_participants + 2 + (1 if observer else 0),
            metadata=json.dumps(room_metadata),  # Pass expected count and observer flag to agent
        )
    )

    print(f"✅ Room created: {room.name}")
    print(f"   Room SID: {room.sid}\n")

    # Step 3: Wait a moment for room to be fully ready
    print(f"⏳ Waiting for room to be ready...")
    await asyncio.sleep(2)

    # Check if any participants already in room (auto-dispatch may have triggered)
    print(f"🔍 Checking for existing agents in room...")
    participants_response = await livekit_api.room.list_participants(api.ListParticipantsRequest(room=room_name))
    participants = participants_response.participants if participants_response.participants else []
    agent_count = sum(1 for p in participants if p.kind == 1)  # kind=1 is AGENT
    print(f"   Found {agent_count} existing agent(s) in room")

    if agent_count > 0:
        print(f"⚠️  WARNING: {agent_count} agent(s) already in room (auto-dispatch enabled?)")
        print(f"   Skipping manual dispatch to avoid duplicates\n")
    else:
        # Step 3: Dispatch agent manually
        print(f"🤖 About to dispatch agent (ONCE ONLY)...")
        print(f"   Calling create_dispatch() now...")
        print(f"   Timestamp: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        try:
            agent_dispatch = await livekit_api.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(room=room_name, agent_name="survey-moderator")
            )
            print(f"✅ Agent dispatch COMPLETED: {agent_dispatch.id}")
            print(f"   Dispatch request returned at: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
            print(f"   Dispatch ID: {agent_dispatch.id}")
            print(f"   This was the ONLY create_dispatch() call made\n")

            # Wait a moment and verify only one agent joined
            await asyncio.sleep(3)
            print(f"🔍 Verifying agent count after dispatch...")
            participants_response = await livekit_api.room.list_participants(api.ListParticipantsRequest(room=room_name))
            participants = participants_response.participants if participants_response.participants else []
            agent_count = sum(1 for p in participants if p.kind == 1)
            print(f"   Current agent count in room: {agent_count}")
            if agent_count > 1:
                print(f"⚠️  WARNING: {agent_count} agents detected! This is the duplicate agent issue!")
            else:
                print(f"✅ Good: Only {agent_count} agent in room\n")
        except Exception as e:
            print(f"⚠️  Agent dispatch failed: {e}")
            print(f"   Make sure 'python3 agent.py dev' is running in Terminal 1\n")
            return

    # Step 4: Prompt for participant names and generate tokens
    print(f"{'='*80}")
    print(f"👥 ENTER PARTICIPANT NAMES")
    print(f"{'='*80}\n")

    participant_data = []

    for i in range(num_participants):
        while True:
            name = input(f"Enter name for Participant {i+1}: ").strip()
            if name and len(name) >= 2:
                participant_data.append(name)
                break
            else:
                print("Please enter a valid name (at least 2 characters)")

    print(f"\n{'='*80}")
    print(f"🔗 GENERATING JOIN LINKS")
    print(f"{'='*80}\n")

    # Generate tokens for each participant
    join_links = []

    for i, name in enumerate(participant_data):
        identity = name.lower().replace(" ", "_")
        identity = ''.join(c for c in identity if c.isalnum() or c == '_')

        # Create token with participant's name
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
        join_url = f"https://meet.livekit.io/custom?liveKitUrl={encoded_url}&token={encoded_token}"

        join_links.append({
            'name': name,
            'url': join_url
        })

        print(f"{'─'*80}")
        print(f"👤 {name}")
        print(f"{'─'*80}")
        print(f"{join_url}\n")

    # Generate Observer token if observer mode is enabled
    observer_url = None
    if observer:
        observer_identity = f"observer_{observer_name.lower().replace(' ', '_')}"
        observer_identity = ''.join(c for c in observer_identity if c.isalnum() or c == '_')

        # Create observer token - can speak (for voice commands), hear, and send data
        observer_token_obj = api.AccessToken(api_key, api_secret)
        observer_token_obj.with_identity(observer_identity)
        observer_token_obj.with_name(f"[Observer] {observer_name}")
        observer_token_obj.with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,       # Observer needs to speak for voice commands
            can_subscribe=True,     # Observer can hear everything
            can_publish_data=True,  # Observer can send control commands
        ))

        observer_jwt = observer_token_obj.to_jwt()
        encoded_observer_token = urllib.parse.quote(observer_jwt, safe='')
        observer_url = f"https://meet.livekit.io/custom?liveKitUrl={encoded_url}&token={encoded_observer_token}"

        print(f"{'─'*80}")
        print(f"👁️ OBSERVER: {observer_name}")
        print(f"{'─'*80}")
        print(f"{observer_url}\n")

    # Create simple HTML page with pre-generated links
    print(f"{'='*80}")
    print(f"📄 CREATING HTML JOIN PAGE")
    print(f"{'='*80}\n")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Survey - Join Now</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}

        .container {{
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 500px;
            width: 100%;
            padding: 40px;
        }}

        h1 {{
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }}

        .subtitle {{
            color: #666;
            margin-bottom: 30px;
            font-size: 16px;
        }}

        .room-info {{
            background: #f0f4ff;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 30px;
            border-left: 4px solid #667eea;
            font-size: 14px;
        }}

        .participant-card {{
            background: #f8f9fa;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 15px;
            border: 2px solid #e9ecef;
            transition: all 0.3s ease;
        }}

        .participant-card:hover {{
            border-color: #667eea;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
        }}

        .participant-name {{
            font-size: 20px;
            font-weight: 600;
            color: #333;
            margin-bottom: 15px;
        }}

        .join-button {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            width: 100%;
            transition: all 0.3s ease;
        }}

        .join-button:hover {{
            transform: scale(1.02);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
        }}

        .instructions {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
            font-size: 14px;
        }}

        .instructions h3 {{
            color: #856404;
            margin-bottom: 10px;
            font-size: 16px;
        }}

        .instructions ol {{
            margin-left: 20px;
            color: #856404;
        }}

        .observer-section {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 2px dashed #4caf50;
        }}

        .observer-card {{
            background: #e8f5e9;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 15px;
            border: 2px solid #4caf50;
        }}

        .observer-badge {{
            background: #4caf50;
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            display: inline-block;
            margin-bottom: 15px;
        }}

        .observer-name {{
            font-size: 20px;
            font-weight: 600;
            color: #2e7d32;
            margin-bottom: 15px;
        }}

        .join-button.observer {{
            background: linear-gradient(135deg, #4caf50 0%, #2e7d32 100%);
        }}

        .join-button.observer:hover {{
            box-shadow: 0 6px 20px rgba(76, 175, 80, 0.4);
        }}

        .observer-instructions {{
            font-size: 13px;
            color: #2e7d32;
            margin-top: 15px;
        }}

        .observer-instructions ul {{
            margin-left: 20px;
            margin-top: 8px;
        }}

        .observer-instructions li {{
            margin-bottom: 5px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🎤 AI Survey Session</h1>
        <p class="subtitle">Click your name below to join the survey</p>

        <div class="room-info">
            <strong>Room:</strong> {room_name}<br>
            <strong>Duration:</strong> ~15-20 minutes
        </div>

        <div id="participants">
"""

    # Add participant cards with their specific tokens
    for link in join_links:
        html_content += f"""
            <div class="participant-card">
                <div class="participant-name">👤 {link['name']}</div>
                <button class="join-button" onclick="window.open('{link['url']}', '_blank')">
                    Join as {link['name']}
                </button>
            </div>
"""

    html_content += """
        </div>
"""

    # Add observer section if observer mode is enabled
    if observer and observer_url:
        html_content += f"""
        <div class="observer-section">
            <div class="observer-card">
                <div class="observer-badge">👁️ OBSERVER CONTROL PANEL</div>
                <div class="observer-name">{observer_name}</div>
                <button class="join-button observer" onclick="window.open('{observer_url}', '_blank')">
                    Join as Observer
                </button>
                <div class="observer-instructions">
                    <strong>Voice Commands:</strong>
                    <ul>
                        <li><strong>"Start survey"</strong> - Begin asking questions</li>
                        <li><strong>"Pause survey"</strong> - Pause the survey</li>
                        <li><strong>"Resume survey"</strong> - Continue from pause point</li>
                    </ul>
                </div>
            </div>
        </div>
"""

    html_content += f"""
        <div class="instructions">
            <h3>📋 Instructions:</h3>
            <ol>
                <li>Click the "Join" button with your name</li>
                <li>Allow microphone access when prompted</li>
                <li>Make sure your microphone is unmuted</li>
                <li>The AI moderator will guide you through the survey</li>
            </ol>
        </div>
    </div>
</body>
</html>
"""

    # Save HTML file
    html_file = "join_survey.html"
    with open(html_file, "w") as f:
        f.write(html_content)

    print(f"✅ HTML JOIN PAGE CREATED: {html_file}\n")
    print(f"📧 SHARE WITH PARTICIPANTS:\n")
    print(f"   Send them '{html_file}' - they click their name to join\n")
    print(f"File location: {os.path.abspath(html_file)}\n")

    print(f"{'='*80}")
    print(f"📋 WHAT TO DO NOW")
    print(f"{'='*80}")
    print(f"1. Check Terminal 1 - you should see 'received job request'")
    print(f"2. Share 'join_survey.html' with ALL participants (same file for everyone!)")
    print(f"3. Each participant opens the file and ENTERS THEIR NAME")
    print(f"4. They click 'Join Survey' to enter the room")
    print(f"5. Agent will greet them by their real names")
    print(f"6. CSV export will contain their actual names!\n")

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
    parser.add_argument(
        "--observer",
        action="store_true",
        help="Generate an observer URL for a human moderator who can control the survey"
    )
    parser.add_argument(
        "--observer-name",
        type=str,
        default="Observer",
        help="Name for the observer (default: Observer)"
    )
    args = parser.parse_args()

    if args.participants < 1 or args.participants > 8:
        print("⚠️  Number of participants must be between 1 and 8")
        sys.exit(1)

    print("\n" + "="*80)
    print("🚀 AI SURVEY MODERATOR - Fresh Start with Auto-Cleanup")
    print("="*80)

    asyncio.run(start_fresh_survey(args.participants, args.observer, args.observer_name))
