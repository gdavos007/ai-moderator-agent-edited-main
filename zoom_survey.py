#!/usr/bin/env python3
"""
Zoom Survey Entry Point

This script launches the AI moderator agent to conduct surveys in Zoom meetings
using Recall.ai as the bridge between Zoom and LiveKit.

Usage:
    python zoom_survey.py --zoom-url "https://zoom.us/j/123456789" --webhook-url "https://your-domain.ngrok.io/webhook"

Requirements:
    - Recall.ai API key configured in .env.local
    - Publicly accessible webhook URL (use ngrok for testing)
    - LiveKit agent running (python agent.py)
    - LiveKit room will be created automatically
"""

import asyncio
import argparse
import os
import sys
from dotenv import load_dotenv
import logging
import uvicorn
from datetime import datetime
from typing import Optional
from livekit import api

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from zoom_bridge.recall_bot import RecallBot
from zoom_bridge.webhook_handler import WebhookHandler
from zoom_bridge.audio_forwarder import AudioForwarder

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ZoomSurveyOrchestrator:
    """
    Orchestrates the Zoom survey by coordinating:
    1. LiveKit room creation
    2. Recall.ai bot launching
    3. Webhook server for receiving events
    4. Audio forwarding between Zoom and LiveKit
    """

    def __init__(
        self,
        zoom_meeting_url: str,
        webhook_url: str,
        room_name: Optional[str] = None
    ):
        """
        Initialize orchestrator.

        Args:
            zoom_meeting_url: Zoom meeting URL to join
            webhook_url: Publicly accessible webhook URL for Recall.ai
            room_name: LiveKit room name (auto-generated if not provided)
        """
        # Load environment variables
        load_dotenv('.env.local')

        # Zoom/Recall config
        self.zoom_meeting_url = zoom_meeting_url
        self.webhook_url = webhook_url
        self.recall_api_key = os.getenv('RECALL_API_KEY')
        self.recall_region = os.getenv('RECALL_REGION', 'us-west-2')  # Default to us-west-2

        # LiveKit config
        self.livekit_url = os.getenv('LIVEKIT_URL')
        self.livekit_api_key = os.getenv('LIVEKIT_API_KEY')
        self.livekit_api_secret = os.getenv('LIVEKIT_API_SECRET')

        # Generate room name
        self.room_name = room_name or f"zoom-survey-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # Components
        self.recall_bot: Optional[RecallBot] = None
        self.audio_forwarder: Optional[AudioForwarder] = None
        self.webhook_handler: Optional[WebhookHandler] = None
        self.livekit_api: Optional[api.LiveKitAPI] = None

        # Validation
        self._validate_config()

    def _validate_config(self):
        """Validate required configuration."""
        required = {
            'RECALL_API_KEY': self.recall_api_key,
            'LIVEKIT_URL': self.livekit_url,
            'LIVEKIT_API_KEY': self.livekit_api_key,
            'LIVEKIT_API_SECRET': self.livekit_api_secret,
        }

        missing = [key for key, value in required.items() if not value]

        if missing:
            logger.error("❌ Missing required environment variables:")
            for key in missing:
                logger.error(f"   - {key}")
            logger.error("\nPlease configure these in .env.local file")
            sys.exit(1)

        if not self.webhook_url.startswith('http'):
            logger.error("❌ Invalid webhook URL. Must start with http:// or https://")
            sys.exit(1)

    async def setup(self):
        """Setup all components."""
        logger.info("="*80)
        logger.info("🚀 ZOOM SURVEY SETUP")
        logger.info("="*80)

        # 1. Create LiveKit room
        logger.info("\n[1/6] Creating LiveKit room...")
        await self._create_livekit_room()

        # 2. Initialize audio forwarder
        logger.info("\n[2/6] Initializing audio forwarder...")
        self.audio_forwarder = AudioForwarder(
            livekit_url=self.livekit_url,
            livekit_api_key=self.livekit_api_key,
            livekit_api_secret=self.livekit_api_secret,
            room_name=self.room_name
        )

        # 3. Initialize webhook handler
        logger.info("\n[3/6] Setting up webhook handler...")
        self.webhook_handler = WebhookHandler(audio_forwarder=self.audio_forwarder)

        # 4. Start webhook server BEFORE launching bot
        # This is critical - Recall.ai will try to connect to websocket immediately
        logger.info("\n[4/6] Starting webhook server...")
        logger.info("   Server will run on http://0.0.0.0:8000")
        logger.info("   Publicly accessible at: {}/webhook".format(self.webhook_url))

        # Start server in background
        import uvicorn
        config = uvicorn.Config(
            self.webhook_handler.get_app(),
            host="0.0.0.0",
            port=8000,
            log_level="info"
        )
        self.server = uvicorn.Server(config)
        self.server_task = asyncio.create_task(self.server.serve())

        # Give server a moment to start
        await asyncio.sleep(2)
        logger.info("✅ Webhook server is ready")

        # 5. Launch Recall.ai bot (will now be able to connect to websocket)
        logger.info("\n[5/6] Launching Recall.ai bot to join Zoom...")
        self.recall_bot = RecallBot(
            api_key=self.recall_api_key,
            webhook_url=f"{self.webhook_url}/webhook",
            zoom_meeting_url=self.zoom_meeting_url,
            bot_name="AI Survey Moderator",
            region=self.recall_region
        )

        await self.recall_bot.launch_bot()
        await self.recall_bot.wait_for_bot_ready(timeout=120)

        # 6. Start monitoring agent audio to send back to Zoom
        logger.info("\n[6/6] Starting bidirectional audio monitoring...")
        asyncio.create_task(
            self.audio_forwarder.listen_for_agent_audio(
                recall_bot=self.recall_bot,
                agent_identity="moderator-bot"  # This should match the agent's identity in LiveKit
            )
        )
        logger.info("✅ Agent audio monitoring started - bot can now speak in Zoom")

        logger.info("\n" + "="*80)
        logger.info("✅ SETUP COMPLETE!")
        logger.info("="*80)
        logger.info(f"📍 LiveKit Room: {self.room_name}")
        logger.info(f"📍 Zoom Meeting: {self.zoom_meeting_url}")
        logger.info(f"📍 Webhook: {self.webhook_url}/webhook")
        logger.info(f"📍 Bot ID: {self.recall_bot.bot_id}")
        logger.info("📍 Bidirectional Audio: ENABLED ✅")
        logger.info("="*80)
        logger.info("\n⏳ Waiting for participants to join Zoom meeting...")
        logger.info("   When they join, they'll automatically appear in LiveKit")
        logger.info("   The AI agent will detect them and start the survey")
        logger.info("   🎙️  Participants will HEAR the agent speaking!\n")

    async def _create_livekit_room(self):
        """Create LiveKit room for the survey."""
        self.livekit_api = api.LiveKitAPI(
            self.livekit_url,
            self.livekit_api_key,
            self.livekit_api_secret
        )

        try:
            room = await self.livekit_api.room.create_room(
                api.CreateRoomRequest(
                    name=self.room_name,
                    empty_timeout=600,  # 10 minutes
                    max_participants=50,
                )
            )
            logger.info(f"✅ Created LiveKit room: {room.name}")
            logger.info(f"   Room SID: {room.sid}")
        except Exception as e:
            # Room might already exist
            logger.info(f"⚠️  Room might already exist: {e}")
            logger.info(f"   Continuing with room: {self.room_name}")

    async def keep_alive(self):
        """Keep the program running and wait for the server task."""
        try:
            # Wait for the server task (it runs forever until interrupted)
            await self.server_task
        except asyncio.CancelledError:
            logger.info("Server task cancelled")

    async def cleanup(self):
        """Cleanup all resources."""
        logger.info("\n" + "="*80)
        logger.info("🧹 CLEANING UP...")
        logger.info("="*80)

        # Stop webhook server
        if hasattr(self, 'server') and self.server:
            logger.info("Stopping webhook server...")
            self.server.should_exit = True
            if hasattr(self, 'server_task'):
                self.server_task.cancel()
                try:
                    await self.server_task
                except asyncio.CancelledError:
                    pass

        # Stop Recall bot
        if self.recall_bot:
            logger.info("Stopping Recall.ai bot...")
            await self.recall_bot.stop_bot()

        # Cleanup audio forwarder
        if self.audio_forwarder:
            logger.info("Disconnecting audio forwarder...")
            await self.audio_forwarder.cleanup()

        # Delete LiveKit room
        if self.livekit_api:
            try:
                logger.info(f"Deleting LiveKit room: {self.room_name}...")
                await self.livekit_api.room.delete_room(
                    api.DeleteRoomRequest(room=self.room_name)
                )
                logger.info("✅ Room deleted")
            except Exception as e:
                logger.warning(f"Could not delete room: {e}")

        logger.info("✅ Cleanup complete")


async def main():
    """Main entry point."""
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Launch AI moderator for Zoom surveys via Recall.ai"
    )
    parser.add_argument(
        '--zoom-url',
        required=True,
        help='Zoom meeting URL (e.g., https://zoom.us/j/123456789)'
    )
    parser.add_argument(
        '--webhook-url',
        required=True,
        help='Publicly accessible webhook URL (e.g., https://your-domain.ngrok.io)'
    )
    parser.add_argument(
        '--room-name',
        help='Custom LiveKit room name (auto-generated if not provided)'
    )

    args = parser.parse_args()

    # Create orchestrator
    orchestrator = ZoomSurveyOrchestrator(
        zoom_meeting_url=args.zoom_url,
        webhook_url=args.webhook_url,
        room_name=args.room_name
    )

    try:
        # Setup (this now starts the webhook server)
        await orchestrator.setup()

        # Keep running (blocks until Ctrl+C)
        await orchestrator.keep_alive()

    except KeyboardInterrupt:
        logger.info("\n\n👋 Received shutdown signal...")
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
    finally:
        await orchestrator.cleanup()


if __name__ == "__main__":
    print("\n" + "="*80)
    print("AI SURVEY MODERATOR - Zoom Integration (Phase 1: Recall.ai)")
    print("="*80 + "\n")

    asyncio.run(main())
