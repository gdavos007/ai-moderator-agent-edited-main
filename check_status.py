#!/usr/bin/env python3
"""
Quick diagnostic script to check the status of the Zoom survey system.
"""

import requests
import os
from dotenv import load_dotenv

load_dotenv('.env.local')

def check_recall_bot(bot_id: str):
    """Check Recall.ai bot status."""
    api_key = os.getenv('RECALL_API_KEY')
    region = os.getenv('RECALL_REGION', 'us-west-2')

    base_url = f"https://{region}.recall.ai/api/v1"
    headers = {
        'Authorization': f'Token {api_key}',
        'Content-Type': 'application/json'
    }

    print(f"\n{'='*60}")
    print(f"Checking Recall.ai Bot: {bot_id}")
    print(f"{'='*60}")

    try:
        response = requests.get(
            f'{base_url}/bot/{bot_id}',
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        bot_data = response.json()

        print(f"✅ Bot found!")
        status = bot_data.get('status', {})
        print(f"   Status: {status.get('code', 'unknown')}")
        if status.get('message'):
            print(f"   Status Message: {status.get('message')}")
        if status.get('sub_code'):
            print(f"   Sub Code: {status.get('sub_code')}")
        print(f"   Meeting URL: {bot_data.get('meeting_url', 'N/A')}")
        print(f"   Join At: {bot_data.get('join_at', 'Not joined')}")

        # Show recording info
        recording_data = bot_data.get('recording', {})
        if recording_data:
            print(f"\n   Recording Status:")
            print(f"      ID: {recording_data.get('id', 'N/A')}")
            print(f"      Started At: {recording_data.get('start_time', 'Not started')}")

        # Show participants
        participants = bot_data.get('participants', [])
        print(f"\n   Participants ({len(participants)}):")
        for p in participants:
            print(f"      - {p.get('name', 'Unknown')} (ID: {p.get('id', 'N/A')})")

        # Show websocket config
        recording_config = bot_data.get('recording_config', {})
        realtime_endpoints = recording_config.get('realtime_endpoints', [])

        if realtime_endpoints:
            print(f"\n   Websocket Endpoints:")
            for endpoint in realtime_endpoints:
                print(f"      - Type: {endpoint.get('type')}")
                print(f"        URL: {endpoint.get('url')}")
                print(f"        Events: {endpoint.get('events')}")
        else:
            print(f"\n   ⚠️  No realtime endpoints configured!")

        return bot_data

    except requests.exceptions.RequestException as e:
        print(f"❌ Error: {e}")
        return None


def check_ngrok(webhook_url: str):
    """Check ngrok tunnel status."""
    print(f"\n{'='*60}")
    print(f"Checking ngrok tunnel: {webhook_url}")
    print(f"{'='*60}")

    try:
        # Check health endpoint
        health_url = f"{webhook_url}/health"
        response = requests.get(health_url, timeout=5)

        if response.status_code == 200:
            print(f"✅ Webhook server is reachable!")
            print(f"   Health check: {health_url}")
        else:
            print(f"⚠️  Unexpected status code: {response.status_code}")

    except requests.exceptions.RequestException as e:
        print(f"❌ Cannot reach webhook: {e}")
        print(f"   Make sure ngrok is running and the URL is correct")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python check_status.py <bot_id> [webhook_url]")
        print("\nExample:")
        print("  python check_status.py 361c0ccc-b5ad-4d98-93b6-6ce05ca4ed0d https://grainier-euna-occupiedly.ngrok-free.dev")
        sys.exit(1)

    bot_id = sys.argv[1]
    webhook_url = sys.argv[2] if len(sys.argv) > 2 else None

    # Check bot status
    check_recall_bot(bot_id)

    # Check ngrok if provided
    if webhook_url:
        check_ngrok(webhook_url)

    print(f"\n{'='*60}\n")
