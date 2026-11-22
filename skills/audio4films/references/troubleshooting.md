# Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `No audio track in language 'xx'` | Track untagged (`und`) or tagged differently | `audio4films probe FILE`, then `--dub-track N` / `--vo-track N` |
| Wrong original language kept | The first or default track is another language | Pass `--vo-lang`; v0.1 never assumes track 0 |
| Verdict `unreliable` | Different mixes, dialogue-only windows, wrong tracks | Check tracks; retry with `--window 90` |
| Verdict `step` | Extended or censored cut, a different intro mid-file | Use versions of the same cut; the tool will not guess |
| Verdict `drift` | Frame rates tagged equal but speeds differ | Rerun with the suggested `--speed` |
| Format duration looks longer than the episode | An extra track (e.g. another dub) runs longer | Harmless: the tool uses the video and chosen tracks' durations |
| Verify: dub far from video in the file | Muxed by another tool without strict interleaving | `mkvmerge -o fixed.mkv file.mkv` |
| Verify: Dolby Vision missing | Very old ffmpeg | Use ffmpeg 6 or newer |
| Remote sources are slow | Every step reads over the network | `run` copies the dub's audio once; the video is read once during the mux |
