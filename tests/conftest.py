"""Synthetic fixtures built with ffmpeg's lavfi sources.

A shared noise "bed" stands in for music and effects; each language gets its own "voice" noise
on top. That is the property the tool relies on: the bed is common, the voices are not.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

DURATION = 150
DUB_OFFSET = 0.25  # the dub's content starts 0.25 s later than the original's

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True)


def noise(path: Path, color: str, seed: int, amp: float, seconds: int = DURATION + 30) -> Path:
    ff("-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c={color}:r=48000:a={amp}:seed={seed}", str(path))
    return path


def seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def srt(path: Path, text: str) -> Path:
    path.write_text(f"1\n00:00:05,000 --> 00:00:08,000\n{text}\n\n2\n00:01:00,000 --> 00:01:03,000\n{text} 2\n")
    return path


def mix(bed_filter: str, bed: Path, voice: Path, out: Path) -> Path:
    ff("-i", str(bed), "-i", str(voice), "-filter_complex",
       f"[0:a]{bed_filter}[b];[b][1:a]amix=inputs=2:duration=first:dropout_transition=0,volume=2,"
       f"atrim=0:{DURATION},asetpts=PTS-STARTPTS[m]", "-map", "[m]", str(out))
    return out


def video_args(rate: str) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=s=160x90:r={rate}:d={DURATION}"]


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("media")
    bed = noise(d / "bed.wav", "pink", 7, 0.5)
    v_en, v_es, v_fr = (noise(d / f"v_{k}.wav", "brown", s, 0.3) for k, s in (("en", 11), ("es", 13), ("fr", 17)))

    en = mix("anull", bed, v_en, d / "en.wav")
    fr = mix("anull", bed, v_fr, d / "fr.wav")
    es = mix(f"adelay={int(DUB_OFFSET * 1000)}", bed, v_es, d / "es.wav")
    # A scene cut: 5 s of the bed missing at 70 s. Built with atrim + concat because aselect left
    # the audio uncut on FFmpeg 6.1 (Ubuntu 24.04). The assert keeps a silent fixture from passing.
    bed_cut = d / "bed_cut.wav"
    ff("-i", str(bed), "-filter_complex",
       "[0:a]asplit[x][y];[x]atrim=0:70[x1];[y]atrim=start=75,asetpts=PTS-STARTPTS[y1];"
       "[x1][y1]concat=n=2:v=0:a=1[c]", "-map", "[c]", str(bed_cut))
    assert abs(seconds(bed) - seconds(bed_cut) - 5) < 0.1, "fixture: the scene cut was not applied"
    es_cut = mix("anull", bed_cut, v_es, d / "es_cut.wav")
    # PAL dub: the same content resampled to play 25/23.976 times faster (pitch goes up too).
    es_pal = mix(f"asetrate={48000 * 25 / (24000 / 1001):.4f},aresample=48000", bed, v_es, d / "es_pal.wav")

    # Original: French first and default, English second (the order that broke v1).
    vo = d / "vo.mkv"
    ff(*video_args("24000/1001"), "-i", str(fr), "-i", str(en), "-i", str(srt(d / "en.srt", "Hello")),
       "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "ac3", "-b:a", "192k", "-c:s", "srt",
       "-metadata:s:a:0", "language=fre", "-metadata:s:a:1", "language=eng", "-metadata:s:s:0", "language=eng",
       "-disposition:a:0", "default", "-disposition:a:1", "0", str(vo))

    def dub(name: str, audio: Path, rate: str) -> Path:
        out = d / name
        ff(*video_args(rate), "-i", str(audio), "-i", str(srt(d / "forced.srt", "Cartel")),
           "-i", str(srt(d / "full.srt", "Hola")),
           "-map", "0:v", "-map", "1:a", "-map", "2:s", "-map", "3:s",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "ac3", "-b:a", "192k", "-c:s", "srt",
           "-metadata:s:a:0", "language=spa", "-metadata:s:s:0", "language=spa", "-metadata:s:s:0", "title=Forzados",
           "-metadata:s:s:1", "language=spa", "-disposition:s:0", "forced", str(out))
        return out

    return {
        "vo": vo,
        "dub": dub("dub.mkv", es, "24000/1001"),
        "dub_cut": dub("dub_cut.mkv", es_cut, "24000/1001"),
        "dub_pal": dub("dub_pal.mkv", es_pal, "25"),
    }
