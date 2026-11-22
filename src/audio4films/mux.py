"""Build and run the ffmpeg commands: extract the dub tracks, then mux them into the original."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .media import LANG_NAMES, MediaError, MediaInfo, Stream

FORCED_HINTS = ("forced", "forzad", "forcé", "forcat", "erzwungen")


def channel_label(channels: int | None) -> str:
    return {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(channels or 0, f"{channels}ch" if channels else "")


def default_title(stream: Stream) -> str:
    if stream.title:
        return stream.title
    return f"{LANG_NAMES.get(stream.language, stream.language)} {channel_label(stream.channels)}".strip()


def is_forced(stream: Stream) -> bool:
    return stream.forced or any(h in stream.title.lower() for h in FORCED_HINTS)


def extract_tracks(dub: MediaInfo, audio: Stream, subtitles: list[Stream], dest: Path) -> Path:
    """Copy the dub's audio (and subtitles) into a small local file.

    Reading a remote dub once is far cheaper than seeking it many times during sync,
    and the mux then reads a local file. Sync and mux must use the same file, because
    ffmpeg aligns each input to its own start time.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part.mkv")
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", dub.source, "-map", f"0:{audio.index}"]
    for s in subtitles:
        cmd += ["-map", f"0:{s.index}"]
    cmd += ["-c", "copy", "-y", str(part)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        part.unlink(missing_ok=True)
        raise MediaError(f"Could not extract the dub tracks: {proc.stderr.strip()[-300:]}")
    part.replace(dest)
    return dest


def retime_filter(speed: float, sample_rate: int) -> str:
    """Slow down (speed < 1) or speed up a dub made at another frame rate, the way PAL releases
    were made: by resampling. This restores the original pitch as well as the duration."""
    return f"asetrate={sample_rate * speed:.4f},aresample={sample_rate}"


def build_command(vo: MediaInfo, vo_audio: Stream, dub: MediaInfo, dub_audio: Stream, output: Path,
                  offset: float, speed: float = 1.0, vo_lang: str | None = None, dub_lang: str | None = None,
                  dub_title: str | None = None) -> list[str]:
    """Return the ffmpeg command.

    Input 0 is the original (video and original audio are copied untouched). Input 1 is the dub.
    With equal frame rates the dub audio is copied too and shifted with -itsoffset; otherwise it is
    re-timed with speed and re-encoded to E-AC3. The video is never re-encoded.
    """
    if vo.video is None:
        raise MediaError(f"{vo.source} has no video track.")
    retime = abs(speed - 1.0) > 1e-6
    dub_lang = dub_lang or dub_audio.language
    vo_lang = vo_lang or vo_audio.language

    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", vo.source]
    if not retime:
        cmd += ["-itsoffset", f"{offset:.4f}"]
    cmd += ["-i", dub.source]
    # Without this, a fast local input gets written minutes ahead of a slow (remote) one,
    # and players lose the dub after seeking.
    cmd += ["-max_interleave_delta", "0"]

    cmd += ["-map", f"0:{vo.video.index}", "-map", f"1:{dub_audio.index}", "-map", f"0:{vo_audio.index}"]
    subs: list[tuple[int, Stream]] = []
    if not retime:  # dub subtitles are timed to the dub; only valid when we only shift it
        subs += [(1, s) for s in dub.subtitles(dub_lang)]
    subs += [(0, s) for s in vo.subtitles(vo_lang)]
    for inp, s in subs:
        cmd += ["-map", f"{inp}:{s.index}"]

    cmd += ["-c", "copy"]
    if retime:
        base = retime_filter(speed, dub_audio.sample_rate or 48000)
        if offset >= 0:
            filt = f"{base},adelay={round(offset * 1000)}:all=1"
        else:
            filt = f"{base},atrim=start={-offset:.4f},asetpts=PTS-STARTPTS"
        cmd += ["-filter:a:0", filt, "-c:a:0", "eac3", "-b:a:0", "640k"]

    cmd += ["-metadata:s:a:0", f"language={dub_lang}", "-metadata:s:a:0", f"title={dub_title or default_title(dub_audio)}",
            "-metadata:s:a:1", f"language={vo_lang}", "-metadata:s:a:1", f"title={default_title(vo_audio)}",
            "-disposition:a:0", "default", "-disposition:a:1", "0"]
    first_forced_done = False
    for i, (_, s) in enumerate(subs):
        flag = "0"
        if is_forced(s) and s.language == dub_lang and not first_forced_done:
            flag, first_forced_done = "default+forced", True
        elif is_forced(s):
            flag = "forced"
        cmd += [f"-disposition:s:{i}", flag]

    cmd += ["-progress", "pipe:1", "-stats_period", "2", "-y", str(output)]
    return cmd


def _fmt(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def run(cmd: list[str], duration: float, output: Path, show_progress: bool = True) -> None:
    """Run ffmpeg into <output>.part.mkv, show progress, then rename into place."""
    part = output.with_name(output.name + ".part.mkv")
    cmd = cmd[:-1] + [str(part)]
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    values: dict[str, str] = {}
    width = shutil.get_terminal_size((80, 20)).columns
    for line in proc.stdout:  # type: ignore[union-attr]
        key, _, val = line.strip().partition("=")
        values[key] = val
        if key != "progress" or not show_progress:
            continue
        try:
            done = int(values.get("out_time_us", "0")) / 1e6
        except ValueError:
            done = 0.0
        frac = min(done / duration, 1.0) if duration else 0.0
        bar = "#" * int(30 * frac) + "-" * (30 - int(30 * frac))
        msg = f"\r  Muxing [{bar}] {frac * 100:5.1f}%  {_fmt(done)} of {_fmt(duration)}  speed {values.get('speed', '?')}"
        sys.stderr.write(msg[: width - 1])
        sys.stderr.flush()
    err = proc.stderr.read() if proc.stderr else ""  # type: ignore[union-attr]
    proc.wait()
    if show_progress:
        sys.stderr.write("\n")
    if proc.returncode != 0:
        part.unlink(missing_ok=True)
        raise MediaError(f"ffmpeg failed (exit {proc.returncode}): {err.strip()[-500:]}")
    part.replace(output)
