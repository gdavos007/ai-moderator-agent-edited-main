"""
Zoom Bridge Module

This module provides integration between Zoom meetings (via Recall.ai) and LiveKit,
allowing the AI moderator agent to participate in Zoom meetings.
"""

from .recall_bot import RecallBot
from .webhook_handler import WebhookHandler
from .audio_forwarder import AudioForwarder
from .audio_converter import AudioConverter, AudioBuffer, convert_pcm_to_mp3_base64

__all__ = [
    'RecallBot',
    'WebhookHandler',
    'AudioForwarder',
    'AudioConverter',
    'AudioBuffer',
    'convert_pcm_to_mp3_base64'
]
