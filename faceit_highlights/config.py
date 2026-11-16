"""Settings loaded from config.toml. API keys can also come from environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .highlights import SPECIAL_KILL_TAGS, HighlightRules


@dataclass
class FaceitConfig:
    api_key: str = ""
    download_token: str = ""
    nickname: str = ""
    steam_id: str = ""
    max_matches: int = 3
    open_room_in_browser: bool = True


@dataclass
class PathsConfig:
    work_dir: Path = Path("data")
    output_dir: Path = Path("videos")
    inbox_dir: Path | None = None
    keep_demos: bool = True


@dataclass
class HighlightsConfig(HighlightRules):
    enabled: bool = True
    only_my_killfeed: bool = True
    montage: bool = True


@dataclass
class FullPovConfig:
    enabled: bool = True
    mode: str = "alive"


@dataclass
class VideoConfig:
    render: bool = True
    csdm_path: str = ""
    ffmpeg_path: str = ""
    width: int = 1920
    height: int = 1080
    framerate: int = 60
    crf: int = 23
    video_codec: str = "libx264"
    record_audio: bool = True
    player_voices: bool = False
    true_view: bool = True
    hide_hud: bool = False


@dataclass
class Config:
    faceit: FaceitConfig = field(default_factory=FaceitConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    highlights: HighlightsConfig = field(default_factory=HighlightsConfig)
    full_pov: FullPovConfig = field(default_factory=FullPovConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    poll_minutes: float = 10


class ConfigError(ValueError):
    pass


def _section(cls, data: dict, name: str):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Unknown setting(s) in [{name}]: {', '.join(sorted(unknown))}")
    return cls(**data)


def load_config(path: Path, require_demo_source: bool = True) -> Config:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path} (copy config.example.toml to config.toml)")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent

    sections = {"faceit": FaceitConfig, "paths": PathsConfig, "highlights": HighlightsConfig,
                "full_pov": FullPovConfig, "video": VideoConfig}
    unknown = set(data) - set(sections) - {"poll_minutes"}
    if unknown:
        raise ConfigError(f"Unknown section(s): {', '.join(sorted(unknown))}")
    cfg = Config(
        **{name: _section(cls, data.get(name, {}), name) for name, cls in sections.items()},
        poll_minutes=float(data.get("poll_minutes", 10)),
    )

    cfg.faceit.api_key = os.environ.get("FACEIT_API_KEY", cfg.faceit.api_key).strip()
    cfg.faceit.download_token = os.environ.get("FACEIT_DOWNLOAD_TOKEN", cfg.faceit.download_token).strip()

    def resolve(p) -> Path:
        p = Path(p).expanduser()
        return p if p.is_absolute() else base / p

    cfg.paths.work_dir = resolve(cfg.paths.work_dir)
    cfg.paths.output_dir = resolve(cfg.paths.output_dir)
    cfg.paths.inbox_dir = resolve(cfg.paths.inbox_dir) if cfg.paths.inbox_dir else None

    bad = [t for t in cfg.highlights.special_kills if t not in SPECIAL_KILL_TAGS]
    if bad:
        raise ConfigError(f"Unknown special_kills {bad}, choose from {list(SPECIAL_KILL_TAGS)}")
    if cfg.full_pov.mode not in ("alive", "full"):
        raise ConfigError("full_pov.mode must be 'alive' or 'full'")
    if cfg.faceit.api_key and not cfg.faceit.nickname:
        raise ConfigError("Set faceit.nickname to the FACEIT account whose matches you want clipped")
    if require_demo_source:
        if not cfg.faceit.api_key and not cfg.paths.inbox_dir:
            raise ConfigError("Set faceit.api_key (or FACEIT_API_KEY), or paths.inbox_dir to process demos you download")
        if cfg.faceit.api_key and not cfg.faceit.download_token and not cfg.paths.inbox_dir:
            raise ConfigError(
                "Without a FACEIT Downloads API token, set paths.inbox_dir to the folder your browser downloads to"
            )
    return cfg
