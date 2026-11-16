import gzip

import pytest

from faceit_highlights import pipeline as pipeline_module
from faceit_highlights.config import Config
from faceit_highlights.demos import DEMO_MAGIC
from faceit_highlights.highlights import MatchAnalysis
from faceit_highlights.pipeline import Pipeline

MATCH_ID = "1-6de46e85-0b21-420d-8c3b-df2737fc0067"
DEMO_BYTES = DEMO_MAGIC + b"\x00" * 100


class FakeFaceit:
    def __init__(self, download_token=""):
        self.download_token = download_token

    def player(self, nickname):
        return {"player_id": "p1", "nickname": nickname, "games": {"cs2": {"game_player_id": "765"}}}

    def recent_matches(self, player_id, limit):
        return [{"match_id": MATCH_ID, "status": "FINISHED", "finished_at": 1_700_000_000},
                {"match_id": "1-ongoing", "status": "ONGOING"}]

    def match(self, match_id):
        return {"demo_url": [f"https://demos.faceit-cdn.net/cs2/{match_id}-1-1.dem.zst"]}

    def signed_demo_url(self, resource_url):
        return resource_url + "?signature=abc"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    analysed = []

    def fake_analyze(demo, steam_id, rules, mode):
        analysed.append((demo.read_bytes(), steam_id))
        return MatchAnalysis("de_mirage", steam_id, "me", [], [], [])

    monkeypatch.setattr(pipeline_module, "analyze_demo", fake_analyze)
    monkeypatch.setattr(pipeline_module.webbrowser, "open", lambda url: pytest.fail("browser opened"))
    cfg = Config()
    cfg.faceit.api_key, cfg.faceit.nickname, cfg.faceit.open_room_in_browser = "key", "me", False
    cfg.paths.work_dir, cfg.paths.output_dir, cfg.paths.inbox_dir = tmp_path / "data", tmp_path / "out", tmp_path / "inbox"
    cfg.paths.inbox_dir.mkdir()
    cfg.video.render = False
    return cfg, analysed


def test_without_download_token_waits_for_the_browser_download(setup):
    cfg, analysed = setup
    p = Pipeline(cfg)
    p.faceit = FakeFaceit()

    p.poll()
    assert p.state.get(MATCH_ID)["status"] == "waiting_for_demo"
    assert analysed == []

    (cfg.paths.inbox_dir / f"{MATCH_ID}-1-1.dem.gz").write_bytes(gzip.compress(DEMO_BYTES))
    p.poll()
    assert p.state.get(MATCH_ID)["status"] == "detected"
    assert analysed == [(DEMO_BYTES, "765")]

    p.poll()  # Nothing new.
    assert len(analysed) == 1
    assert "1-ongoing" not in p.state.data


def test_with_download_token_downloads_by_itself(setup, monkeypatch):
    cfg, analysed = setup
    downloaded = []

    def fake_download(url, dest):
        downloaded.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(gzip.compress(DEMO_BYTES))
        return dest

    monkeypatch.setattr(pipeline_module, "download", fake_download)
    p = Pipeline(cfg)
    p.faceit = FakeFaceit(download_token="token")
    p.poll()
    assert downloaded == [f"https://demos.faceit-cdn.net/cs2/{MATCH_ID}-1-1.dem.zst?signature=abc"]
    assert p.state.get(MATCH_ID)["status"] == "detected"
    assert analysed == [(DEMO_BYTES, "765")]
    assert not list(cfg.paths.work_dir.glob("*.download"))  # compressed copy cleaned up


def test_button_flow_waits_for_the_download_you_click(setup, monkeypatch):
    import threading

    cfg, analysed = setup
    opened = []

    def you_click_watch_demo(url):
        opened.append(url)
        def save():
            part = cfg.paths.inbox_dir / f"{MATCH_ID}-1-1.dem.gz.crdownload"
            part.write_bytes(gzip.compress(DEMO_BYTES))
            part.rename(part.with_suffix(""))  # Browsers rename the file once it's complete.
        threading.Timer(0.2, save).start()

    cfg.faceit.open_room_in_browser = True
    monkeypatch.setattr(pipeline_module.webbrowser, "open", you_click_watch_demo)
    monkeypatch.setattr(pipeline_module, "INBOX_CHECK_SECONDS", 0.1)
    p = Pipeline(cfg)
    p.faceit = FakeFaceit()
    p.poll(wait_for_downloads_s=5, reopen_waiting=True)
    assert opened == [f"https://www.faceit.com/en/cs2/room/{MATCH_ID}"]
    assert p.state.get(MATCH_ID)["status"] == "detected"
    assert analysed == [(DEMO_BYTES, "765")]
