"""Check a finished file: dub aligned with the original audio, sane interleaving, HDR kept."""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field

import numpy as np

from .media import MediaInfo, Stream, has_hdr10plus, probe
from .sync import MIN_PEAK_RATIO, SR, bandpass, decode, xcorr_valid

MAX_RESIDUAL_MS = 20.0  # lip sync is hard to notice below ~45 ms; we demand much less
MAX_INTERLEAVE_BYTES = 50_000_000  # dub packets must sit within 50 MB of the video for the same time
PAD = 5.0


@dataclass
class Residual:
    time: float
    residual_ms: float
    peak_ratio: float


@dataclass
class Interleave:
    time: float
    audio_byte: int | None
    video_byte: int | None

    @property
    def gap(self) -> int | None:
        if self.audio_byte is None or self.video_byte is None:
            return None
        return abs(self.audio_byte - self.video_byte)


@dataclass
class VerifyResult:
    passed: bool
    residuals: list[Residual] = field(default_factory=list)
    interleave: list[Interleave] = field(default_factory=list)
    hdr: dict[str, dict[str, bool]] = field(default_factory=dict)  # feature -> {"source": x, "output": y}
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["interleave"] = [{**asdict(i), "gap": i.gap} for i in self.interleave]
        return d


def residuals(info: MediaInfo, dub: Stream, ref: Stream, window: float = 40.0) -> list[Residual]:
    """Seek both tracks to the same times; a ~0 ms residual means they are aligned."""
    duration = info.reference_duration()
    window = min(window, duration / 8)
    out = []
    for frac in (0.08, 0.27, 0.46, 0.65, 0.84):
        t = max(PAD, (duration - window - PAD) * frac)
        a = bandpass(decode(info.source, ref, t, window))
        b = bandpass(decode(info.source, dub, t - PAD, window + 2 * PAD))
        if len(a) < window * SR * 0.9 or len(b) <= len(a):
            continue
        corr = np.abs(xcorr_valid(b, a))
        k = int(np.argmax(corr))
        elsewhere = np.ones(len(corr), dtype=bool)
        elsewhere[max(0, k - SR): k + SR] = False
        second = corr[elsewhere].max() if elsewhere.any() else 1e-12
        out.append(Residual(time=round(t, 1), residual_ms=round((k / SR - PAD) * 1000, 1),
                            peak_ratio=round(float(corr[k] / (second + 1e-12)), 2)))
    return out


def _first_packet_byte(source: str, stream: Stream, t: float) -> int | None:
    cmd = ["ffprobe", "-v", "error", "-select_streams", str(stream.index), "-read_intervals", f"{t}%+20",
           "-show_entries", "packet=pts_time,pos", "-of", "csv=p=0", source]
    lines = subprocess.run(cmd, capture_output=True, text=True).stdout.split()
    for line in lines:
        pts, _, pos = line.partition(",")
        try:
            if float(pts) >= t and pos not in ("", "N/A"):
                return int(pos)
        except ValueError:
            continue
    return None


def interleave(info: MediaInfo, dub: Stream) -> list[Interleave]:
    duration = info.reference_duration()
    video = info.video
    out = []
    for frac in (0.25, 0.5, 0.75):
        t = round(duration * frac, 1)
        out.append(Interleave(time=t, audio_byte=_first_packet_byte(info.source, dub, t),
                              video_byte=_first_packet_byte(info.source, video, t) if video else None))
    return out


def verify(output: str, source: str | None = None, dub_track: int = 0, ref_track: int = 1) -> VerifyResult:
    info = probe(output)
    audio = info.of_kind("audio")
    result = VerifyResult(passed=True)
    if len(audio) <= max(dub_track, ref_track):
        result.passed = False
        result.problems.append(f"Expected at least {max(dub_track, ref_track) + 1} audio tracks, found {len(audio)}.")
        return result
    dub, ref = audio[dub_track], audio[ref_track]

    result.residuals = residuals(info, dub, ref)
    clear = [r for r in result.residuals if r.peak_ratio >= MIN_PEAK_RATIO]
    if len(clear) < 3:
        result.problems.append(f"Only {len(clear)} points gave a clear match; alignment could not be confirmed.")
    bad = [r for r in clear if abs(r.residual_ms) > MAX_RESIDUAL_MS]
    if bad:
        result.problems.append("Dub is off by " + ", ".join(f"{r.residual_ms:+.1f} ms at {r.time:.0f}s" for r in bad) + ".")

    result.interleave = interleave(info, dub)
    for i in result.interleave:
        if i.gap is None:
            result.problems.append(f"Could not locate packets at {i.time:.0f}s to check interleaving.")
        elif i.gap > MAX_INTERLEAVE_BYTES:
            result.problems.append(f"At {i.time:.0f}s the dub sits {i.gap / 1e6:.0f} MB away from the video; "
                                   "seeking may lose the dub. Remux with mkvmerge to fix.")

    if info.video is not None:
        out_hdr = {"Dolby Vision": info.video.dovi, "HDR10+": has_hdr10plus(output, info.video)}
        src_hdr = dict.fromkeys(out_hdr, False)
        if source:
            src = probe(source)
            if src.video is not None:
                src_hdr = {"Dolby Vision": src.video.dovi, "HDR10+": has_hdr10plus(source, src.video)}
        for feature in out_hdr:
            result.hdr[feature] = {"source": src_hdr[feature], "output": out_hdr[feature]}
            if src_hdr[feature] and not out_hdr[feature]:
                result.problems.append(f"{feature} was in the source but is missing from the output.")

    result.passed = not result.problems
    return result
