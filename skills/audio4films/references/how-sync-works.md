# How the sync works

## Why two different languages can be lined up

A dub replaces the voices but keeps the music and sound effects (the "M&E" track). audio4films
band-passes both audio tracks to 60–1500 Hz and cross-correlates them. The shared music and effects
produce one sharp peak at the true offset even though the dialogue differs.

## Several points, not one

`sync` compares a window (default 45 s) of the original at 6 points spread over the file and
searches the dub up to ±300 s around each. Each point yields:

- **offset**: seconds to add to the dub so it lines up (positive = delay it);
- **match clarity**: best match divided by the best match elsewhere. Under 1.8 the point is
  ambiguous (dialogue-only scene, silence) and is ignored.

| Verdict | Meaning | What the tool does |
| --- | --- | --- |
| `constant` | ≥3 clear points within 50 ms of the median (one outlier allowed) | Uses the median |
| `drift` | Offsets grow on a straight line | Suggests a `--speed` and stops |
| `step` | Offsets jump between points | Stops: a scene is cut or added |
| `unreliable` | Fewer than 3 clear points | Stops |

## Frame rates (PAL dubs)

If the frame rates differ (e.g. a 25 fps PAL dub for a 23.976 fps original), the dub was made by
playing the film faster through resampling, which also raised the pitch. The tool undoes it the
same way (`asetrate` + `aresample`). That restores both the speed and the pitch, and needs the dub
audio to be re-encoded (E-AC3 640 kb/s). Dub subtitles are dropped in this case, since they follow
the dub's timing.

## Timelines

ffmpeg aligns each input to its own start time. Sync and mux therefore use the same local copy of
the dub's tracks (`run` makes one). Near the start of a file, the tool decodes from time zero
instead of seeking, because seeking there can drop the first AC3/E-AC3 frame (32 ms).

## Interleaving

The dub is written next to the video for the same moment (`-max_interleave_delta 0`). Without it,
a fast local dub gets written minutes ahead of a slow, remote video, and players lose the dub
after seeking. `verify` checks that the dub packets sit within 50 MB of the video ones at 25%, 50%
and 75% of the file.

## Verification

`verify` seeks both audio tracks of the finished file to the same times at 5 points and measures
the residual. It must stay within ±20 ms; real files land within ±1 ms. It also checks that
Dolby Vision (configuration record) and HDR10+ (T.35 SEI in the first frames) survived when the
original had them.
