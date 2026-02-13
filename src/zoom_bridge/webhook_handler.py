"""
Webhook Handler for Recall.ai Events

This module receives webhooks from Recall.ai when events occur in the Zoom meeting
(participant join/leave, audio data, etc.) and forwards them to the AudioForwarder
which manages the LiveKit connection.
"""

import logging
import asyncio
import base64
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from typing import Dict, Optional, Callable
import json

logger = logging.getLogger(__name__)


class WebhookHandler:
    """
    Handles webhooks from Recall.ai and coordinates with AudioForwarder.
    """

    def __init__(self, audio_forwarder=None):
        """
        Initialize webhook handler.

        Args:
            audio_forwarder: AudioForwarder instance to send events to
        """
        self.audio_forwarder = audio_forwarder
        self.app = FastAPI(title="Recall.ai Webhook Handler")
        self.bot_id: Optional[str] = None
        self.participant_callbacks: Dict[str, Callable] = {}

        # Setup routes
        self._setup_routes()

        # Event tracking
        self.events_received = 0
        self.participants_seen = set()
        self.websocket_events = 0
        self.websocket_connected = False

    def _setup_routes(self):
        """Setup FastAPI routes for webhooks."""

        @self.app.post("/webhook")
        async def receive_webhook(request: Request):
            """Main webhook endpoint for Recall.ai events."""
            try:
                # Parse webhook data
                data = await request.json()
                self.events_received += 1

                # Log event
                event_type = data.get('type', 'unknown')
                logger.debug(f"Received webhook #{self.events_received}: {event_type}")

                # Route to appropriate handler
                await self._handle_event(data)

                return {"status": "ok"}

            except Exception as e:
                logger.error(f"Error processing webhook: {e}", exc_info=True)
                return Response(
                    content=json.dumps({"error": str(e)}),
                    status_code=500,
                    media_type="application/json"
                )

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {
                "status": "healthy",
                "events_received": self.events_received,
                "websocket_events": self.websocket_events,
                "websocket_connected": self.websocket_connected,
                "participants_seen": len(self.participants_seen),
                "audio_forwarder_connected": self.audio_forwarder is not None
            }

        @self.app.get("/")
        async def root():
            """Root endpoint."""
            return {
                "service": "Zoom-LiveKit Bridge",
                "status": "running",
                "webhook_endpoint": "/webhook",
                "websocket_endpoint": "/ws/audio"
            }

        @self.app.websocket("/ws/audio")
        async def websocket_audio(websocket: WebSocket):
            """
            Websocket endpoint for real-time audio from Recall.ai.

            Reference: https://docs.recall.ai/docs/real-time-websocket-endpoints
            """
            await websocket.accept()
            self.websocket_connected = True
            logger.info("🔌 Websocket connected: Real-time audio stream from Recall.ai")
            logger.info("   Waiting for audio data from Zoom participants...")

            try:
                while True:
                    # Receive data from Recall.ai (could be JSON or binary)
                    try:
                        # Try to receive as JSON first
                        data = await websocket.receive_json()
                        self.websocket_events += 1
                        event_type = data.get('event', 'unknown')

                        # Log every event for debugging
                        logger.info(f"📨 WebSocket event #{self.websocket_events}: {event_type}")

                        # Handle the event
                        await self._handle_websocket_event(data)

                    except Exception as json_error:
                        # If JSON fails, try receiving as text/bytes
                        logger.info(f"📨 Trying non-JSON format: {json_error}")
                        message = await websocket.receive()
                        logger.info(f"📨 Received raw message: type={message.get('type')}, keys={list(message.keys())}")

                        # Log the first 200 chars to see what format it is
                        if 'text' in message:
                            logger.info(f"   Text data (first 200 chars): {message['text'][:200]}")
                            # Try parsing as JSON manually
                            try:
                                import json
                                data = json.loads(message['text'])
                                self.websocket_events += 1
                                logger.info(f"📨 Parsed text as JSON - event: {data.get('event', 'unknown')}")
                                await self._handle_websocket_event(data)
                            except:
                                pass
                        elif 'bytes' in message:
                            logger.info(f"   Binary data length: {len(message['bytes'])} bytes")

            except WebSocketDisconnect:
                self.websocket_connected = False
                logger.info("🔌 Websocket disconnected")
            except Exception as e:
                self.websocket_connected = False
                logger.error(f"❌ Websocket error: {e}", exc_info=True)

    async def _handle_event(self, data: Dict):
        """
        Route webhook event to appropriate handler.

        Args:
            data: Webhook payload from Recall.ai
        """
        event_type = data.get('type')
        event_data = data.get('data', {})

        # Map event types to handlers
        # Reference: https://docs.recall.ai/docs/real-time-webhook-endpoints
        handlers = {
            # Legacy webhook events
            'bot.status_change': self._handle_bot_status_change,
            'bot.participant_join': self._handle_participant_join,
            'bot.participant_leave': self._handle_participant_leave,
            'bot.transcription': self._handle_transcription,
            'bot.audio_data': self._handle_audio_data,
            'bot.error': self._handle_bot_error,

            # Real-time webhook endpoint events (Nov 2024+)
            'audio_separate_raw.data': self._handle_audio_separate_raw,
            'transcript.data': self._handle_transcript_data,
            'participant_events.join': self._handle_participant_join,
            'participant_events.leave': self._handle_participant_leave,
        }

        handler = handlers.get(event_type)
        if handler:
            await handler(event_data)
        else:
            logger.debug(f"Unhandled event type: {event_type}")

    async def _handle_bot_status_change(self, data: Dict):
        """
        Handle bot status changes.

        Args:
            data: Status change event data
        """
        status = data.get('status', {})
        status_code = status.get('code', 'unknown')
        message = status.get('message', '')

        logger.info(f"Bot status changed: {status_code}")
        if message:
            logger.info(f"  Message: {message}")

        # Notify audio forwarder if needed
        if self.audio_forwarder:
            if status_code == 'in_call_not_recording' or status_code == 'in_call_recording':
                logger.info("Bot is now in the Zoom call - ready to receive audio")

    async def _handle_participant_join(self, data: Dict):
        """
        Handle participant joining Zoom meeting.

        This is the KEY function that extracts Zoom participant names
        and passes them to LiveKit.

        Args:
            data: Participant join event data
        """
        # Extract participant from nested structure
        # WebSocket sends: {event: "participant_events.join", data: {data: {participant: {...}}}}
        # We receive the outer 'data', so we need to go one level deeper
        event_data = data.get('data', {})
        participant = event_data.get('participant', {})

        participant_id = participant.get('id', 'unknown')
        participant_name = participant.get('name', 'Unknown Participant')
        participant_email = participant.get('email', '')

        logger.info("="*80)
        logger.info(f"🎯 NEW ZOOM PARTICIPANT JOINED:")
        logger.info(f"   Name: {participant_name}")
        logger.info(f"   ID: {participant_id}")
        if participant_email:
            logger.info(f"   Email: {participant_email}")
        logger.info("="*80)

        # Track participant
        self.participants_seen.add(participant_id)

        # Forward to audio forwarder to create LiveKit connection
        if self.audio_forwarder:
            try:
                await self.audio_forwarder.add_zoom_participant(
                    zoom_id=participant_id,
                    zoom_name=participant_name,
                    email=participant_email
                )
                logger.info(f"✅ Created LiveKit connection for {participant_name}")
            except Exception as e:
                logger.error(f"❌ Failed to create LiveKit connection for {participant_name}: {e}")
        else:
            logger.warning("⚠️  No audio forwarder configured - participant won't be added to LiveKit")

        # Call custom callbacks if registered
        if 'participant_join' in self.participant_callbacks:
            await self.participant_callbacks['participant_join'](participant)

    async def _handle_participant_leave(self, data: Dict):
        """
        Handle participant leaving Zoom meeting.

        Args:
            data: Participant leave event data
        """
        participant = data.get('participant', {})
        participant_id = participant.get('id', 'unknown')
        participant_name = participant.get('name', 'Unknown Participant')

        logger.info(f"👋 Zoom participant left: {participant_name} (ID: {participant_id})")

        # Notify audio forwarder to disconnect LiveKit connection
        if self.audio_forwarder:
            try:
                await self.audio_forwarder.remove_zoom_participant(participant_id)
                logger.info(f"✅ Removed LiveKit connection for {participant_name}")
            except Exception as e:
                logger.error(f"Error removing participant {participant_name}: {e}")

        # Call custom callbacks if registered
        if 'participant_leave' in self.participant_callbacks:
            await self.participant_callbacks['participant_leave'](participant)

    async def _handle_transcription(self, data: Dict):
        """
        Handle real-time transcription from Recall.ai.

        Note: We're using Recall's transcription as a backup.
        Primary transcription comes from OpenAI STT in the LiveKit agent.

        Args:
            data: Transcription event data
        """
        speaker = data.get('speaker', 'Unknown')
        transcript = data.get('transcript', '')
        is_final = data.get('is_final', False)

        if is_final:
            logger.debug(f"[Recall Transcript] {speaker}: {transcript}")
            # We can log this for comparison/debugging, but agent uses its own STT

    async def _handle_audio_data(self, data: Dict):
        """
        Handle raw audio data from Zoom participants (legacy API).

        This is sent to AudioForwarder to be published to LiveKit.

        Args:
            data: Audio data event
        """
        speaker_id = data.get('speaker_id', 'unknown')
        audio_chunk = data.get('audio')  # Base64 encoded audio

        if self.audio_forwarder:
            await self.audio_forwarder.forward_audio(speaker_id, audio_chunk)

    async def _handle_audio_separate_raw(self, data: Dict):
        """
        Handle real-time separate audio per participant (Nov 2024+ API).

        Event: audio_separate_raw.data
        Reference: https://docs.recall.ai/docs/real-time-webhook-endpoints

        Args:
            data: Audio data with format:
                {
                    "participant_id": "abc123",
                    "data": "base64_encoded_pcm_s16le",
                    "sample_rate": 16000,
                    "channels": 1
                }
        """
        # Extract nested data structure from Recall.ai WebSocket
        event_data = data.get('data', {})
        participant_info = event_data.get('participant', {})

        # Get participant details
        participant_id = str(participant_info.get('id', 'unknown'))
        participant_name = participant_info.get('name', 'Unknown')

        # Get audio buffer (base64 encoded PCM s16le audio)
        audio_data = event_data.get('buffer')

        # Log first audio chunk to confirm we're receiving data
        if self.websocket_events <= 5:  # Only log first few
            logger.info(f"🎵 Received audio from {participant_name} (ID: {participant_id})")
            if audio_data:
                logger.info(f"   Audio data length: {len(audio_data)} bytes (base64)")
                logger.info(f"   Is host: {participant_info.get('is_host', False)}")

        if self.audio_forwarder and audio_data:
            # Forward with participant ID - audio forwarder will create LiveKit track
            await self.audio_forwarder.forward_audio(participant_id, audio_data)
        elif not self.audio_forwarder:
            logger.warning(f"⚠️  No audio forwarder - cannot forward audio from {participant_name}")

    async def _handle_transcript_data(self, data: Dict):
        """
        Handle real-time transcript data (Nov 2024+ API).

        Event: transcript.data
        Reference: https://docs.recall.ai/docs/real-time-webhook-endpoints

        Args:
            data: Transcript data
        """
        speaker = data.get('speaker', 'Unknown')
        transcript = data.get('transcript', '')

        logger.debug(f"[Transcript] {speaker}: {transcript}")

    async def _handle_bot_error(self, data: Dict):
        """
        Handle bot errors.

        Args:
            data: Error event data
        """
        error_code = data.get('error', {}).get('code', 'unknown')
        error_message = data.get('error', {}).get('message', 'Unknown error')

        logger.error(f"❌ Recall bot error: {error_code}")
        logger.error(f"   Message: {error_message}")

    async def _handle_websocket_event(self, data: Dict):
        """
        Handle websocket events from Recall.ai.

        Websocket events have format:
        {
            "event": "audio_separate_raw.data",
            "data": {
                "participant_id": "abc123",
                "data": "base64_encoded_pcm",
                "sample_rate": 16000,
                "channels": 1
            }
        }

        Reference: https://docs.recall.ai/docs/real-time-websocket-endpoints
        """
        event_type = data.get('event', 'unknown')
        event_data = data.get('data', {})

        # Map websocket events to handlers
        if event_type == 'audio_separate_raw.data':
            logger.debug(f"   → Processing separate audio from participant {event_data.get('participant_id', 'unknown')}")
            await self._handle_audio_separate_raw(event_data)
        elif event_type == 'audio_mixed_raw.data':
            # Handle mixed audio if needed
            logger.info(f"   → Received mixed audio (not used - we use separate)")
        elif event_type == 'transcript.data':
            logger.info(f"   → Transcript: {event_data.get('speaker', 'Unknown')}")
            await self._handle_transcript_data(event_data)
        elif event_type.startswith('participant_events.'):
            # Handle participant events
            logger.info(f"   → Participant event: {event_type}")
            if 'join' in event_type:
                await self._handle_participant_join(event_data)
            elif 'leave' in event_type:
                await self._handle_participant_leave(event_data)
        else:
            logger.info(f"   ⚠️  Unhandled websocket event: {event_type}")
            logger.info(f"      Data keys: {list(event_data.keys())}")

    def register_callback(self, event_type: str, callback: Callable):
        """
        Register custom callback for specific event types.

        Args:
            event_type: Event type to listen for (e.g., 'participant_join')
            callback: Async function to call when event occurs
        """
        self.participant_callbacks[event_type] = callback
        logger.info(f"Registered callback for event: {event_type}")

    def get_app(self) -> FastAPI:
        """
        Get the FastAPI application instance.

        Returns:
            FastAPI app instance
        """
        return self.app

    def get_stats(self) -> Dict:
        """
        Get statistics about webhook activity.

        Returns:
            Dictionary with statistics
        """
        return {
            'events_received': self.events_received,
            'participants_seen': len(self.participants_seen),
            'participant_ids': list(self.participants_seen),
            'audio_forwarder_active': self.audio_forwarder is not None
        }
