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
    es_cut = mix("aselect='not(between(t,70,75))',asetpts=N/SR/TB", bed, v_es, d / "es_cut.wav")
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
