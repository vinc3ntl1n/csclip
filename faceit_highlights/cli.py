"""Command line: run / watch / process / check."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import Config, ConfigError, load_config
from .demos import demo_key, unpack
from .faceit import FaceitClient, FaceitError, steam_id_of
from .pipeline import Pipeline, log
from .render import RenderError, find_csdm, find_ffmpeg


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="faceit-highlights",
        description="Turn your FACEIT CS2 matches into highlight clips and full-POV videos.",
    )
    parser.add_argument("-c", "--config", type=Path, default=Path("config.toml"), help="default: config.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="check FACEIT (and the inbox folder) once and process new matches")
    commands.add_parser("watch", help="keep checking every poll_minutes")
    process = commands.add_parser("process", help="process one demo file (.dem, .dem.gz or .dem.zst)")
    process.add_argument("demo", type=Path)
    process.add_argument("--steam-id", help="whose highlights to clip (default: from config / FACEIT nickname)")
    process.add_argument("--no-render", action="store_true", help="only find highlights, don't record videos")
    commands.add_parser("check", help="verify API keys, CS Demo Manager and FFmpeg")
    commands.add_parser("app", help="open the window with the 'Get my new matches' button")
    args = parser.parse_args(argv)

    if args.command == "app":
        from .gui import main as app_main

        return app_main(args.config.resolve() if args.config.exists() else None)
    try:
        if args.command == "process" and not args.config.exists():
            cfg = Config()  # Lets you try a single demo without writing a config first.
        else:
            cfg = load_config(args.config, require_demo_source=args.command in ("run", "watch"))
        {"run": cmd_run, "watch": cmd_watch, "process": cmd_process, "check": cmd_check}[args.command](cfg, args)
    except (ConfigError, RenderError, FaceitError) as error:
        sys.exit(f"Error: {error}")
    except KeyboardInterrupt:
        sys.exit("Stopped.")


def cmd_run(cfg: Config, args) -> None:
    Pipeline(cfg).poll()


def cmd_watch(cfg: Config, args) -> None:
    pipeline = Pipeline(cfg)
    while True:
        try:
            pipeline.poll()
        except (ConfigError, RenderError):
            raise
        except Exception as error:  # Network hiccups etc. shouldn't kill the watcher.
            log(f"Check failed: {error}")
        log(f"Next check in {cfg.poll_minutes:g} min (Ctrl+C to stop)")
        time.sleep(cfg.poll_minutes * 60)


def cmd_process(cfg: Config, args) -> None:
    if args.no_render:
        cfg.video.render = False
    pipeline = Pipeline(cfg)
    if not args.demo.exists():
        raise ConfigError(f"Demo not found: {args.demo}")
    key = demo_key(args.demo)
    demo = args.demo if args.demo.suffix == ".dem" else unpack(args.demo, pipeline.demos_dir / f"{key}.dem")
    pipeline.process(key, demo, args.steam_id or pipeline.steam_id())


def cmd_check(cfg: Config, args) -> None:
    def line(ok: bool | None, text: str) -> None:
        log(f"  [{'ok' if ok else '--' if ok is None else 'x '}] {text}")

    log("FACEIT")
    if cfg.faceit.api_key:
        client = FaceitClient(cfg.faceit.api_key, cfg.faceit.download_token)
        try:
            player = client.player(cfg.faceit.nickname)
            elo = player.get("games", {}).get("cs2", {}).get("faceit_elo")
            line(True, f"Data API key works: {player.get('nickname')} (Steam ID {steam_id_of(player)}, elo {elo})")
            matches = client.recent_matches(player["player_id"], 1)
        except FaceitError as error:
            line(False, f"Data API: {error}")
            matches = []
        if cfg.faceit.download_token and matches:
            try:
                urls = client.match(matches[0]["match_id"]).get("demo_url") or []
                if urls:
                    client.signed_demo_url(urls[0])
                    line(True, "Downloads API token works, demos download automatically")
                else:
                    line(None, "Your last match has no demo to test the Downloads API token with")
            except FaceitError as error:
                line(False, f"Downloads API: {error}")
        elif not cfg.faceit.download_token:
            line(None, "No Downloads API token: demos you download in the browser are picked up from inbox_dir")
    else:
        line(None, "No API key: only demos in inbox_dir are processed (set faceit.steam_id)")

    if cfg.paths.inbox_dir:
        line(cfg.paths.inbox_dir.is_dir(), f"Inbox folder {cfg.paths.inbox_dir}")

    log("Rendering")
    if not cfg.video.render:
        line(None, "Rendering is off (video.render = false), only highlight lists are written")
    elif sys.platform == "darwin":
        line(False, "CS2 doesn't run on macOS: run this on Windows or set video.render = false")
    else:
        try:
            line(True, f"CS Demo Manager CLI: {find_csdm(cfg.video.csdm_path)}")
        except RenderError as error:
            line(False, str(error))
        ffmpeg = find_ffmpeg(cfg.video.ffmpeg_path)
        line(bool(ffmpeg) or None, f"FFmpeg for montages: {ffmpeg or 'not found yet (CSDM installs it on first video)'}")
