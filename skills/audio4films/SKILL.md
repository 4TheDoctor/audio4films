---
name: audio4films
description: "Trigger: add a dubbed or other-language audio track to a better video file, sync a dub, /audio4films. Lossless mux with measured, verified sync."
license: MIT
compatibility: "Needs ffmpeg/ffprobe 6+ and the audio4films CLI: uv tool install git+https://github.com/4TheDoctor/audio4films"
metadata:
  author: 4TheDoctor
  version: "0.1.0"
---

## Activation Contract

Load when the user wants the video of one release (e.g. 4K HDR) with the audio of another (e.g. a dub in their language), wants to know the offset between two versions, or wants the result streamed to Stremio on their TV.

## Hard Rules

- Drive everything through the `audio4films` CLI; never hand-build ffmpeg commands.
- Never mux when the sync verdict is not `constant`. Report the verdict and the probe table instead.
- Never call a result done unless `verify` says PASSED; always report its numbers.
- The video is never re-encoded. Do not offer to.
- Work only with files or URLs the user provides. Do not search for, download or recommend sources.
- Ask before overwriting an existing output file.
- When several audio tracks match the dub language (e.g. Castilian and Latin American Spanish), ask which one.

## Decision Gates

| Situation | Action |
| --- | --- |
| `audio4films` or `ffmpeg` missing | Install: `uv tool install git+https://github.com/4TheDoctor/audio4films` (or pipx); ffmpeg via brew/apt |
| Verdict `constant` | Proceed (`run` already muxed and verified) |
| Verdict `drift` | Show the suggested `--speed`; rerun with it only if the user agrees |
| Verdict `step` | Stop: one version has a scene cut or added. Explain; nothing to mux |
| Verdict `unreliable` | Check languages with `probe`, retry with `--window 90`; if still unreliable, stop |
| Verify fails on interleaving | `mkvmerge -o fixed.mkv OUT.mkv`, replace, verify again |
| Verify fails on sync or HDR | Report it, keep the file, do not claim success |
| User wants it on the TV | `run --stremio-id`, then `serve`; follow `references/stremio.md` |

## Execution Steps

1. Run `audio4films --version` and `ffprobe -version`; apply the install gate if needed.
2. Get the original (best video) and the dub source, plus languages (defaults: original `eng`, dub `spa`).
3. `audio4films probe ORIGINAL DUB`; confirm the tracks to use.
4. `audio4films run ORIGINAL DUB --vo-lang X --dub-lang Y --json` (series: one call per episode, same `--group`, `--stremio-id ttID:S:E`). Long runs go in the background.
5. Read the JSON: `sync.verdict`, `sync.offset`, `sync.probes`, `verify.passed`, `verify.residuals`, `verify.interleave`, `verify.hdr`. Apply the gates.
6. For Stremio: start `audio4films serve` in the background and give the user the printed URL to type in Stremio.

## Output Contract

Return one row per file:

| File | Offset | Points agreeing | Dub vs original | Interleave gap | Dolby Vision / HDR10+ | Result |

Then state what was verified (numbers above) apart from what was not (e.g. playback on the TV), and the next step.

## References

- `references/how-sync-works.md`: the method, the verdicts and their thresholds.
- `references/troubleshooting.md`: symptoms, causes and fixes.
- `references/stremio.md`: serving to Stremio on a TV, and its pitfalls.
