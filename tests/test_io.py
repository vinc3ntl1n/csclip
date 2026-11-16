import gzip
import json
import re
from pathlib import Path

import pytest
import zstandard

from faceit_highlights.config import ConfigError, load_config
from faceit_highlights.demos import DEMO_MAGIC, demo_key, unpack
from faceit_highlights.render import csdm_safe_json

DEMO_BYTES = DEMO_MAGIC + b"\x00" * 1000
MATCH_ID = "1-6de46e85-0b21-420d-8c3b-df2737fc0067"


@pytest.mark.parametrize("compress", [
    lambda b: b,
    gzip.compress,
    lambda b: zstandard.ZstdCompressor().compress(b),
])
def test_unpack_handles_raw_gzip_and_zstd(tmp_path, compress):
    src = tmp_path / "in.dem.x"
    src.write_bytes(compress(DEMO_BYTES))
    assert unpack(src, tmp_path / "out.dem").read_bytes() == DEMO_BYTES


def test_unpack_rejects_non_demos(tmp_path):
    src = tmp_path / "not-a-demo.dem"
    src.write_bytes(b"<html>login required</html>")
    with pytest.raises(ValueError):
        unpack(src, tmp_path / "out.dem")
    assert not (tmp_path / "out.dem").exists()


def test_demo_key_prefers_the_faceit_match_id():
    assert demo_key(Path(f"{MATCH_ID}-1-1.dem.zst")) == MATCH_ID
    assert demo_key(Path("my-scrim.dem.gz")) == "my-scrim"


def test_csdm_config_survives_csdm_comment_stripping():
    config = {"demoPath": "C://demos/#1.dem", "sequences": [{"playerName": "x#y // z /* w */"}]}
    text = csdm_safe_json(config)
    # What CS Demo Manager does to the file before JSON.parse:
    stripped = re.sub(r"//.*|/\*[\s\S]*?\*/", "", re.sub(r"(#.*)", "", text)).strip()
    assert json.loads(stripped) == config


def test_example_config_loads(tmp_path):
    example = Path(__file__).parent.parent / "config.example.toml"
    config = tmp_path / "config.toml"
    config.write_text(example.read_text().replace('api_key = ""', 'api_key = "k"')
                      .replace('nickname = ""', 'nickname = "me"').replace('inbox_dir = "~/Downloads"', 'inbox_dir = "in"'))
    cfg = load_config(config)
    assert cfg.paths.inbox_dir == tmp_path / "in"
    assert cfg.highlights.min_kills == 3 and cfg.full_pov.mode == "alive"


def test_typos_in_config_are_reported(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[highlights]\nmin_kill = 3\n[paths]\ninbox_dir = "in"\n')
    with pytest.raises(ConfigError, match="min_kill"):
        load_config(config)
