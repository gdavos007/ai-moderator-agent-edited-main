"""
Recall.ai Bot Integration

This module handles launching and managing Recall.ai bots that join Zoom meetings
and stream audio to our LiveKit-based AI moderator agent.
"""

import logging
import asyncio
import requests
from typing import Dict, Optional, Callable
from datetime import datetime

logger = logging.getLogger(__name__)


class RecallBot:
    """
    Manages Recall.ai bot lifecycle for Zoom meeting integration.

    The bot joins a Zoom meeting, captures audio from participants,
    and streams it to the webhook handler.
    """

    def __init__(
        self,
        api_key: str,
        webhook_url: str,
        zoom_meeting_url: str,
        bot_name: str = "AI Survey Moderator",
        region: str = "us-west-2"
    ):
        """
        Initialize Recall bot manager.

        Args:
            api_key: Recall.ai API key
            webhook_url: Your webhook endpoint URL (publicly accessible)
            zoom_meeting_url: Zoom meeting URL to join
            bot_name: Display name for the bot in the meeting
            region: Recall.ai region (us-west-2, us-east-1, eu-central-1, ap-northeast-1)
        """
        self.api_key = api_key
        self.webhook_url = webhook_url
        self.zoom_meeting_url = zoom_meeting_url
        self.bot_name = bot_name
        self.bot_id: Optional[str] = None
        self.status_callback: Optional[Callable] = None

        # API configuration with region support
        if region:
            self.base_url = f"https://{region}.recall.ai/api/v1"
        else:
            self.base_url = "https://api.recall.ai/api/v1"

        self.headers = {
            'Authorization': f'Token {self.api_key}',
            'Content-Type': 'application/json'
        }

    async def launch_bot(self) -> str:
        """
        Launch Recall.ai bot to join the Zoom meeting.

        Returns:
            Bot ID from Recall.ai

        Raises:
            Exception: If bot creation fails
        """
        logger.info(f"Launching Recall.ai bot to join Zoom meeting: {self.zoom_meeting_url}")

        # Websocket configuration for real-time audio
        # Reference: https://docs.recall.ai/docs/real-time-websocket-endpoints
        # Convert HTTP webhook URL to WSS websocket URL
        # Remove /webhook suffix if present and add /ws/audio
        base_url = self.webhook_url.replace('/webhook', '')
        ws_url = base_url.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws/audio'

        payload = {
            'meeting_url': self.zoom_meeting_url,
            'bot_name': self.bot_name,

            # Real-time websocket endpoints for audio streaming
            # NOTE: Despite support saying otherwise, API requires BOTH artifact AND endpoint
            'recording_config': {
                # Must configure the artifact first
                'audio_separate_raw': {},

                # Then specify it in realtime endpoints
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
        }

        # Add automatic leave settings
        try:
            payload['automatic_leave'] = {
                'waiting_room_timeout': 600,  # Leave if stuck in waiting room for 10 min
                'noone_joined_timeout': 300   # Leave if alone for 5 min
            }
        except:
            pass  # Ignore if not supported

        try:
            # Create bot via Recall.ai API
            logger.info(f"🚀 Creating Recall bot with websocket endpoints...")
            logger.info(f"   Meeting URL: {self.zoom_meeting_url}")
            logger.info(f"   Bot name: {self.bot_name}")
            logger.info(f"   Websocket URL: {ws_url}")

            response = requests.post(
                f'{self.base_url}/bot',
                headers=self.headers,
                json=payload,
                timeout=30
            )

            response.raise_for_status()
            bot_data = response.json()

            self.bot_id = bot_data['id']
            logger.info(f"✅ Recall bot created successfully! Bot ID: {self.bot_id}")
            logger.info(f"   Bot status: {bot_data.get('status', {}).get('code', 'unknown')}")
            logger.info(f"   Real-time websocket: ENABLED ✅")
            logger.info(f"   Websocket URL: {ws_url}")
            logger.info(f"   Events: audio_separate_raw.data")

            return self.bot_id

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Failed to create Recall bot: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"   Response: {e.response.text}")
                try:
                    error_json = e.response.json()
                    logger.error(f"   Error details: {error_json}")
                except:
                    pass
            raise Exception(f"Recall bot creation failed: {e}")

    async def wait_for_bot_ready(self, timeout: int = 120) -> bool:
        """
        Wait for bot to join the meeting and be ready.

        Args:
            timeout: Maximum seconds to wait

        Returns:
            True if bot joined successfully, False if timeout

        Raises:
            Exception: If bot encounters an error
        """
        if not self.bot_id:
            raise ValueError("Bot not launched yet. Call launch_bot() first.")

        logger.info(f"Waiting for bot {self.bot_id} to join meeting (timeout: {timeout}s)...")

        start_time = datetime.now()
        last_status = None

        while (datetime.now() - start_time).total_seconds() < timeout:
            try:
                status = self.get_bot_status()
                status_code = status.get('status', {}).get('code', 'unknown')

                # Log status changes
                if status_code != last_status:
                    logger.info(f"   Bot status: {status_code}")
                    last_status = status_code

                    if self.status_callback:
                        await self.status_callback(status_code)

                # Check if bot is in the call
                if status_code in ['in_call_not_recording', 'in_call_recording']:
                    logger.info(f"✅ Bot successfully joined Zoom meeting!")
                    logger.info(f"   Participants will see: '{self.bot_name}'")
                    return True

                # Check for error states
                if status_code in ['fatal', 'error']:
                    error_msg = status.get('status', {}).get('message', 'Unknown error')
                    raise Exception(f"Bot encountered error: {error_msg}")

                # Still joining...
                await asyncio.sleep(2)

            except requests.exceptions.RequestException as e:
                logger.warning(f"Error checking bot status: {e}")
                await asyncio.sleep(5)

        # Timeout reached
        logger.error(f"❌ Timeout waiting for bot to join meeting after {timeout}s")
        return False

    def get_bot_status(self) -> Dict:
        """
        Get current bot status from Recall.ai.

        Returns:
            Dictionary with bot status information
        """
        if not self.bot_id:
            raise ValueError("Bot not launched yet")

        response = requests.get(
            f'{self.base_url}/bot/{self.bot_id}',
            headers=self.headers,
            timeout=10
        )
        response.raise_for_status()
        return response.json()

    def get_participant_list(self) -> list:
        """
        Get list of participants currently in the meeting.

        Returns:
            List of participant dictionaries with 'id', 'name', etc.
        """
        if not self.bot_id:
            raise ValueError("Bot not launched yet")

        try:
            status = self.get_bot_status()
            participants = status.get('participants', [])

            logger.info(f"Current Zoom participants ({len(participants)}):")
            for p in participants:
                logger.info(f"  - {p.get('name', 'Unknown')} (ID: {p.get('id', 'N/A')})")

            return participants

        except Exception as e:
            logger.error(f"Error fetching participant list: {e}")
            return []

    async def send_audio_to_zoom(self, audio_data_mp3_base64: str):
        """
        Send audio from AI agent back to Zoom meeting.

        Uses Recall.ai's Output Audio API to play audio through the bot's microphone.
        Requires bot to be configured with automatic_audio_output.

        Args:
            audio_data_mp3_base64: Base64-encoded MP3 audio data

        Returns:
            True if successful, False otherwise
        """
        if not self.bot_id:
            logger.error("Cannot send audio: Bot not launched")
            return False

        try:
            # Send audio to Recall.ai Output Audio endpoint
            response = requests.post(
                f'{self.base_url}/bot/{self.bot_id}/output_audio/',
                headers=self.headers,
                json={
                    'kind': 'mp3',
                    'b64_data': audio_data_mp3_base64
                },
                timeout=10
            )

            response.raise_for_status()
            logger.debug(f"✅ Audio sent to Zoom successfully")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Failed to send audio to Zoom: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"   Response: {e.response.text}")
            return False

    async def delete_recording(self, delete_immediately: bool = False):
        """
        Delete recording for this bot.

        Args:
            delete_immediately: If True, delete now. If False, rely on auto-deletion policy.

        Returns:
            True if deleted successfully, False otherwise
        """
        if not self.bot_id:
            logger.warning("No bot ID to delete recordings for")
            return False

        if not delete_immediately:
            logger.info("Recording will be auto-deleted based on retention policy")
            return True

        logger.info(f"🗑️  Deleting recording for bot {self.bot_id}...")

        try:
            # Get bot status to find recordings
            status = self.get_bot_status()
            recordings = status.get('recordings', [])

            if not recordings:
                logger.info("No recordings found to delete")
                return True

            # Delete each recording
            for recording in recordings:
                recording_id = recording.get('id')
                if recording_id:
                    response = requests.delete(
                        f'{self.base_url}/recording/{recording_id}',
                        headers=self.headers,
                        timeout=10
                    )
                    response.raise_for_status()
                    logger.info(f"✅ Deleted recording: {recording_id}")

            logger.info(f"✅ All recordings deleted for bot {self.bot_id}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Error deleting recordings: {e}")
            return False

    async def stop_bot(self, delete_recording: bool = False):
        """
        Stop the bot and have it leave the Zoom meeting.

        Args:
            delete_recording: If True, also delete the recording immediately
        """
        if not self.bot_id:
            logger.warning("No bot to stop")
            return

        logger.info(f"Stopping Recall bot {self.bot_id}...")

        try:
            # Delete recording first if requested
            if delete_recording:
                await self.delete_recording(delete_immediately=True)

            # Then stop the bot
            response = requests.delete(
                f'{self.base_url}/bot/{self.bot_id}',
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()

            logger.info(f"✅ Bot {self.bot_id} stopped successfully")
            self.bot_id = None

        except requests.exceptions.RequestException as e:
            logger.error(f"Error stopping bot: {e}")

    def set_status_callback(self, callback: Callable):
        """
        Set callback function to be called when bot status changes.

        Args:
            callback: Async function(status_code: str) -> None
        """
        self.status_callback = callback
