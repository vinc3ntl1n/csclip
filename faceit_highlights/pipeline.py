"""Find new FACEIT matches, get their demos, detect highlights and record the videos."""

from __future__ import annotations

import json
import shutil
import time
import webbrowser
from datetime import datetime
from functools import partial
from pathlib import Path

from .config import Config, ConfigError
from .demos import MATCH_ID_RE, download, inbox_demos, unpack
from .faceit import FaceitClient, room_url, steam_id_of
from .highlights import MatchAnalysis, analyze_demo
from .render import Recorder, concat_videos, find_ffmpeg
from .state import DETECTED, DONE, FAILED, MAX_ATTEMPTS, WAITING, State

log = partial(print, flush=True)
INBOX_CHECK_SECONDS = 2


class NotInDemo(ValueError):
    pass


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.demos_dir = cfg.paths.work_dir / "demos"
        self.state = State(cfg.paths.work_dir / "state.json")
        self.faceit = FaceitClient(cfg.faceit.api_key, cfg.faceit.download_token) if cfg.faceit.api_key else None
        self._steam_id = cfg.faceit.steam_id or None
        self._recorder: Recorder | None = None

    @property
    def recorder(self) -> Recorder:
        if self._recorder is None:
            self._recorder = Recorder(self.cfg.video)
        return self._recorder

    def steam_id(self) -> str:
        if not self._steam_id:
            if not self.faceit:
                raise ConfigError("Set faceit.steam_id, or faceit.api_key + nickname, so I know whose POV to clip")
            self._steam_id = steam_id_of(self.faceit.player(self.cfg.faceit.nickname))
        return self._steam_id

    # Polling ---------------------------------------------------------------------------------------

    def poll(self, wait_for_downloads_s: float = 0, reopen_waiting: bool = False) -> None:
        """Get the demos of all new matches, then record them one by one.

        wait_for_downloads_s: without a Downloads API token, keep watching the inbox this long for the demos
        of the match pages that were just opened (the app's button does this while you click "Watch demo").
        reopen_waiting: open the match page again for matches that are still waiting for their demo.
        """
        if self.cfg.video.render:
            self.recorder  # Fail on setup problems (no CSDM, macOS) before any match burns a retry.
        inbox = inbox_demos(self.cfg.paths.inbox_dir) if self.cfg.paths.inbox_dir else {}
        todo: list[tuple[str, dict]] = []  # (key, keyword arguments for process)

        if self.faceit:
            player = self.faceit.player(self.cfg.faceit.nickname)
            self._steam_id = self._steam_id or steam_id_of(player)
            matches = self.faceit.recent_matches(player["player_id"], self.cfg.faceit.max_matches)
            new = [m for m in matches
                   if str(m.get("status", "")).lower() == "finished" and self.state.should_process(m["match_id"])]
            log(f"Checked FACEIT: {len(new)} new of your last {len(matches)} match(es)")
            waiting: dict[str, dict] = {}
            for item in new:
                match_id = item["match_id"]
                job = {"finished_at": item.get("finished_at"), "source": "faceit"}
                demo = self._safely(match_id, lambda: self._get_demo(match_id, inbox.get(match_id), reopen_waiting))
                if demo:
                    todo.append((match_id, {"demo": demo, **job}))
                elif self.state.get(match_id).get("status") == WAITING:
                    waiting[match_id] = job
            if waiting and wait_for_downloads_s > 0:
                todo += self._wait_for_inbox(waiting, wait_for_downloads_s)
                inbox = inbox_demos(self.cfg.paths.inbox_dir)
            inbox = {k: v for k, v in inbox.items() if k not in {m["match_id"] for m in matches}}

        for key, path in inbox.items():
            if self.state.should_process(key):
                demo = self._safely(key, lambda: unpack(path, self.demos_dir / f"{key}.dem"))
                if demo:
                    todo.append((key, {"demo": demo}))

        if not todo:
            log("Nothing new to record.")
        for key, kwargs in todo:
            self._safely(key, lambda: self.process(key, steam_id=self.steam_id(), **kwargs))

    def _get_demo(self, match_id: str, inbox_file: Path | None, reopen: bool = False) -> Path | None:
        dest = self.demos_dir / f"{match_id}.dem"
        if dest.exists():
            return dest
        if inbox_file:
            log(f"  {match_id}: unpacking {inbox_file.name}")
            return unpack(inbox_file, dest)

        compressed = self.cfg.paths.work_dir / f"{match_id}.download"
        if self.faceit.download_token:
            urls = self.faceit.match(match_id).get("demo_url") or []
            if not urls:
                log(f"  {match_id}: FACEIT has no demo for this match yet")
                return None
            log(f"  {match_id}: downloading demo")
            download(self.faceit.signed_demo_url(urls[0]), compressed)
        else:
            # Downloaded by you in your own browser ("Watch demo") and picked up from the inbox folder.
            if reopen or self.state.get(match_id).get("status") != WAITING:
                url = room_url(match_id)
                log(f"  {match_id}: click 'Watch demo' on {url}")
                if self.cfg.faceit.open_room_in_browser:
                    webbrowser.open(url)
                self.state.update(match_id, status=WAITING)
            return None
        try:
            return unpack(compressed, dest)
        finally:
            compressed.unlink(missing_ok=True)

    def _wait_for_inbox(self, waiting: dict[str, dict], timeout_s: float) -> list[tuple[str, dict]]:
        log(f"Waiting for {len(waiting)} demo download(s) to land in {self.cfg.paths.inbox_dir} ...")
        deadline = time.monotonic() + timeout_s
        sizes: dict[str, int] = {}
        found = []
        while waiting and time.monotonic() < deadline:
            time.sleep(INBOX_CHECK_SECONDS)
            for key, path in inbox_demos(self.cfg.paths.inbox_dir).items():
                if key not in waiting:
                    continue
                size = path.stat().st_size
                if sizes.get(key) == size:  # Unchanged since the last check, so the download has finished.
                    job = waiting.pop(key)
                    demo = self._safely(key, lambda: self._get_demo(key, path))
                    if demo:
                        found.append((key, {"demo": demo, **job}))
                sizes[key] = size
        if waiting:
            log(f"  Stopped waiting for {len(waiting)} demo(s); they'll be picked up next time")
        return found

    def _safely(self, key: str, work):
        """Run one match's step; a failure is recorded for that match instead of stopping everything."""
        try:
            return work()
        except ConfigError:
            raise  # A setup problem (e.g. not logged in), not this match's fault: don't burn its retries.
        except Exception as error:
            attempts = MAX_ATTEMPTS if isinstance(error, NotInDemo) else self.state.get(key).get("attempts", 0) + 1
            self.state.update(key, status=FAILED, attempts=attempts, error=str(error))
            log(f"  [x] {key}: {error}")
            return None

    # Processing one demo ---------------------------------------------------------------------------

    def process(self, key: str, demo: Path, steam_id: str, finished_at: int | None = None,
                source: str | None = None) -> Path:
        """Find the highlights in one demo and record its videos."""
        log(f"> {key}: finding highlights in {demo.name}")
        try:
            analysis = analyze_demo(demo, steam_id, self.cfg.highlights, self.cfg.full_pov.mode)
        except ValueError as error:
            raise NotInDemo(str(error)) from error

        when = datetime.fromtimestamp(finished_at or demo.stat().st_mtime)
        short_id = key[2:10] if MATCH_ID_RE.fullmatch(key) else key[:24]
        out = self.cfg.paths.output_dir / f"{when:%Y-%m-%d_%H%M}_{analysis.map_name}_{short_id}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "highlights.json").write_text(json.dumps(self._summary(key, analysis), indent=2), "utf-8")
        self._print_summary(analysis)

        if not self.cfg.video.render:
            self.state.update(key, status=DETECTED, output=str(out))
            log(f"  Rendering is off, wrote {out / 'highlights.json'}")
            return out

        videos = self._record(demo, analysis, out, source)
        if not self.cfg.paths.keep_demos:
            demo.unlink(missing_ok=True)
        self.state.update(key, status=DONE, output=str(out), videos=[v.name for v in videos], error=None)
        for video in videos:
            log(f"  [ok] {video} ({video.stat().st_size / 1e6:.0f} MB)")
        return out

    def _record(self, demo: Path, a: MatchAnalysis, out: Path, source: str | None) -> list[Path]:
        hl, recorder, raw = self.cfg.highlights, self.recorder, out / "raw"
        recorder.analyze(demo, source)
        videos: list[Path] = []

        if hl.enabled and a.highlights:
            log(f"  Recording {len(a.highlights)} highlight(s), CS2 will open and close by itself")
            parts = [part for h in a.highlights for part in h.parts]
            files = iter(recorder.record(demo, parts, raw, a.steam_id, a.player_name,
                                         only_my_killfeed=hl.only_my_killfeed))
            ffmpeg = find_ffmpeg(self.cfg.video.ffmpeg_path)  # CSDM has installed its FFmpeg by now.
            clips = []
            for i, highlight in enumerate(a.highlights, 1):
                part_files = [next(files) for _ in highlight.parts]
                target = out / f"{i:02d}_{highlight.label}.mp4"
                if len(part_files) == 1:
                    clips.append(part_files[0].replace(target))
                elif ffmpeg:
                    clips.append(concat_videos(ffmpeg, part_files, target))
                else:
                    clips += [f.replace(out / f"{i:02d}_{highlight.label}_part{n}.mp4")
                              for n, f in enumerate(part_files, 1)]
            videos += clips
            if hl.montage and len(clips) > 1:
                if ffmpeg:
                    videos.append(concat_videos(ffmpeg, clips, out / "montage.mp4"))
                else:
                    log("  FFmpeg not found, skipping the montage (set video.ffmpeg_path)")

        if self.cfg.full_pov.enabled and a.pov:
            minutes = sum(c.seconds for c in a.pov) / 60
            log(f"  Recording full POV ({minutes:.0f} min of gameplay, takes about as long to record)")
            [file] = recorder.record(demo, a.pov, raw, a.steam_id, a.player_name,
                                     only_my_killfeed=False, concatenate_as="full_pov")
            videos.append(file.replace(out / "full_pov.mp4"))

        shutil.rmtree(raw, ignore_errors=True)
        return videos

    def _summary(self, key: str, a: MatchAnalysis) -> dict:
        return {
            "match_id": key,
            "faceit_room": room_url(key) if MATCH_ID_RE.fullmatch(key) else None,
            "map": a.map_name,
            "player": a.player_name,
            "steam_id": a.steam_id,
            "highlights": [
                {
                    "file": f"{i:02d}_{h.label}.mp4",
                    "reasons": h.reasons,
                    "seconds": round(h.seconds, 1),
                    "parts": [{"start_tick": p.start_tick, "end_tick": p.end_tick} for p in h.parts],
                    "jump_to_it_in_cs2": f"demo_gototick {h.parts[0].start_tick}",
                }
                for i, h in enumerate(a.highlights, 1)
            ],
            "full_pov": {
                "mode": self.cfg.full_pov.mode,
                "segments": len(a.pov),
                "minutes": round(sum(c.seconds for c in a.pov) / 60, 1),
            },
        }

    def _print_summary(self, a: MatchAnalysis) -> None:
        log(f"  {a.player_name} on {a.map_name}: {len(a.highlights)} highlight(s)")
        for h in a.highlights:
            cuts = f", {len(h.parts)} parts" if len(h.parts) > 1 else ""
            log(f"    - {h.label} ({h.seconds:.0f}s{cuts})")
        if self.cfg.full_pov.enabled:
            log(f"  Full POV ({self.cfg.full_pov.mode}): {sum(c.seconds for c in a.pov) / 60:.0f} min")
