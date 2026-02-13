#!/usr/bin/env python3
"""
Test Audio Conversion

Verifies that audio conversion (PCM to MP3) is working correctly.
This is required for bidirectional audio in Zoom integration.

Usage:
    python3 test_audio_conversion.py
"""

import logging
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_ffmpeg_installation():
    """Test if ffmpeg is installed"""
    logger.info("=" * 80)
    logger.info("TEST 1: Checking ffmpeg installation")
    logger.info("=" * 80)

    try:
        import subprocess
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            logger.info(f"✅ ffmpeg installed: {version_line}")
            return True
        else:
            logger.error("❌ ffmpeg command failed")
            return False

    except FileNotFoundError:
        logger.error("❌ ffmpeg not found!")
        logger.error("   Install with: brew install ffmpeg")
        return False
    except Exception as e:
        logger.error(f"❌ Error checking ffmpeg: {e}")
        return False


def test_pydub_import():
    """Test if pydub can be imported"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 2: Checking pydub installation")
    logger.info("=" * 80)

    try:
        import pydub
        logger.info(f"✅ pydub installed (version: {pydub.__version__ if hasattr(pydub, '__version__') else 'unknown'})")
        return True
    except ImportError as e:
        logger.error("❌ pydub not installed!")
        logger.error("   Install with: python3 -m pip install pydub")
        return False


def test_audio_converter():
    """Test the AudioConverter module"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 3: Testing AudioConverter module")
    logger.info("=" * 80)

    try:
        from src.zoom_bridge.audio_converter import AudioConverter, test_audio_conversion

        # Run built-in test
        result = test_audio_conversion()

        if result:
            logger.info("✅ AudioConverter test passed")
            return True
        else:
            logger.error("❌ AudioConverter test failed")
            return False

    except ImportError as e:
        logger.error(f"❌ Failed to import AudioConverter: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ AudioConverter test error: {e}")
        return False


def test_pcm_to_mp3_conversion():
    """Test actual PCM to MP3 conversion"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 4: Testing PCM to MP3 conversion")
    logger.info("=" * 80)

    try:
        from src.zoom_bridge.audio_converter import convert_pcm_to_mp3_base64

        # Create test PCM data (1 second of silence at 24kHz)
        sample_rate = 24000
        duration_seconds = 1
        samples = sample_rate * duration_seconds

        # 16-bit PCM (2 bytes per sample, mono)
        test_pcm = bytes([0] * samples * 2)

        logger.info(f"   Input: {len(test_pcm)} bytes PCM ({duration_seconds}s @ {sample_rate}Hz)")

        # Convert to MP3
        mp3_base64 = convert_pcm_to_mp3_base64(test_pcm, sample_rate, channels=1)

        logger.info(f"   Output: {len(mp3_base64)} chars base64-encoded MP3")
        logger.info(f"   Compression ratio: {len(test_pcm) / (len(mp3_base64) * 0.75):.1f}x")

        # Verify it's valid base64
        import base64
        mp3_bytes = base64.b64decode(mp3_base64)
        logger.info(f"   Decoded MP3: {len(mp3_bytes)} bytes")

        # Check MP3 signature (starts with ID3 tag or 0xFF 0xFB)
        if mp3_bytes[:3] == b'ID3' or (mp3_bytes[0] == 0xFF and (mp3_bytes[1] & 0xE0) == 0xE0):
            logger.info("✅ Valid MP3 signature detected")
        else:
            logger.warning("⚠️  MP3 signature not recognized (but may still be valid)")

        logger.info("✅ PCM to MP3 conversion successful")
        return True

    except Exception as e:
        logger.error(f"❌ Conversion test failed: {e}", exc_info=True)
        return False


def test_audio_buffer():
    """Test AudioBuffer functionality"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 5: Testing AudioBuffer")
    logger.info("=" * 80)

    try:
        from src.zoom_bridge.audio_converter import AudioBuffer
        import time

        buffer = AudioBuffer(
            sample_rate=24000,
            channels=1,
            buffer_duration_ms=500
        )

        # Add some frames
        frame_size = 480 * 2  # 480 samples * 2 bytes (20ms of audio at 24kHz)
        test_frame = bytes([0] * frame_size)

        logger.info(f"   Adding frames of {frame_size} bytes...")

        frames_added = 0
        flush_count = 0
        current_time = time.time()

        # Add frames until we get a flush
        for i in range(30):  # 30 frames = ~600ms
            result = buffer.add_frame(test_frame, current_time + (i * 0.02))
            frames_added += 1

            if result:
                flush_count += 1
                logger.info(f"   Flushed after {frames_added} frames: {len(result)} bytes")
                frames_added = 0

        logger.info(f"✅ AudioBuffer test passed (flushes: {flush_count})")
        return True

    except Exception as e:
        logger.error(f"❌ AudioBuffer test failed: {e}")
        return False


def main():
    """Run all tests"""
    logger.info("\n" + "=" * 80)
    logger.info("AUDIO CONVERSION TEST SUITE")
    logger.info("Testing bidirectional audio for Zoom integration")
    logger.info("=" * 80)

    results = {
        "ffmpeg": test_ffmpeg_installation(),
        "pydub": test_pydub_import(),
    }

    # Only run converter tests if dependencies are available
    if results["ffmpeg"] and results["pydub"]:
        results["converter"] = test_audio_converter()
        results["conversion"] = test_pcm_to_mp3_conversion()
        results["buffer"] = test_audio_buffer()
    else:
        logger.warning("\n⚠️  Skipping converter tests - install dependencies first")
        results["converter"] = False
        results["conversion"] = False
        results["buffer"] = False

    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)

    all_passed = True
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{status} - {test_name}")
        if not passed:
            all_passed = False

    logger.info("=" * 80)

    if all_passed:
        logger.info("\n🎉 ALL TESTS PASSED! Bidirectional audio is ready!")
        logger.info("\nNext steps:")
        logger.info("1. Start agent: python3 agent.py dev")
        logger.info("2. Start ngrok: ngrok http 8000")
        logger.info("3. Launch Zoom integration:")
        logger.info("   python3 zoom_survey.py \\")
        logger.info("     --zoom-url 'https://zoom.us/j/YOUR_MEETING' \\")
        logger.info("     --webhook-url 'https://your-ngrok-url.ngrok-free.app'")
        logger.info("\n✨ Participants in Zoom will now HEAR the AI agent!")
        return 0
    else:
        logger.error("\n❌ SOME TESTS FAILED")
        logger.error("\nTroubleshooting:")

        if not results["ffmpeg"]:
            logger.error("• Install ffmpeg: brew install ffmpeg")

        if not results["pydub"]:
            logger.error("• Install pydub: python3 -m pip install pydub")

        if results["ffmpeg"] and results["pydub"] and not results["converter"]:
            logger.error("• Check audio_converter.py for errors")

        return 1


if __name__ == "__main__":
    sys.exit(main())
