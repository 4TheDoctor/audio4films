# audio4films

**Put the dub from one release onto the video of a better one, in sync, without re-encoding the video.**

*[Leer en español](README.es.md)*

It started on a Sunday night. The 4K Dolby Vision + HDR10+ version of a series existed only in
English, and the Spanish dub only came with a lesser video. audio4films joins the two: it measures
how far apart the two audio tracks are, refuses to guess when the versions differ, muxes losslessly,
and checks the result before calling it done.

```text
2/4 Measuring the offset
    Original time   Dub time     Offset      Match clarity
         139.7s     139.699s     +0.035s    6.05  used
         512.4s     512.321s     +0.034s    5.96  used
         885.0s     884.943s     +0.034s    5.67  used
        1257.6s    1257.565s     +0.034s   13.04  used
        1630.2s    1630.187s     +0.034s    4.19  used
        2002.8s    2002.809s     +0.034s    3.26  used
  Verdict: constant. 6 of 6 clear points agree within 50 ms.
3/4 Muxing (video and original audio are copied untouched)
4/4 Verifying the result
    Dub vs original audio at 5 points: -0.5 to -0.3 ms (limit ±20 ms)
    Dub stored next to the video: largest gap 0.4 MB (limit 50 MB, checked at 3 points)
    Dolby Vision: source yes, output yes
    HDR10+: source yes, output yes
Result: PASSED
```

*(Real output from a 39-minute 4K episode. The original was read over HTTP; the run took 4 minutes.)*

## What it does

- **Picks tracks by language**, not by position. The first audio track of a multi-language release is often not the one you want.
- **Measures the offset at 6 points** by correlating music and effects, which both versions share. If the offset is not the same everywhere (a cut scene, a speed difference), it **stops and tells you why** instead of producing a file that drifts.
- **Handles PAL dubs** (25 fps vs 23.976): it undoes the speed-up and the pitch shift.
- **Muxes losslessly**: video, original audio and subtitles are copied. Dolby Vision and HDR10+ are kept. The dub becomes the default track, its forced subtitles are on by default, and the original language stays one click away.
- **Writes the dub next to the video** in the file, so seeking never loses the dub.
- **Verifies** the finished file: dub vs original at 5 points, interleaving, Dolby Vision and HDR10+.
- **Streams to Stremio** on your TV over the home network (optional).

Inputs can be local files or HTTP(S) URLs.

## Install

Requires [FFmpeg](https://ffmpeg.org) 6 or newer (`brew install ffmpeg` / `apt install ffmpeg`) and Python 3.10+.

```bash
uv tool install git+https://github.com/4TheDoctor/audio4films
# or: pipx install git+https://github.com/4TheDoctor/audio4films
```

### As a Claude Code skill

```text
/plugin marketplace add 4TheDoctor/audio4films
/plugin install audio4films@audio4films
```

Then ask Claude something like *"add the Spanish audio from dub.mkv to movie-4k.mkv"*. The skill
drives the CLI and won't call it done until verification passes. You still need the CLI installed
(see above).

## Use

```bash
# Everything in one go: copy the dub tracks, sync, mux, verify.
audio4films run movie-4k.mkv movie-dub.mkv --vo-lang eng --dub-lang spa

# Look at the tracks first.
audio4films probe movie-4k.mkv movie-dub.mkv

# Step by step.
audio4films sync   movie-4k.mkv movie-dub.mkv          # prints the offset and a verdict
audio4films mux    movie-4k.mkv movie-dub.mkv --offset 0.034
audio4films verify ~/Movies/audio4films/movie-4k.spa+eng.mkv --source movie-4k.mkv
```

Output goes to `~/Movies/audio4films` (or `~/Videos/audio4films`). Change it with `--out-dir` or `A4F_OUT_DIR`.

| Exit code | Meaning |
| --- | --- |
| 0 | Done and verified |
| 1 | Error (missing file, track or tool) |
| 2 | Not safe to sync with one shift (`step`, `drift` or `unreliable`); nothing was written |
| 3 | Written, but verification failed. Read the PROBLEM lines |

### A whole season

```bash
for e in 06 07 08 09 10; do
  audio4films run "Show.S03E$e.2160p.mkv" "Show.S03E$e.dub.mkv" \
    --stremio-id "tt1234567:3:${e#0}" --title "Show S03E$e" --group show-s03
done
```

Measure every episode separately. On one real season the offsets were +34, +161, +34, +84 and +53 ms.

### Watch it in Stremio

```bash
audio4films serve
# In Stremio: Add-ons -> Add addon -> type http://<this-computer-ip>:7878/manifest.json
```

Type the `http://` address by hand, because `stremio://` links force HTTPS. See
[skills/audio4films/references/stremio.md](skills/audio4films/references/stremio.md) for the pitfalls.

## How it works

See [how-sync-works.md](skills/audio4films/references/how-sync-works.md). In short: band-pass both
tracks to 60–1500 Hz, cross-correlate 45-second windows at 6 points, accept only a constant
offset, shift the dub with `-itsoffset` and mux with strict interleaving, then measure the result
again.

## Limits

- Both versions must be the same cut. If they are not, it stops (`step`); it does not try to splice.
- The dub needs the same music and effects as the original. A remixed or re-scored dub will not match.
- PAL dubs that were pitch-corrected at mastering may not correlate.
- Stremio streaming is plain HTTP on your LAN, and the computer must stay on.

## Prior art

- [syncstart](https://github.com/rpuntaie/syncstart), [sync-offset-tool](https://pypi.org/project/sync-offset-tool/) and [AudioSync](https://github.com/zzattack/AudioSync) measure audio offsets between two files.
- [ffsubsync](https://github.com/smacke/ffsubsync) uses similar correlation for subtitles.

audio4films adds track selection by language, multi-point checks that refuse unsafe shifts, a lossless mux that keeps HDR, verification and Stremio streaming.

## Legal

Use it with media you have the right to use. It works on files and URLs you provide. It does not
search for or download content.

## License

[MIT](LICENSE)
