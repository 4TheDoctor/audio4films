from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from audio4films.cli import main
from audio4films.media import probe, select_audio
from audio4films.serve import make_server, register
from audio4films.sync import analyze
from audio4films.verify import verify

from .conftest import DUB_OFFSET


def _sync(media, dub_key):
    vo, dub = probe(str(media["vo"])), probe(str(media[dub_key]))
    vo_a, _ = select_audio(vo, "eng")
    dub_a, _ = select_audio(dub, "spa")
    return analyze(vo, vo_a, dub, dub_a, window=15, search=20)


def test_picks_original_language_not_first_track(media):
    info = probe(str(media["vo"]))
    chosen, _ = select_audio(info, "en")  # 2-letter code is normalised to eng
    assert chosen.language == "eng" and chosen.type_index == 1
    default, _ = select_audio(info)
    assert default.language == "fre"  # without a language, the default track wins


def test_constant_offset(media):
    res = _sync(media, "dub")
    assert res.verdict == "constant", res.detail
    assert res.offset == pytest.approx(-DUB_OFFSET, abs=0.003)
    assert res.speed == 1.0


def test_cut_scene_is_refused(media):
    res = _sync(media, "dub_cut")
    assert res.verdict == "step", res.detail
    assert res.offset is None


def test_pal_dub_is_retimed(media):
    res = _sync(media, "dub_pal")
    assert res.speed == pytest.approx(24000 / 1001 / 25, abs=1e-4)
    assert res.verdict == "constant", res.detail


def test_run_end_to_end(media, tmp_path):
    rc = main(["run", str(media["vo"]), str(media["dub"]), "--out-dir", str(tmp_path), "--window", "15",
               "--search", "20", "--stremio-id", "tt0000001", "--dub-title", "Castellano"])
    assert rc == 0
    out = next(tmp_path.glob("*.spa+eng.mkv"))
    info = probe(str(out))
    audio = info.of_kind("audio")
    assert [(a.language, a.default, a.codec) for a in audio] == [("spa", True, "ac3"), ("eng", False, "ac3")]
    assert audio[0].title == "Castellano"
    subs = info.of_kind("subtitle")
    assert [(s.language, s.forced, s.default) for s in subs] == [("spa", True, True), ("spa", False, False),
                                                                ("eng", False, False)]
    v = verify(str(out), str(media["vo"]))
    assert v.passed, v.problems
    assert all(abs(r.residual_ms) <= 2 for r in v.residuals if r.peak_ratio >= 1.8)
    registry = json.loads((tmp_path / "stremio.json").read_text())
    assert registry["tt0000001"]["path"] == out.name
    assert not any((tmp_path / ".audio4films-work").glob("*.mkv"))  # work copy cleaned up


def test_run_pal_end_to_end(media, tmp_path):
    rc = main(["run", str(media["vo"]), str(media["dub_pal"]), "--out-dir", str(tmp_path), "--window", "15",
               "--search", "20"])
    assert rc == 0
    out = next(tmp_path.glob("*.spa+eng.mkv"))
    audio = probe(str(out)).of_kind("audio")
    assert audio[0].codec == "eac3"  # re-timed dub is re-encoded; the video is not
    assert verify(str(out), str(media["vo"])).passed


def test_run_refuses_cut_scene(media, tmp_path):
    rc = main(["run", str(media["vo"]), str(media["dub_cut"]), "--out-dir", str(tmp_path), "--window", "15",
               "--search", "20"])
    assert rc == 2
    assert not list(tmp_path.glob("*.mkv"))


def test_serve(tmp_path: Path):
    payload = bytes(range(256)) * 40
    (tmp_path / "movie.mkv").write_bytes(payload)
    register(tmp_path, "tt0000002", tmp_path / "movie.mkv", "Movie", "Español + English audio")
    server = make_server(tmp_path, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        manifest = json.load(urllib.request.urlopen(f"{base}/manifest.json"))
        assert manifest["resources"] == ["stream"]
        streams = json.load(urllib.request.urlopen(f"{base}/stream/movie/tt0000002.json"))["streams"]
        assert len(streams) == 1 and streams[0]["url"].endswith("/files/movie.mkv")
        assert json.load(urllib.request.urlopen(f"{base}/stream/movie/tt9999999.json"))["streams"] == []
        req = urllib.request.Request(streams[0]["url"], headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 206
            assert resp.headers["Content-Range"] == f"bytes 10-19/{len(payload)}"
            assert resp.read() == payload[10:20]
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"{base}/files/..%2F..%2Fetc%2Fpasswd")
        assert err.value.code == 404
    finally:
        server.shutdown()
