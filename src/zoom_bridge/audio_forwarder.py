"""
Audio Forwarder

This module manages the bidirectional audio flow between Zoom (via Recall.ai)
and LiveKit rooms, creating virtual participants in LiveKit for each Zoom participant.
"""

import logging
import asyncio
import base64
import time
from typing import Dict, Optional
from livekit import rtc, api
import numpy as np
from .audio_converter import AudioBuffer, convert_pcm_to_mp3_base64

logger = logging.getLogger(__name__)


class AudioForwarder:
    """
    Manages audio forwarding between Zoom participants (via Recall.ai) and LiveKit room.

    For each Zoom participant, this creates:
    1. A LiveKit room connection with the participant's Zoom name as identity
    2. An audio track to publish participant's audio to LiveKit
    3. A listener for agent's audio to send back (future enhancement)
    """

    def __init__(
        self,
        livekit_url: str,
        livekit_api_key: str,
        livekit_api_secret: str,
        room_name: str
    ):
        """
        Initialize audio forwarder.

        Args:
            livekit_url: LiveKit server URL
            livekit_api_key: LiveKit API key
            livekit_api_secret: LiveKit API secret
            room_name: LiveKit room name to connect participants to
        """
        self.livekit_url = livekit_url
        self.livekit_api_key = livekit_api_key
        self.livekit_api_secret = livekit_api_secret
        self.room_name = room_name

        # Track connections: zoom_id -> participant info
        self.zoom_participants: Dict[str, Dict] = {}

        # Audio configuration (Recall.ai uses 16kHz mono by default)
        self.sample_rate = 16000
        self.num_channels = 1

        logger.info(f"AudioForwarder initialized for room: {room_name}")

    async def add_zoom_participant(
        self,
        zoom_id: str,
        zoom_name: str,
        email: Optional[str] = None
    ):
        """
        Add a Zoom participant and create their LiveKit connection.

        This is called when Recall.ai webhook reports a participant joined.

        Args:
            zoom_id: Recall.ai's participant ID
            zoom_name: Participant's display name from Zoom
            email: Participant's email (optional)
        """
        # Check if already added
        if zoom_id in self.zoom_participants:
            logger.warning(f"Participant {zoom_name} already exists, skipping")
            return

        logger.info(f"➕ Adding Zoom participant to LiveKit: {zoom_name}")

        try:
            # Create LiveKit access token for this participant
            token = api.AccessToken(self.livekit_api_key, self.livekit_api_secret)

            # ✅ KEY: Use Zoom display name as LiveKit identity
            # This is what the agent will see as participant.identity
            token.with_identity(zoom_name)
            token.with_name(zoom_name)

            # Set metadata if available
            if email:
                token.with_metadata(f'{{"email": "{email}", "source": "zoom"}}')

            token.with_grants(api.VideoGrants(
                room_join=True,
                room=self.room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True
            ))

            participant_token = token.to_jwt()

            # Create LiveKit room connection for this participant
            room = rtc.Room()

            # Setup event handlers for this participant's connection
            @room.on("participant_connected")
            def on_participant_connected(participant: rtc.RemoteParticipant):
                logger.info(f"   LiveKit participant connected: {participant.identity}")

            @room.on("disconnected")
            def on_disconnected():
                logger.warning(f"   {zoom_name}'s LiveKit connection disconnected")

            # Connect to LiveKit room
            logger.info(f"   Connecting {zoom_name} to LiveKit room '{self.room_name}'...")
            await room.connect(self.livekit_url, participant_token)

            # Create audio source and track for this participant
            audio_source = rtc.AudioSource(self.sample_rate, self.num_channels)
            audio_track = rtc.LocalAudioTrack.create_audio_track(
                "microphone",
                audio_source
            )

            # Publish the audio track
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            publication = await room.local_participant.publish_track(audio_track, options)

            # Store participant info
            self.zoom_participants[zoom_id] = {
                'zoom_name': zoom_name,
                'email': email,
                'room': room,
                'audio_source': audio_source,
                'audio_track': audio_track,
                'publication': publication,
                'connected_at': asyncio.get_event_loop().time()
            }

            logger.info(f"✅ {zoom_name} successfully connected to LiveKit")
            logger.info(f"   The AI agent will now see them as: '{zoom_name}'")
            logger.info(f"   Active Zoom participants in LiveKit: {len(self.zoom_participants)}")

        except Exception as e:
            logger.error(f"❌ Failed to add Zoom participant {zoom_name}: {e}", exc_info=True)
            raise

    async def remove_zoom_participant(self, zoom_id: str):
        """
        Remove a Zoom participant and disconnect their LiveKit connection.

        Args:
            zoom_id: Recall.ai's participant ID
        """
        if zoom_id not in self.zoom_participants:
            logger.warning(f"Cannot remove participant {zoom_id} - not found")
            return

        participant_info = self.zoom_participants[zoom_id]
        zoom_name = participant_info['zoom_name']

        logger.info(f"➖ Removing Zoom participant from LiveKit: {zoom_name}")

        try:
            # Unpublish audio track
            room = participant_info['room']
            if room.local_participant:
                await room.local_participant.unpublish_track(
                    participant_info['publication'].sid
                )

            # Disconnect from LiveKit room
            await room.disconnect()

            # Remove from tracking
            del self.zoom_participants[zoom_id]

            logger.info(f"✅ {zoom_name} disconnected from LiveKit")
            logger.info(f"   Remaining Zoom participants: {len(self.zoom_participants)}")

        except Exception as e:
            logger.error(f"Error removing participant {zoom_name}: {e}", exc_info=True)

    async def forward_audio(self, zoom_id: str, audio_data_base64: str):
        """
        Forward audio from Zoom participant to their LiveKit track.

        This is called when Recall.ai webhook sends audio data.

        Args:
            zoom_id: Recall.ai's participant ID
            audio_data_base64: Base64-encoded audio data from Recall.ai
        """
        if zoom_id not in self.zoom_participants:
            # Participant not tracked yet - might be the bot itself
            return

        participant_info = self.zoom_participants[zoom_id]
        audio_source = participant_info['audio_source']

        try:
            # Decode base64 audio data
            audio_bytes = base64.b64decode(audio_data_base64)

            # Convert to int16 numpy array
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)

            # Create AudioFrame for LiveKit
            frame = rtc.AudioFrame(
                data=audio_array.tobytes(),
                sample_rate=self.sample_rate,
                num_channels=self.num_channels,
                samples_per_channel=len(audio_array)
            )

            # Push to LiveKit audio source
            await audio_source.capture_frame(frame)

        except Exception as e:
            logger.error(f"Error forwarding audio for {participant_info['zoom_name']}: {e}")

    async def listen_for_agent_audio(self, recall_bot, agent_identity: str = "moderator-bot"):
        """
        Listen for audio from the AI agent in LiveKit and forward to Zoom.

        Args:
            recall_bot: RecallBot instance to send audio to
            agent_identity: Identity of the AI agent in LiveKit

        Note: This requires audio conversion (PCM → MP3) and base64 encoding.
        For production use, consider using a dedicated audio processing service.
        """
        logger.info(f"🎧 Agent audio monitoring started for: {agent_identity}")
        logger.info("   Will capture agent audio and forward to Zoom participants")

        # Create a separate LiveKit connection to monitor the room
        monitor_room = rtc.Room()

        # Create token for monitoring (can subscribe only)
        token = api.AccessToken(self.livekit_api_key, self.livekit_api_secret)
        token.with_identity(f"zoom-audio-monitor-{asyncio.get_event_loop().time()}")
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=self.room_name,
            can_subscribe=True
        ))
        monitor_token = token.to_jwt()

        # Buffer for collecting audio chunks
        audio_buffer = []
        buffer_duration_ms = 1000  # Send audio every 1 second

        @monitor_room.on("track_subscribed")
        def on_track_subscribed(
            track: rtc.Track,
            publication: rtc.TrackPublication,
            participant: rtc.RemoteParticipant
        ):
            """Called when we subscribe to a track"""
            # Match any agent (identity starts with "agent-")
            # Skip zoom participants (actual user names like "Srini M")
            # Skip our own monitor connection (starts with "zoom-audio-monitor-")
            is_agent = participant.identity.startswith("agent-")

            if is_agent and isinstance(track, rtc.AudioTrack):
                logger.info(f"✅ Subscribed to agent audio track: {participant.identity}")

                # Start processing audio frames
                asyncio.create_task(process_agent_audio(track, recall_bot))

        async def process_agent_audio(audio_track: rtc.AudioTrack, bot):
            """Process audio frames from agent and send to Zoom"""
            logger.info("🎤 Processing agent audio frames...")

            # Create audio buffer to batch frames before encoding
            # This is more efficient than encoding each small frame individually
            audio_buffer = AudioBuffer(
                sample_rate=24000,  # LiveKit agent typically uses 24kHz
                channels=1,  # Mono
                buffer_duration_ms=500,  # Send every 500ms
            )

            frame_count = 0
            sent_count = 0

            async for frame in rtc.AudioStream(audio_track):
                try:
                    frame_count += 1

                    # Add frame to buffer
                    buffered_audio = audio_buffer.add_frame(
                        frame.data.tobytes(),
                        time.time()
                    )

                    # If buffer is ready, convert and send
                    if buffered_audio:
                        try:
                            # Convert PCM to MP3 and base64 encode
                            mp3_base64 = convert_pcm_to_mp3_base64(
                                buffered_audio,
                                sample_rate=24000,
                                channels=1
                            )

                            # Send to Zoom via Recall.ai
                            success = await bot.send_audio_to_zoom(mp3_base64)

                            if success:
                                sent_count += 1
                                logger.debug(
                                    f"📤 Sent audio chunk #{sent_count} to Zoom "
                                    f"({len(buffered_audio)} bytes PCM → {len(mp3_base64)} chars base64)"
                                )
                            else:
                                logger.warning("Failed to send audio chunk to Zoom")

                        except Exception as e:
                            logger.error(f"Error converting/sending audio to Zoom: {e}")

                except Exception as e:
                    logger.error(f"Error processing agent audio frame: {e}")

            logger.info(f"Agent audio processing ended (received: {frame_count}, sent: {sent_count})")

        try:
            # Connect to LiveKit room as monitor
            await monitor_room.connect(self.livekit_url, monitor_token)
            logger.info("✅ Audio monitor connected to LiveKit room")

            # Keep monitoring until cleanup
            while True:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error in agent audio monitoring: {e}", exc_info=True)

    def get_participant_count(self) -> int:
        """
        Get number of Zoom participants currently connected to LiveKit.

        Returns:
            Number of participants
        """
        return len(self.zoom_participants)

    def get_participant_names(self) -> list:
        """
        Get list of Zoom participant names.

        Returns:
            List of participant names
        """
        return [info['zoom_name'] for info in self.zoom_participants.values()]

    def get_participant_info(self, zoom_id: str) -> Optional[Dict]:
        """
        Get information about a specific participant.

        Args:
            zoom_id: Recall.ai's participant ID

        Returns:
            Participant info dictionary or None
        """
        return self.zoom_participants.get(zoom_id)

    async def cleanup(self):
        """
        Disconnect all participants and cleanup resources.
        """
        logger.info("Cleaning up audio forwarder...")

        zoom_ids = list(self.zoom_participants.keys())
        for zoom_id in zoom_ids:
            await self.remove_zoom_participant(zoom_id)

        logger.info("✅ Audio forwarder cleanup complete")
