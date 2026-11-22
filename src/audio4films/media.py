"""Probe media with ffprobe and pick streams by language."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

# Common ISO 639 spellings -> the 639-2/B codes Matroska muxers usually write.
LANG_ALIASES = {
    "es": "spa", "esp": "spa",
    "en": "eng",
    "fr": "fre", "fra": "fre",
    "de": "ger", "deu": "ger",
    "it": "ita",
    "pt": "por",
    "ca": "cat",
    "ja": "jpn",
    "ko": "kor",
    "zh": "chi", "zho": "chi",
    "ru": "rus",
    "pl": "pol",
    "nl": "dut", "nld": "dut",
}

# Native names, used for default track titles shown by TV players.
LANG_NAMES = {
    "spa": "Español", "eng": "English", "fre": "Français", "ger": "Deutsch",
    "ita": "Italiano", "por": "Português", "cat": "Català", "jpn": "日本語",
    "kor": "한국어", "chi": "中文", "rus": "Русский", "pol": "Polski", "dut": "Nederlands",
}

HDR10PLUS_SEI = bytes([0xB5, 0x00, 0x3C, 0x00, 0x01, 0x04])  # ITU-T T.35, Samsung, HDR10+


class MediaError(RuntimeError):
    """A file could not be read or does not contain the requested stream."""


def norm_lang(code: str | None) -> str:
    code = (code or "und").strip().lower()
    return LANG_ALIASES.get(code, code)


@dataclass(frozen=True)
class Stream:
    index: int  # absolute stream index in the file (ffmpeg -map 0:<index>)
    kind: str  # video | audio | subtitle | data | attachment
    type_index: int  # position among streams of the same kind (ffmpeg a:N / s:N)
    codec: str
    language: str
    title: str
    channels: int | None
    sample_rate: int | None
    default: bool
    forced: bool
    start_time: float
    duration: float | None
    frame_rate: float | None
    dovi: bool
    attached_pic: bool

    def label(self) -> str:
        parts = [f"#{self.index}", self.kind, self.codec, self.language]
        if self.channels:
            parts.append(f"{self.channels}ch")
        if self.title:
            parts.append(f"'{self.title}'")
        if self.default:
            parts.append("default")
        if self.forced:
            parts.append("forced")
        if self.dovi:
            parts.append("Dolby Vision")
        return " ".join(parts)


@dataclass
class MediaInfo:
    source: str
    duration: float
    start_time: float
    streams: list[Stream]

    def of_kind(self, kind: str) -> list[Stream]:
        return [s for s in self.streams if s.kind == kind]

    @property
    def video(self) -> Stream | None:
        videos = [s for s in self.of_kind("video") if not s.attached_pic]
        return videos[0] if videos else None

    def audio_candidates(self, lang: str) -> list[Stream]:
        want = norm_lang(lang)
        return [s for s in self.of_kind("audio") if s.language == want]

    def subtitles(self, lang: str) -> list[Stream]:
        want = norm_lang(lang)
        return [s for s in self.of_kind("subtitle") if s.language == want]

    def reference_duration(self, stream: Stream | None = None) -> float:
        """Duration of the content that matters, not of the longest extra track."""
        for s in (stream, self.video):
            if s is not None and s.duration:
                return s.duration
        return self.duration


def _parse_clock(value: str) -> float | None:
    m = re.match(r"^(\d+):(\d+):(\d+(?:\.\d+)?)$", value.strip())
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _fraction(value: str | None) -> float | None:
    if not value or value in ("0/0", "N/A"):
        return None
    if "/" in value:
        num, den = value.split("/", 1)
        return float(num) / float(den) if float(den) else None
    return float(value)


def _stream_duration(raw: dict) -> float | None:
    if raw.get("duration") not in (None, "N/A"):
        return float(raw["duration"])
    for key, value in (raw.get("tags") or {}).items():
        if key.upper().startswith("DURATION"):
            return _parse_clock(value)
    return None


def probe(source: str) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", source]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"ffprobe could not read {source}: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout)
    counters: dict[str, int] = {}
    streams = []
    for raw in data.get("streams", []):
        kind = raw.get("codec_type", "data")
        type_index = counters.get(kind, 0)
        counters[kind] = type_index + 1
        tags = raw.get("tags") or {}
        disp = raw.get("disposition") or {}
        side = raw.get("side_data_list") or []
        streams.append(Stream(
            index=int(raw["index"]),
            kind=kind,
            type_index=type_index,
            codec=raw.get("codec_name", "?"),
            language=norm_lang(tags.get("language")),
            title=tags.get("title", ""),
            channels=raw.get("channels"),
            sample_rate=int(raw["sample_rate"]) if raw.get("sample_rate") else None,
            default=bool(disp.get("default")),
            forced=bool(disp.get("forced")),
            start_time=float(raw.get("start_time") or 0.0),
            duration=_stream_duration(raw),
            frame_rate=_fraction(raw.get("r_frame_rate")) if kind == "video" else None,
            dovi=any("DOVI" in (sd.get("side_data_type") or "") for sd in side),
            attached_pic=bool(disp.get("attached_pic")),
        ))
    fmt = data.get("format", {})
    return MediaInfo(
        source=source,
        duration=float(fmt.get("duration") or 0.0),
        start_time=float(fmt.get("start_time") or 0.0),
        streams=streams,
    )


def select_audio(info: MediaInfo, lang: str | None = None, track: int | None = None) -> tuple[Stream, list[Stream]]:
    """Pick an audio stream. Returns (chosen, all candidates that matched).

    `track` is the position among audio streams (ffmpeg a:N). Otherwise match by language,
    preferring the default track and then the one with most channels.
    """
    audio = info.of_kind("audio")
    if not audio:
        raise MediaError(f"{info.source} has no audio tracks.")
    if track is not None:
        if not 0 <= track < len(audio):
            raise MediaError(f"Audio track a:{track} does not exist. Tracks: " + "; ".join(s.label() for s in audio))
        return audio[track], [audio[track]]
    if lang:
        matches = info.audio_candidates(lang)
        if not matches:
            raise MediaError(
                f"No audio track in language '{norm_lang(lang)}' in {info.source}. Tracks: "
                + "; ".join(f"a:{s.type_index} {s.label()}" for s in audio)
            )
        ranked = sorted(matches, key=lambda s: (not s.default, -(s.channels or 0), s.index))
        return ranked[0], matches
    defaults = [s for s in audio if s.default]
    chosen = defaults[0] if defaults else audio[0]
    return chosen, [chosen]


def has_hdr10plus(source: str, stream: Stream, frames: int = 24) -> bool:
    """True if HDR10+ dynamic metadata SEI appears in the first frames of an HEVC stream."""
    if stream.codec != "hevc":
        return False
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", source, "-map", f"0:{stream.index}", "-c", "copy",
           "-frames:v", str(frames), "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", "-"]
    out = subprocess.run(cmd, capture_output=True).stdout
    return HDR10PLUS_SEI in out
