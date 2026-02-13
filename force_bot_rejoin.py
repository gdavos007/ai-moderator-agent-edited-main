#!/usr/bin/env python3
"""
Force bot to rejoin by deleting and recreating it.

This script:
1. Deletes the existing bot
2. Waits a moment
3. Creates a new bot with explicit recording enabled
"""

import requests
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv('.env.local')

def delete_bot(bot_id: str):
    """Delete a Recall.ai bot."""
    api_key = os.getenv('RECALL_API_KEY')
    region = os.getenv('RECALL_REGION', 'us-west-2')

    base_url = f"https://{region}.recall.ai/api/v1"
    headers = {
        'Authorization': f'Token {api_key}',
        'Content-Type': 'application/json'
    }

    print(f"\n🗑️  Deleting bot {bot_id}...")

    try:
        response = requests.delete(
            f'{base_url}/bot/{bot_id}',
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        print(f"✅ Bot deleted successfully")
        return True
    except Exception as e:
        print(f"❌ Error deleting bot: {e}")
        return False


def create_bot_with_recording(zoom_url: str, webhook_url: str):
    """Create a new bot with explicit recording enabled."""
    api_key = os.getenv('RECALL_API_KEY')
    region = os.getenv('RECALL_REGION', 'us-west-2')

    base_url = f"https://{region}.recall.ai/api/v1"
    headers = {
        'Authorization': f'Token {api_key}',
        'Content-Type': 'application/json'
    }

    # Convert to websocket URL
    ws_url = webhook_url.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws/audio'

    payload = {
        'meeting_url': zoom_url,
        'bot_name': 'AI Survey Moderator',
        'recording_mode': 'speaker_view',  # Force recording mode

        'recording_config': {
            'audio_separate_raw': {
                'enabled': True
            },
            'realtime_endpoints': [
                {
                    'type': 'websocket',
                    'url': ws_url,
                    'events': [
                        'audio_separate_raw.data',
                        'participant_events.join',
                        'participant_events.leave'
                    ]
                }
            ]
        },
        'automatic_leave': {
            'waiting_room_timeout': 600,
            'noone_joined_timeout': 300
        }
    }

    print(f"\n🚀 Creating new bot...")
    print(f"   Zoom URL: {zoom_url}")
    print(f"   WebSocket: {ws_url}")
    print(f"   Recording Mode: speaker_view")

    try:
        response = requests.post(
            f'{base_url}/bot',
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        bot_data = response.json()

        bot_id = bot_data['id']
        print(f"\n✅ New bot created!")
        print(f"   Bot ID: {bot_id}")
        print(f"   Status: {bot_data.get('status', {}).get('code', 'unknown')}")

        return bot_id

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to create bot: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python force_bot_rejoin.py <bot_id> <zoom_url> <webhook_url>")
        print("\nExample:")
        print("  python force_bot_rejoin.py 4f41cf60-7d65-4617-83a2-cc4ac493d174 \\")
        print("    'https://zoom.us/j/2339463704?pwd=iS8czk' \\")
        print("    'https://grainier-euna-occupiedly.ngrok-free.dev'")
        sys.exit(1)

    old_bot_id = sys.argv[1]
    zoom_url = sys.argv[2]
    webhook_url = sys.argv[3]

    print("="*60)
    print("FORCE BOT REJOIN")
    print("="*60)

    # Delete old bot
    if delete_bot(old_bot_id):
        print("\n⏳ Waiting 5 seconds for cleanup...")
        time.sleep(5)

        # Create new bot
        new_bot_id = create_bot_with_recording(zoom_url, webhook_url)

        if new_bot_id:
            print("\n" + "="*60)
            print("✅ SUCCESS!")
            print("="*60)
            print(f"\nNew bot ID: {new_bot_id}")
            print("\nNext steps:")
            print(f"  1. Check if bot appears in Zoom meeting")
            print(f"  2. Run: python check_status.py {new_bot_id} {webhook_url}")
            print(f"  3. Speak in Zoom and watch for audio events")
    else:
        print("\n❌ Failed to delete old bot - aborting")
