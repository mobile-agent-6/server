from __future__ import annotations

import struct

import pytest

from app.modules.voice.audio import (
    MAX_VOICE_BYTES,
    parse_pcm_wav,
    validate_stt_wav,
    validate_tts_wav,
)
from app.modules.voice.errors import (
    AudioUnsupported,
    SpeechAudioTooLarge,
    SpeechNotRecognized,
    TtsUnavailable,
)


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    padding = b"\0" if len(payload) & 1 else b""
    return chunk_id + struct.pack("<I", len(payload)) + payload + padding


def pcm_wav(
    *,
    frames: int = 1_600,
    channels: int = 1,
    sample_rate: int = 16_000,
    bits: int = 16,
    junk_size: int = 0,
) -> bytes:
    block_align = channels * (bits // 8)
    fmt = struct.pack(
        "<HHIIHH",
        1,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        bits,
    )
    chunks = [_chunk(b"fmt ", fmt)]
    if junk_size:
        chunks.append(_chunk(b"JUNK", b"x" * junk_size))
    chunks.append(_chunk(b"data", b"\0" * (frames * block_align)))
    body = b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_valid_stt_pcm_contract() -> None:
    info = validate_stt_wav(pcm_wav(), content_type="audio/wav; charset=binary")
    assert (info.channels, info.sample_rate, info.bits_per_sample) == (1, 16_000, 16)


def test_exact_size_and_duration_boundaries() -> None:
    base = pcm_wav()
    junk_size = MAX_VOICE_BYTES - len(base) - 8
    exact_size = pcm_wav(junk_size=junk_size)
    assert len(exact_size) == MAX_VOICE_BYTES
    validate_stt_wav(exact_size, content_type="audio/wav")

    with pytest.raises(SpeechAudioTooLarge):
        validate_stt_wav(exact_size + b"x", content_type="audio/wav")

    validate_stt_wav(pcm_wav(frames=240_000), content_type="audio/wav")
    with pytest.raises(SpeechAudioTooLarge):
        validate_stt_wav(pcm_wav(frames=240_001), content_type="audio/wav")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"channels": 2},
        {"sample_rate": 8_000},
        {"bits": 8},
    ],
)
def test_stt_rejects_wrong_pcm_shape(kwargs: dict[str, int]) -> None:
    with pytest.raises(AudioUnsupported):
        validate_stt_wav(pcm_wav(**kwargs), content_type="audio/wav")


def test_empty_zero_frame_and_wrong_media_type_are_distinct() -> None:
    with pytest.raises(SpeechNotRecognized):
        validate_stt_wav(b"", content_type="audio/wav")
    with pytest.raises(SpeechNotRecognized):
        validate_stt_wav(pcm_wav(frames=0), content_type="audio/wav")
    with pytest.raises(AudioUnsupported):
        validate_stt_wav(pcm_wav(), content_type=None)
    with pytest.raises(AudioUnsupported):
        validate_stt_wav(pcm_wav(), content_type="audio/m4a")


def test_parser_rejects_truncated_and_multiple_data_chunks() -> None:
    valid = pcm_wav()
    with pytest.raises(AudioUnsupported):
        parse_pcm_wav(valid[:-1])

    fmt_chunk = valid[12:36]
    data_chunk = valid[36:]
    body = fmt_chunk + data_chunk + _chunk(b"data", b"\0\0")
    duplicated = b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body
    with pytest.raises(AudioUnsupported):
        parse_pcm_wav(duplicated)


def test_tts_accepts_other_pcm_rate_but_rejects_invalid_wav() -> None:
    info = validate_tts_wav(pcm_wav(sample_rate=22_050))
    assert info.sample_rate == 22_050
    with pytest.raises(TtsUnavailable):
        validate_tts_wav(b"RIFFfake")
