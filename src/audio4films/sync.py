"""Measure how far a dubbed audio track is from the original, at several points.

Music and sound effects are shared between language versions; dialogue is not. Correlating
band-passed audio windows therefore finds the same moment in both files even though the
voices differ. Measuring at several points tells a constant offset (safe to fix with one
shift) apart from a cut scene (a step) or a speed mismatch (a drift).
"""

from __future__ import annotations

import statistics
import subprocess
from dataclasses import asdict, dataclass, field

import numpy as np

from .media import MediaError, MediaInfo, Stream

SR = 4000  # analysis sample rate (Hz); enough for the 60-1500 Hz band we correlate
MIN_PEAK_RATIO = 1.8  # a probe below this is ambiguous (dialogue-only, silence) and is ignored
AGREE_S = 0.050  # probes within 50 ms of the median count as agreeing
PROBE_FRACTIONS = (0.06, 0.22, 0.38, 0.54, 0.70, 0.86)


def decode(source: str, stream: Stream, start: float, duration: float) -> np.ndarray:
    """Decode a mono 4 kHz window. `start` is relative to the file's start time.

    Far from the start we seek (fast, also over HTTP). Near it, seeking can drop the first
    encoded frame (~32 ms for AC3/E-AC3), so we decode from the beginning and pad or trim
    so that sample 0 is exactly the file's time zero.
    """
    tail = ["-map", f"0:{stream.index}", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    if start < 1.0:
        filt = f"aresample=async=1:first_pts=0,atrim=start={max(0.0, start):.4f}:duration={duration:.4f}"
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", source, "-af", filt, *tail]
    else:
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", source, "-t", f"{duration:.3f}", *tail]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg could not decode {source}: {proc.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32).astype(np.float64)


def bandpass(x: np.ndarray, lo: float = 60.0, hi: float = 1500.0) -> np.ndarray:
    if len(x) == 0:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / SR)
    spec[(freqs < lo) | (freqs > hi)] = 0
    y = np.fft.irfft(spec, len(x))
    return (y - y.mean()) / (y.std() + 1e-12)


def stretch(x: np.ndarray, factor: float) -> np.ndarray:
    """Resample so the result lasts `factor` times longer (linear interpolation).

    This models how PAL releases are made: the film is played 25/23.976 faster by resampling,
    which also raises the pitch. Undoing it the same way restores both speed and pitch.
    """
    if abs(factor - 1.0) < 1e-9 or len(x) < 2:
        return x
    n_out = int(round(len(x) * factor))
    return np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)


def xcorr_valid(long: np.ndarray, short: np.ndarray) -> np.ndarray:
    """corr[k] = sum(short * long[k:k+len(short)]) for every full overlap, via FFT."""
    n = len(long) + len(short) - 1
    nfft = 1 << (n - 1).bit_length()
    full = np.fft.irfft(np.fft.rfft(long, nfft) * np.fft.rfft(short[::-1], nfft), nfft)[:n]
    return full[len(short) - 1: len(long)]


@dataclass
class Probe:
    vo_time: float  # where the window starts in the original
    dub_time: float  # where the same moment starts in the dub (after speed correction)
    offset: float  # seconds to add to the dub so it lines up: vo_time - dub_time
    peak_ratio: float  # best match / best match elsewhere; higher = more certain
    used: bool = False


@dataclass
class SyncResult:
    verdict: str  # constant | drift | step | unreliable
    offset: float | None  # seconds to delay the dub (negative = advance it)
    speed: float  # dub speed factor (fps_vo / fps_dub); 1.0 = same speed
    fps_vo: float | None
    fps_dub: float | None
    probes: list[Probe] = field(default_factory=list)
    detail: str = ""
    suggested_speed: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def measure_one(vo: MediaInfo, vo_stream: Stream, dub: MediaInfo, dub_stream: Stream,
                t: float, window: float, search: float, speed: float) -> Probe | None:
    seg = bandpass(decode(vo.source, vo_stream, t, window))
    if len(seg) < window * SR * 0.9:
        return None
    u0 = max(0.0, (t - search) * speed)
    ref = decode(dub.source, dub_stream, u0, (window + 2 * search) * speed)
    ref = bandpass(stretch(ref, 1.0 / speed))
    if len(ref) <= len(seg):
        return None
    corr = np.abs(xcorr_valid(ref, seg))
    k = int(np.argmax(corr))
    elsewhere = np.ones(len(corr), dtype=bool)
    elsewhere[max(0, k - SR): k + SR] = False
    second = corr[elsewhere].max() if elsewhere.any() else 1e-12
    dub_time = u0 / speed + k / SR
    return Probe(vo_time=round(t, 3), dub_time=round(dub_time, 4), offset=round(t - dub_time, 4),
                 peak_ratio=round(float(corr[k] / (second + 1e-12)), 2))


def classify(probes: list[Probe], speed: float, duration: float) -> tuple[str, float | None, str, float | None]:
    good = [p for p in probes if p.peak_ratio >= MIN_PEAK_RATIO]
    if len(good) < 3:
        return ("unreliable", None,
                f"Only {len(good)} of {len(probes)} points gave a clear match (need 3). "
                "The versions may differ too much, or the window falls on dialogue-only scenes.", None)
    med = statistics.median(p.offset for p in good)
    agree = [p for p in good if abs(p.offset - med) <= AGREE_S]
    if len(agree) >= 3 and len(agree) >= len(good) - 1:
        for p in agree:
            p.used = True
        offset = statistics.median(p.offset for p in agree)
        return ("constant", round(offset, 4),
                f"{len(agree)} of {len(good)} clear points agree within {int(AGREE_S * 1000)} ms.", None)
    good.sort(key=lambda p: p.vo_time)
    xs = np.array([p.vo_time for p in good])
    ys = np.array([p.offset for p in good])
    slope, intercept = np.polyfit(xs, ys, 1)
    residual = float(np.max(np.abs(ys - (slope * xs + intercept))))
    if residual <= AGREE_S and abs(slope) * duration > 0.1:
        suggested = speed * (1.0 - slope)
        return ("drift", None,
                f"The offset grows steadily ({slope * 1000:+.2f} ms per second): the dub runs at a slightly "
                f"different speed. Try --speed {suggested:.6f}.", round(float(suggested), 6))
    steps = [(a, b) for a, b in zip(good, good[1:]) if abs(b.offset - a.offset) > AGREE_S]
    where = "; ".join(f"{a.offset:+.3f}s at {a.vo_time:.0f}s -> {b.offset:+.3f}s at {b.vo_time:.0f}s" for a, b in steps)
    return ("step", None,
            f"The offset jumps between points ({where}). A scene is cut or added in one version; "
            "a single shift cannot fix it.", None)


def analyze(vo: MediaInfo, vo_stream: Stream, dub: MediaInfo, dub_stream: Stream,
            window: float = 45.0, search: float = 300.0, speed: float | None = None,
            dub_fps: float | None = None) -> SyncResult:
    """`dub_fps` overrides the dub's frame rate, for when `dub` is an audio-only copy of it."""
    fps_vo = vo.video.frame_rate if vo.video else None
    fps_dub = dub_fps or (dub.video.frame_rate if dub.video else None)
    if speed is None:
        speed = (fps_vo / fps_dub) if (fps_vo and fps_dub) else 1.0
        if abs(speed - 1.0) < 0.001:
            speed = 1.0
    duration = vo.reference_duration(vo_stream)
    window = min(window, duration / 8)
    probes = []
    for frac in PROBE_FRACTIONS:
        p = measure_one(vo, vo_stream, dub, dub_stream, (duration - window) * frac, window, search, speed)
        if p:
            probes.append(p)
    verdict, offset, detail, suggested = classify(probes, speed, duration)
    return SyncResult(verdict=verdict, offset=offset, speed=round(speed, 6),
                      fps_vo=round(fps_vo, 4) if fps_vo else None, fps_dub=round(fps_dub, 4) if fps_dub else None,
                      probes=probes, detail=detail, suggested_speed=suggested)
