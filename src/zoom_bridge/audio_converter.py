"""
Audio Converter

Converts audio between different formats for Zoom integration.
Specifically handles PCM (from LiveKit) to MP3 (for Recall.ai).
"""

import logging
import io
import base64
from pydub import AudioSegment
from typing import Optional

logger = logging.getLogger(__name__)


class AudioConverter:
    """
    Handles audio format conversions for Zoom bridge.

    Primary use case: Convert LiveKit PCM audio to MP3 for Recall.ai
    """

    @staticmethod
    def pcm_to_mp3(
        pcm_data: bytes,
        sample_rate: int = 24000,
        channels: int = 1,
        sample_width: int = 2,
        bitrate: str = "128k"
    ) -> bytes:
        """
        Convert PCM audio data to MP3 format.

        Args:
            pcm_data: Raw PCM audio bytes (16-bit signed integers)
            sample_rate: Audio sample rate in Hz (default: 24000 for LiveKit agent)
            channels: Number of audio channels (1=mono, 2=stereo, default: 1)
            sample_width: Bytes per sample (2 for 16-bit audio, default: 2)
            bitrate: MP3 bitrate (default: "128k")

        Returns:
            MP3-encoded audio bytes

        Raises:
            RuntimeError: If ffmpeg is not installed or conversion fails
        """
        try:
            # Create AudioSegment from raw PCM data
            audio = AudioSegment(
                data=pcm_data,
                sample_width=sample_width,  # 16-bit = 2 bytes
                frame_rate=sample_rate,
                channels=channels
            )

            # Export as MP3 to in-memory buffer
            buffer = io.BytesIO()
            audio.export(
                buffer,
                format="mp3",
                bitrate=bitrate,
                parameters=["-q:a", "2"]  # VBR quality (0-9, lower is better)
            )

            # Get MP3 bytes
            buffer.seek(0)
            mp3_data = buffer.read()

            logger.debug(
                f"Converted {len(pcm_data)} bytes PCM to {len(mp3_data)} bytes MP3 "
                f"(sample_rate={sample_rate}, channels={channels}, bitrate={bitrate})"
            )

            return mp3_data

        except FileNotFoundError as e:
            logger.error("❌ ffmpeg not found! Install with: brew install ffmpeg")
            raise RuntimeError("ffmpeg is required for audio conversion") from e
        except Exception as e:
            logger.error(f"Error converting PCM to MP3: {e}")
            raise

    @staticmethod
    def pcm_to_mp3_base64(
        pcm_data: bytes,
        sample_rate: int = 24000,
        channels: int = 1,
        sample_width: int = 2,
        bitrate: str = "128k"
    ) -> str:
        """
        Convert PCM audio to MP3 and encode as base64 string.

        This is the format required by Recall.ai's Output Audio API.

        Args:
            pcm_data: Raw PCM audio bytes
            sample_rate: Audio sample rate in Hz
            channels: Number of audio channels
            sample_width: Bytes per sample
            bitrate: MP3 bitrate

        Returns:
            Base64-encoded MP3 string (ready for Recall.ai API)
        """
        mp3_data = AudioConverter.pcm_to_mp3(
            pcm_data,
            sample_rate,
            channels,
            sample_width,
            bitrate
        )

        # Encode to base64
        mp3_base64 = base64.b64encode(mp3_data).decode('utf-8')

        logger.debug(f"Encoded MP3 to base64 (length: {len(mp3_base64)} chars)")

        return mp3_base64

    @staticmethod
    def test_ffmpeg_availability() -> bool:
        """
        Test if ffmpeg is available and working.

        Returns:
            True if ffmpeg is available, False otherwise
        """
        try:
            # Try to create a simple AudioSegment (requires ffmpeg)
            test_audio = AudioSegment.silent(duration=100, frame_rate=16000)
            buffer = io.BytesIO()
            test_audio.export(buffer, format="mp3")
            logger.info("✅ ffmpeg is available and working")
            return True
        except FileNotFoundError:
            logger.error("❌ ffmpeg not found! Install with: brew install ffmpeg")
            return False
        except Exception as e:
            logger.error(f"❌ ffmpeg test failed: {e}")
            return False


class AudioBuffer:
    """
    Buffers audio frames for batch processing.

    Useful for collecting multiple small audio frames before encoding to MP3,
    which is more efficient than encoding each frame individually.
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        channels: int = 1,
        buffer_duration_ms: int = 500,
        max_silence_ms: int = 2000
    ):
        """
        Initialize audio buffer.

        Args:
            sample_rate: Audio sample rate in Hz
            channels: Number of audio channels
            buffer_duration_ms: How long to buffer before sending (milliseconds)
            max_silence_ms: Maximum silence before flushing buffer
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.buffer_duration_ms = buffer_duration_ms
        self.max_silence_ms = max_silence_ms

        self.buffer = bytearray()
        self.buffer_start_time = None
        self.last_audio_time = None

    def add_frame(self, pcm_data: bytes, timestamp: float) -> Optional[bytes]:
        """
        Add audio frame to buffer.

        Args:
            pcm_data: PCM audio data
            timestamp: Frame timestamp (seconds)

        Returns:
            Buffered PCM data if ready to flush, None otherwise
        """
        if self.buffer_start_time is None:
            self.buffer_start_time = timestamp

        # Add to buffer
        self.buffer.extend(pcm_data)
        self.last_audio_time = timestamp

        # Calculate buffer duration
        elapsed_ms = (timestamp - self.buffer_start_time) * 1000

        # Flush if buffer is full or silence timeout
        if elapsed_ms >= self.buffer_duration_ms:
            return self.flush()

        return None

    def flush(self) -> Optional[bytes]:
        """
        Flush the current buffer and return accumulated audio.

        Returns:
            Buffered PCM data, or None if buffer is empty
        """
        if not self.buffer:
            return None

        data = bytes(self.buffer)
        self.buffer = bytearray()
        self.buffer_start_time = None

        logger.debug(f"Flushed audio buffer: {len(data)} bytes")

        return data

    def get_buffer_size_ms(self) -> float:
        """
        Get current buffer size in milliseconds.

        Returns:
            Buffer duration in milliseconds
        """
        if self.buffer_start_time is None:
            return 0

        import time
        elapsed = (time.time() - self.buffer_start_time) * 1000
        return elapsed


# Singleton converter instance
_converter = AudioConverter()


def convert_pcm_to_mp3_base64(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1
) -> str:
    """
    Convenience function to convert PCM to base64-encoded MP3.

    Args:
        pcm_data: Raw PCM audio bytes
        sample_rate: Sample rate in Hz
        channels: Number of channels

    Returns:
        Base64-encoded MP3 string
    """
    return _converter.pcm_to_mp3_base64(pcm_data, sample_rate, channels)


def test_audio_conversion():
    """
    Test audio conversion functionality.

    Returns:
        True if successful, False otherwise
    """
    logger.info("Testing audio conversion...")

    # Test ffmpeg availability
    if not AudioConverter.test_ffmpeg_availability():
        return False

    # Test PCM to MP3 conversion
    try:
        # Create test PCM data (1 second of silence)
        sample_rate = 24000
        samples = sample_rate * 1  # 1 second
        test_pcm = bytes([0] * samples * 2)  # 16-bit = 2 bytes per sample

        # Convert to MP3
        mp3_base64 = convert_pcm_to_mp3_base64(test_pcm, sample_rate, channels=1)

        logger.info(f"✅ Audio conversion test passed (output: {len(mp3_base64)} chars)")
        return True

    except Exception as e:
        logger.error(f"❌ Audio conversion test failed: {e}")
        return False


if __name__ == "__main__":
    # Run test when executed directly
    logging.basicConfig(level=logging.INFO)
    test_audio_conversion()
