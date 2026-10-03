"""Regression test for QQAdapter._detect_message_type unknown-media logging.

The static method logged an unknown media content type through
``self._log_tag`` — but no instance exists in a staticmethod, so any
QQ message carrying a non-media attachment (e.g. a PDF) crashed the
adapter with NameError instead of defaulting to TEXT.
"""

import pytest

from gateway.platforms.qqbot.adapter import QQAdapter
from gateway.platforms.base import MessageType


class TestDetectMessageType:
    def test_unknown_content_type_returns_text_without_raising(self):
        # Before the fix this raised NameError (self is undefined in a
        # staticmethod) as soon as the debug log fired.
        assert (
            QQAdapter._detect_message_type(
                ["https://example.com/file.pdf"], ["application/pdf"]
            )
            is MessageType.TEXT
        )

    def test_audio_maps_to_voice(self):
        assert (
            QQAdapter._detect_message_type(
                ["https://example.com/a.silk"], ["audio/silk"]
            )
            is MessageType.VOICE
        )

    def test_video_maps_to_video(self):
        assert (
            QQAdapter._detect_message_type(
                ["https://example.com/v.mp4"], ["video/mp4"]
            )
            is MessageType.VIDEO
        )

    def test_image_maps_to_photo(self):
        assert (
            QQAdapter._detect_message_type(
                ["https://example.com/i.jpg"], ["image/jpeg"]
            )
            is MessageType.PHOTO
        )

    def test_no_media_returns_text(self):
        assert QQAdapter._detect_message_type([], []) is MessageType.TEXT

    def test_media_without_types_defaults_to_photo(self):
        assert (
            QQAdapter._detect_message_type(["https://example.com/x"], [])
            is MessageType.PHOTO
        )
