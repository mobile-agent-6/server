"""Strict WAV validation for the public voice contract."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from app.modules.voice.errors import (
    AudioUnsupported,
    SpeechAudioTooLarge,
    SpeechNotRecognized,
    TtsUnavailable,
)

MAX_VOICE_BYTES = 1_048_576
MAX_VOICE_FRAMES = 240_000
VOICE_SAMPLE_RATE = 16_000
VOICE_CHANNELS = 1
VOICE_SAMPLE_WIDTH_BYTES = 2

_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE
_PCM_SUBFORMAT_GUID = bytes.fromhex("0100000000001000800000aa00389b71")


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_rate: int
    bits_per_sample: int
    block_align: int
    frame_count: int


def normalized_media_type(content_type: str | None) -> str:
    if content_type is None:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _unsupported() -> AudioUnsupported:
    return AudioUnsupported()


def parse_pcm_wav(audio_bytes: bytes) -> WavInfo:
    """Parse one PCM RIFF/WAVE without trusting declared frame counts."""

    if len(audio_bytes) < 12 or audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        raise _unsupported()

    riff_size = struct.unpack_from("<I", audio_bytes, 4)[0]
    riff_end = riff_size + 8
    if riff_end < 12 or riff_end > len(audio_bytes):
        raise _unsupported()

    fmt: tuple[int, int, int, int, int, int] | None = None
    data_size: int | None = None
    position = 12
    while position < riff_end:
        if position + 8 > riff_end:
            raise _unsupported()
        chunk_id = audio_bytes[position : position + 4]
        chunk_size = struct.unpack_from("<I", audio_bytes, position + 4)[0]
        chunk_start = position + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > riff_end:
            raise _unsupported()

        if chunk_id == b"fmt ":
            if fmt is not None or data_size is not None or chunk_size < 16:
                raise _unsupported()
            audio_format, channels, sample_rate, byte_rate, block_align, bits = (
                struct.unpack_from("<HHIIHH", audio_bytes, chunk_start)
            )
            if audio_format == _WAVE_FORMAT_EXTENSIBLE:
                if chunk_size < 40:
                    raise _unsupported()
                extension_size = struct.unpack_from("<H", audio_bytes, chunk_start + 16)[0]
                valid_bits = struct.unpack_from("<H", audio_bytes, chunk_start + 18)[0]
                subformat = audio_bytes[chunk_start + 24 : chunk_start + 40]
                if extension_size < 22 or valid_bits != bits or subformat != _PCM_SUBFORMAT_GUID:
                    raise _unsupported()
            elif audio_format != _WAVE_FORMAT_PCM:
                raise _unsupported()
            fmt = (audio_format, channels, sample_rate, byte_rate, block_align, bits)
        elif chunk_id == b"data":
            if fmt is None or data_size is not None:
                raise _unsupported()
            data_size = chunk_size

        position = chunk_end + (chunk_size & 1)
        if position > riff_end:
            raise _unsupported()

    if fmt is None or data_size is None:
        raise _unsupported()

    _, channels, sample_rate, byte_rate, block_align, bits = fmt
    if channels <= 0 or sample_rate <= 0 or bits <= 0 or bits % 8 != 0:
        raise _unsupported()
    expected_align = channels * (bits // 8)
    if block_align != expected_align or byte_rate != sample_rate * block_align:
        raise _unsupported()
    if data_size % block_align != 0:
        raise _unsupported()

    return WavInfo(
        channels=channels,
        sample_rate=sample_rate,
        bits_per_sample=bits,
        block_align=block_align,
        frame_count=data_size // block_align,
    )


def validate_stt_wav(audio_bytes: bytes, *, content_type: str | None) -> WavInfo:
    if len(audio_bytes) > MAX_VOICE_BYTES:
        raise SpeechAudioTooLarge()
    if not audio_bytes:
        raise SpeechNotRecognized()
    if normalized_media_type(content_type) != "audio/wav":
        raise AudioUnsupported()

    info = parse_pcm_wav(audio_bytes)
    if (
        info.channels != VOICE_CHANNELS
        or info.sample_rate != VOICE_SAMPLE_RATE
        or info.bits_per_sample != VOICE_SAMPLE_WIDTH_BYTES * 8
        or info.block_align != VOICE_SAMPLE_WIDTH_BYTES
    ):
        raise AudioUnsupported()
    if info.frame_count == 0:
        raise SpeechNotRecognized()
    if info.frame_count > MAX_VOICE_FRAMES:
        raise SpeechAudioTooLarge()
    return info


def validate_tts_wav(audio_bytes: bytes) -> WavInfo:
    try:
        info = parse_pcm_wav(audio_bytes)
    except AudioUnsupported as exc:
        raise TtsUnavailable() from exc
    if info.frame_count == 0:
        raise TtsUnavailable()
    return info
