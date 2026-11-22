"""Command line: audio4films probe | sync | mux | verify | run | register | serve."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.parse
from pathlib import Path

from . import __version__
from . import mux as muxmod
from .media import LANG_NAMES, MediaError, MediaInfo, Stream, norm_lang, probe, select_audio
from .serve import lan_ip, make_server, register
from .sync import MIN_PEAK_RATIO, SyncResult, analyze
from .verify import MAX_INTERLEAVE_BYTES, MAX_RESIDUAL_MS, VerifyResult, verify

EXIT_OK, EXIT_ERROR, EXIT_UNSAFE_SYNC, EXIT_VERIFY_FAILED = 0, 1, 2, 3
WORK_DIR = ".audio4films-work"


class Out:
    """Human-readable messages go to stdout, or to stderr when --json owns stdout."""

    def __init__(self, json_mode: bool) -> None:
        self.json_mode = json_mode

    def __call__(self, msg: str = "") -> None:
        print(msg, file=sys.stderr if self.json_mode else sys.stdout, flush=True)


def default_out_dir() -> Path:
    env = os.environ.get("A4F_OUT_DIR")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    base = home / "Movies" if (home / "Movies").is_dir() else home / "Videos"
    return base / "audio4films"


def is_remote(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def stem_of(source: str) -> str:
    name = urllib.parse.unquote(urllib.parse.urlsplit(source).path) if is_remote(source) else source
    return Path(name).stem or "output"


def _check_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise MediaError(f"Missing {', '.join(missing)}. Install FFmpeg (macOS: brew install ffmpeg; "
                         "Debian/Ubuntu: apt install ffmpeg).")


def _pick(info: MediaInfo, lang: str | None, track: int | None, role: str, say: Out) -> Stream:
    flag = "--dub-track" if role.startswith("Dub") else "--vo-track"
    chosen, matches = select_audio(info, lang, track)
    how = f"track a:{track}" if track is not None else (f"language '{norm_lang(lang)}'" if lang else "default track")
    say(f"  {role}: a:{chosen.type_index} {chosen.label()}   (picked by {how})")
    if len(matches) > 1:
        others = ", ".join(f"a:{s.type_index} '{s.title or s.codec}'" for s in matches if s is not chosen)
        say(f"    Note: other tracks also match ({others}). Use {flag} N to pick one.")
    return chosen


def _print_probes(result: SyncResult, say: Out) -> None:
    say("    Original time   Dub time     Offset      Match clarity")
    for p in result.probes:
        mark = "used" if p.used else ("unclear, ignored" if p.peak_ratio < MIN_PEAK_RATIO else "disagrees")
        say(f"    {p.vo_time:10.1f}s  {p.dub_time:10.3f}s  {p.offset:+9.3f}s   {p.peak_ratio:5.2f}  {mark}")
    say(f"    Match clarity = best match / best match elsewhere; points under {MIN_PEAK_RATIO} are ignored.")


def _print_verify(v: VerifyResult, say: Out) -> None:
    if v.residuals:
        vals = [r.residual_ms for r in v.residuals if r.peak_ratio >= MIN_PEAK_RATIO]
        rng = f"{min(vals):+.1f} to {max(vals):+.1f} ms" if vals else "no clear match"
        say(f"    Dub vs original audio at {len(v.residuals)} points: {rng} (limit ±{MAX_RESIDUAL_MS:.0f} ms)")
    gaps = [i.gap for i in v.interleave if i.gap is not None]
    if gaps:
        say(f"    Dub stored next to the video: largest gap {max(gaps) / 1e6:.1f} MB "
            f"(limit {MAX_INTERLEAVE_BYTES / 1e6:.0f} MB, checked at {len(v.interleave)} points)")
    for feature, st in v.hdr.items():
        say(f"    {feature}: source {'yes' if st['source'] else 'no'}, output {'yes' if st['output'] else 'no'}")
    for problem in v.problems:
        say(f"    PROBLEM: {problem}")


def _localize_dub(dub: MediaInfo, audio: Stream, dub_lang: str, work: Path, say: Out) -> MediaInfo:
    subs = dub.subtitles(dub_lang)
    dest = work / f"{stem_of(dub.source)}.dub.mkv"
    say(f"  Copying the dub audio{' and ' + str(len(subs)) + ' subtitle tracks' if subs else ''} "
        f"to a local work file (one full read of the dub)...")
    muxmod.extract_tracks(dub, audio, subs, dest)
    return probe(str(dest))


# ---------------------------------------------------------------- commands

def cmd_probe(args: argparse.Namespace) -> int:
    for src in args.sources:
        info = probe(src)
        print(f"{src}\n  duration {info.duration:.1f}s (longest track), start {info.start_time:.3f}s")
        for s in info.streams:
            dur = f" {s.duration:.1f}s" if s.duration else ""
            print(f"  {s.kind[0]}:{s.type_index:<2} {s.label()}{dur}")
    return EXIT_OK


def cmd_sync(args: argparse.Namespace) -> int:
    say = Out(args.json)
    vo, dub = probe(args.vo), probe(args.dub)
    vo_a = _pick(vo, args.vo_lang, args.vo_track, "Original audio", say)
    dub_a = _pick(dub, args.dub_lang, args.dub_track, "Dub audio", say)
    analysed, shift = dub, 0.0
    if is_remote(args.dub):
        analysed = _localize_dub(dub, dub_a, dub_a.language, Path(args.work_dir), say)
        dub_a = analysed.of_kind("audio")[0]
        shift = dub.start_time - analysed.start_time  # convert back to the original dub file's timeline
    say(f"  Measuring the offset at 6 points (window {args.window:.0f}s, search ±{args.search:.0f}s)...")
    res = analyze(vo, vo_a, analysed, dub_a, window=args.window, search=args.search, speed=args.speed,
                  dub_fps=dub.video.frame_rate if dub.video else None)
    if res.offset is not None:
        res.offset = round(res.offset + shift, 4)
    _print_probes(res, say)
    say(f"  Verdict: {res.verdict}. {res.detail}")
    if res.offset is not None:
        say(f"  Offset to apply: {res.offset:+.3f}s (positive = delay the dub). Speed factor: {res.speed}")
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    return EXIT_OK if res.verdict == "constant" else EXIT_UNSAFE_SYNC


def cmd_mux(args: argparse.Namespace) -> int:
    say = Out(False)
    vo, dub = probe(args.vo), probe(args.dub)
    vo_a = _pick(vo, args.vo_lang, args.vo_track, "Original audio", say)
    dub_a = _pick(dub, args.dub_lang, args.dub_track, "Dub audio", say)
    out = Path(args.output) if args.output else default_out_dir() / f"{stem_of(args.vo)}.{dub_a.language}+{vo_a.language}.mkv"
    cmd = muxmod.build_command(vo, vo_a, dub, dub_a, out, args.offset, args.speed, vo_a.language, dub_a.language, args.dub_title)
    muxmod.run(cmd, vo.reference_duration(vo_a), out)
    say(f"  Written: {out}")
    say("  Next: audio4films verify " + json.dumps(str(out)) + " --source " + json.dumps(args.vo))
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    say = Out(args.json)
    say(f"  Verifying {args.output} ...")
    v = verify(args.output, args.source, args.dub_track, args.ref_track)
    _print_verify(v, say)
    say(f"  Result: {'PASSED' if v.passed else 'FAILED'}")
    if args.json:
        print(json.dumps(v.to_dict(), indent=2))
    return EXIT_OK if v.passed else EXIT_VERIFY_FAILED


def cmd_run(args: argparse.Namespace) -> int:
    say = Out(args.json)
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else default_out_dir()
    work = out_dir / WORK_DIR
    say(f"audio4films {__version__}")
    say(f"  Original: {args.vo}")
    say(f"  Dub:      {args.dub}")
    vo, dub = probe(args.vo), probe(args.dub)
    vo_a = _pick(vo, args.vo_lang, args.vo_track, "Original audio", say)
    dub_a = _pick(dub, args.dub_lang, args.dub_track, "Dub audio", say)
    out = Path(args.output) if args.output else out_dir / f"{stem_of(args.vo)}.{dub_a.language}+{vo_a.language}.mkv"

    say("1/4 Copying the dub tracks")
    local = _localize_dub(dub, dub_a, dub_a.language, work, say)
    local_a = local.of_kind("audio")[0]

    say("2/4 Measuring the offset")
    res = analyze(vo, vo_a, local, local_a, window=args.window, search=args.search, speed=args.speed,
                  dub_fps=dub.video.frame_rate if dub.video else None)
    _print_probes(res, say)
    say(f"  Verdict: {res.verdict}. {res.detail}")
    summary: dict = {"output": str(out), "sync": res.to_dict()}
    if res.verdict != "constant" or res.offset is None:
        say("  Stopped: a single shift would leave the dub out of sync somewhere. Nothing was written.")
        if args.json:
            print(json.dumps(summary, indent=2))
        return EXIT_UNSAFE_SYNC
    say(f"  Offset to apply: {res.offset:+.3f}s (positive = delay the dub). Speed factor: {res.speed}")

    say("3/4 Muxing (video and original audio are copied untouched)")
    cmd = muxmod.build_command(vo, vo_a, local, local_a, out, res.offset, res.speed, vo_a.language, dub_a.language,
                               args.dub_title)
    muxmod.run(cmd, vo.reference_duration(vo_a), out, show_progress=not args.json)

    say("4/4 Verifying the result")
    v = verify(str(out), args.vo)
    _print_verify(v, say)
    summary["verify"] = v.to_dict()
    if not args.keep_work:
        Path(local.source).unlink(missing_ok=True)
    if args.stremio_id:
        try:
            entry = register(out_dir, args.stremio_id, out, args.title or out.stem,
                             f"{LANG_NAMES.get(dub_a.language, dub_a.language)} + {LANG_NAMES.get(vo_a.language, vo_a.language)} audio",
                             args.group or "")
            summary["stremio"] = {"id": args.stremio_id, **entry}
            say(f"  Stremio: registered {args.stremio_id}. Run `audio4films serve` to stream it.")
        except ValueError:
            say(f"  Stremio: not registered, the output is outside {out_dir}. Use --out-dir instead of -o.")
    say(f"Result: {'PASSED' if v.passed else 'FAILED'}  ->  {out}")
    if args.json:
        print(json.dumps(summary, indent=2))
    return EXIT_OK if v.passed else EXIT_VERIFY_FAILED


def cmd_register(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else default_out_dir()
    entry = register(out_dir, args.stremio_id, Path(args.file), args.title or "", args.detail or "", args.group or "")
    print(f"Registered {args.stremio_id} -> {entry['path']} in {out_dir / 'stremio.json'}")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    server = make_server(out_dir, args.host, args.port)
    url = f"http://{lan_ip()}:{server.server_address[1]}/manifest.json"
    print(f"Serving {out_dir}", flush=True)
    print(f"In Stremio: Add-ons -> Add addon -> type this exact address:  {url}", flush=True)
    print("Type it by hand: stremio:// links force https, which this server does not speak.", flush=True)
    print("Keep this running while you watch. Ctrl+C stops it.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return EXIT_OK


# ---------------------------------------------------------------- parser

def _track_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--vo-lang", default="eng", help="language of the original audio to keep (default: eng)")
    p.add_argument("--dub-lang", default="spa", help="language of the dub to add (default: spa)")
    p.add_argument("--vo-track", type=int, help="original audio track a:N, overrides --vo-lang")
    p.add_argument("--dub-track", type=int, help="dub audio track a:N, overrides --dub-lang")


def _sync_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--window", type=float, default=45.0, help="seconds of audio compared at each point (default 45)")
    p.add_argument("--search", type=float, default=300.0, help="largest offset searched, in seconds (default 300)")
    p.add_argument("--speed", type=float, help="force the dub speed factor (default: frame rate ratio, e.g. 0.959 for a PAL dub)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="audio4films", description="Add a dubbed audio track from one release to a better video release, in sync, without re-encoding the video.")
    ap.add_argument("--version", action="version", version=f"audio4films {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="list the tracks of one or more files or URLs")
    p.add_argument("sources", nargs="+")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("sync", help="measure the dub offset at several points")
    p.add_argument("vo", help="original (high quality) file or URL")
    p.add_argument("dub", help="file or URL with the dubbed audio")
    _track_args(p)
    _sync_args(p)
    p.add_argument("--work-dir", default=str(default_out_dir() / WORK_DIR), help="where to copy a remote dub")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("mux", help="add the dub to the original with a known offset")
    p.add_argument("vo")
    p.add_argument("dub")
    p.add_argument("--offset", type=float, required=True, help="seconds to delay the dub (negative = advance)")
    p.add_argument("--speed", type=float, default=1.0, help="dub speed factor; != 1 re-encodes the dub to E-AC3")
    p.add_argument("-o", "--output")
    p.add_argument("--dub-title", help="track title shown by players (default: the dub's own title or language)")
    _track_args(p)
    p.set_defaults(func=cmd_mux)

    p = sub.add_parser("verify", help="check sync, interleaving and HDR of a finished file")
    p.add_argument("output")
    p.add_argument("--source", help="the original, to confirm Dolby Vision / HDR10+ were kept")
    p.add_argument("--dub-track", type=int, default=0, help="dub audio track a:N in the output (default 0)")
    p.add_argument("--ref-track", type=int, default=1, help="original audio track a:N in the output (default 1)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("run", help="copy dub tracks, sync, mux and verify in one go")
    p.add_argument("vo")
    p.add_argument("dub")
    p.add_argument("-o", "--output")
    p.add_argument("--out-dir", help="output folder (default: $A4F_OUT_DIR or ~/Movies/audio4films)")
    p.add_argument("--dub-title")
    p.add_argument("--stremio-id", help="register for Stremio: tt1234567 or tt1234567:SEASON:EPISODE")
    p.add_argument("--title", help="title shown in Stremio")
    p.add_argument("--group", help="Stremio binge group (same value for all episodes of a season)")
    p.add_argument("--keep-work", action="store_true", help="keep the local copy of the dub tracks")
    p.add_argument("--json", action="store_true")
    _track_args(p)
    _sync_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("register", help="offer an existing file in Stremio")
    p.add_argument("file")
    p.add_argument("--stremio-id", required=True)
    p.add_argument("--title")
    p.add_argument("--detail")
    p.add_argument("--group")
    p.add_argument("--out-dir")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("serve", help="Stremio addon + file server for the output folder")
    p.add_argument("--out-dir")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=7878, help="default 7878 (macOS uses 7000 for AirPlay)")
    p.set_defaults(func=cmd_serve)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _check_tools()
        return args.func(args)
    except MediaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
