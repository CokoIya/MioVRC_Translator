# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Wire protocol for Microsoft Edge's speech-recognition WebSocket service.

The endpoint is the recognition counterpart of the Edge read-aloud service and
shares its trusted-client token and ``Sec-MS-GEC`` anti-abuse signature. Framing
is Microsoft's Speech SDK format: text frames carry ``Key:Value`` headers, a
blank line, then a JSON body; binary frames prefix the same headers with a
big-endian ``uint16`` header length.

Kept separate from the provider so the framing can be unit-tested without a
socket.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import struct
from typing import Any

TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
CHROMIUM_FULL_VERSION = "143.0.3650.75"
CHROMIUM_MAJOR_VERSION = CHROMIUM_FULL_VERSION.split(".", 1)[0]
SEC_MS_GEC_VERSION = f"1-{CHROMIUM_FULL_VERSION}"
SPEECH_HOST = "speech.platform.bing.com"
RECOGNITION_PATH = "/speech/recognition/edge/interactive/v1"

# Windows file-time epoch (1601-01-01) offset from the Unix epoch, in seconds.
WIN_EPOCH_SECONDS = 11644473600
# The service rounds the signed timestamp down to a five-minute boundary.
SEC_MS_GEC_WINDOW_SECONDS = 300

BITS_PER_SAMPLE = 16
CHANNELS = 1

WSS_HEADERS = {
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "Origin": "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{CHROMIUM_MAJOR_VERSION}.0.0.0 "
        f"Safari/537.36 Edg/{CHROMIUM_MAJOR_VERSION}.0.0.0"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def generate_sec_ms_gec(clock_skew_seconds: float = 0.0) -> str:
    """Sign the current five-minute window with the trusted client token.

    ``clock_skew_seconds`` compensates a wrong local clock: the service rejects
    signatures from the wrong window, and its ``Date`` response header is the
    only way to discover the offset.
    """

    ticks = _utc_now().timestamp() + float(clock_skew_seconds)
    ticks += WIN_EPOCH_SECONDS
    ticks -= ticks % SEC_MS_GEC_WINDOW_SECONDS
    # Windows file time counts 100-nanosecond intervals.
    ticks *= 1e9 / 100
    payload = f"{ticks:.0f}{TRUSTED_CLIENT_TOKEN}".encode("ascii")
    return hashlib.sha256(payload).hexdigest().upper()


def build_recognition_url(language: str, clock_skew_seconds: float = 0.0) -> str:
    """Build the recognition WebSocket URL for one language."""

    return (
        f"wss://{SPEECH_HOST}{RECOGNITION_PATH}"
        f"?TrustedClientToken={TRUSTED_CLIENT_TOKEN}"
        f"&Sec-MS-GEC={generate_sec_ms_gec(clock_skew_seconds)}"
        f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}"
        f"&language={language}"
        "&profanity=raw"
    )


def _timestamp() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def text_message(
    path: str,
    body: dict[str, Any],
    *,
    content_type: str | None = None,
    request_id: str | None = None,
) -> str:
    headers = [f"X-Timestamp:{_timestamp()}", f"Path:{path}"]
    if request_id:
        headers.append(f"X-RequestId:{request_id}")
    if content_type:
        headers.append(f"Content-Type:{content_type}")
    return "\r\n".join(headers) + "\r\n\r\n" + json.dumps(body)


def binary_message(
    path: str,
    request_id: str,
    payload: bytes,
    *,
    stream_id: str | None = None,
    content_type: str | None = None,
) -> bytes:
    headers = [
        f"X-Timestamp:{_timestamp()}",
        f"Path:{path}",
        f"X-RequestId:{request_id}",
    ]
    if content_type:
        headers.append(f"Content-Type:{content_type}")
    if stream_id:
        headers.append(f"X-StreamId:{stream_id}")
    head = "\r\n".join(headers).encode("utf-8")
    return struct.pack(">H", len(head)) + head + payload


def speech_config_message(sample_rate: int) -> str:
    return text_message(
        "speech.config",
        {
            "context": {
                "audio": {
                    "source": {
                        "bitspersample": str(BITS_PER_SAMPLE),
                        "channelcount": str(CHANNELS),
                        "model": "",
                        "samplerate": str(int(sample_rate)),
                        "type": "Stream",
                    }
                },
                "os": {"name": "Client", "platform": "Windows", "version": "10"},
                "system": {
                    "build": "Windows-x64",
                    "name": "SpeechSDK",
                    "version": "1.15.0",
                },
            }
        },
        content_type="application/json",
    )


def speech_context_message(
    request_id: str,
    *,
    stream_id: str = "1",
    previous_service_tag: str | None = None,
    offset_ticks: int | None = None,
) -> str:
    """Open a recognition turn.

    Passing ``previous_service_tag`` continues the existing session instead of
    starting a new one, which is what lets a single socket serve many
    utterances without reconnecting.
    """

    body: dict[str, Any] = {"audio": {"streams": {stream_id: None}}}
    if previous_service_tag and offset_ticks is not None:
        body["continuation"] = {
            "audio": {"streams": {stream_id: {"offset": str(int(offset_ticks))}}},
            "previousServiceTag": previous_service_tag,
        }
    return text_message(
        "speech.context",
        body,
        content_type="application/json",
        request_id=request_id,
    )


def wav_header(sample_rate: int) -> bytes:
    """A RIFF header with zero-length data; the stream supplies the samples."""

    byte_rate = sample_rate * CHANNELS * BITS_PER_SAMPLE // 8
    block_align = CHANNELS * BITS_PER_SAMPLE // 8
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 0),
            b"WAVE",
            b"fmt ",
            struct.pack("<I", 16),
            struct.pack("<H", 1),
            struct.pack("<H", CHANNELS),
            struct.pack("<I", int(sample_rate)),
            struct.pack("<I", byte_rate),
            struct.pack("<H", block_align),
            struct.pack("<H", BITS_PER_SAMPLE),
            b"data",
            struct.pack("<I", 0),
        )
    )


def parse_message(raw: str | bytes) -> tuple[str, str]:
    """Split one frame into its ``Path`` header value and its body."""

    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
        if len(data) < 2:
            return "", ""
        head_len = struct.unpack(">H", data[:2])[0]
        head = data[2 : 2 + head_len].decode("utf-8", "replace")
        body = data[2 + head_len :].decode("utf-8", "replace")
    else:
        head, _, body = str(raw).partition("\r\n\r\n")

    path = ""
    for line in head.split("\r\n"):
        if line[:5].lower() == "path:":
            path = line.split(":", 1)[1].strip()
            break
    return path, body


def parse_json_body(body: str) -> dict[str, Any]:
    try:
        parsed = json.loads(body or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bytes_to_offset_ticks(byte_count: int, sample_rate: int) -> int:
    """Convert a sent-byte count into the 100-nanosecond offset the API wants."""

    bytes_per_second = max(1, int(sample_rate) * CHANNELS * BITS_PER_SAMPLE // 8)
    return int(byte_count / bytes_per_second * 10_000_000)
