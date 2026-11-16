"""Record clips by driving the CS Demo Manager CLI (CS2 + HLAE + FFmpeg, Windows only)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import VideoConfig
from .highlights import Clip


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: don't flash console windows from the app.


class RenderError(RuntimeError):
    pass


def find_csdm(configured: str = "") -> str:
    if configured:
        if Path(configured).exists() or shutil.which(configured):
            return configured
        raise RenderError(f"video.csdm_path {configured!r} does not exist")
    candidates = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", "")
        for folder in ("CS Demo Manager", "cs-demo-manager"):
            if local:
                candidates.append(Path(local) / "Programs" / folder / "csdm.cmd")
            if program_files:
                candidates.append(Path(program_files) / folder / "csdm.cmd")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    found = shutil.which("csdm")
    if found:
        return found
    raise RenderError(
        "CS Demo Manager CLI not found. Install CS Demo Manager (https://cs-demo-manager.com) "
        "or set video.csdm_path to its csdm.cmd"
    )


def find_ffmpeg(configured: str = "") -> str | None:
    if configured:
        return configured
    # Reuse the FFmpeg that CS Demo Manager downloads for itself.
    csdm_ffmpeg = Path.home() / ".csdm" / "ffmpeg"
    for candidate in (csdm_ffmpeg / "bin" / "ffmpeg.exe", csdm_ffmpeg / "bin" / "ffmpeg", csdm_ffmpeg / "ffmpeg"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("ffmpeg")


def csdm_safe_json(config: dict) -> str:
    # CSDM strips "#..." and "//..." comments from config files before parsing them, which would cut a
    # player name containing "#" or a path containing "//". JSON escapes keep both intact.
    return json.dumps(config, indent=2, ensure_ascii=True).replace("#", "\\u0023").replace("/", "\\/")


class Recorder:
    def __init__(self, cfg: VideoConfig):
        if sys.platform == "darwin":
            raise RenderError(
                "CS2 does not run on macOS, so videos can't be recorded here. Run this on your Windows "
                "PC, or set [video] render = false to only detect highlights."
            )
        self.cfg = cfg
        self.csdm = find_csdm(cfg.csdm_path)

    def _run(self, *args: str) -> None:
        print(f"    $ csdm {' '.join(args)}", flush=True)
        # Relay CSDM's progress through print so it also shows up in the app window.
        with subprocess.Popen([self.csdm, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", creationflags=NO_WINDOW) as proc:
            for line in proc.stdout:
                print(f"      {line.rstrip()}", flush=True)
        if proc.returncode != 0:
            raise RenderError(f"csdm {args[0]} failed with exit code {proc.returncode}")

    def analyze(self, demo: Path, source: str | None) -> None:
        # The video command needs the demo in CSDM's database (for player slots and tickrate).
        self._run("analyze", str(demo), *(["--source", source] if source else []))

    def record(
        self,
        demo: Path,
        clips: list[Clip],
        out_dir: Path,
        steam_id: str,
        player_name: str,
        *,
        only_my_killfeed: bool,
        concatenate_as: str | None = None,
    ) -> list[Path]:
        """Record every clip in one CS2 session. Returns one mp4 per clip, or one file if concatenating."""
        out_dir.mkdir(parents=True, exist_ok=True)
        name = player_name.replace('"', "")  # Quotes break CSDM's mirv_replace_name command.
        sequences = [self._sequence(i, clip, steam_id, name, only_my_killfeed) for i, clip in enumerate(clips, 1)]
        concatenate = bool(concatenate_as) and len(clips) > 1
        config = {
            "demoPath": str(demo.resolve()),
            "outputFolderPath": str(out_dir.resolve()),
            "recordingSystem": "HLAE",  # HLAE pipes frames straight into FFmpeg, no huge raw files.
            "recordingOutput": "video",
            "encoderSoftware": "FFmpeg",
            "framerate": self.cfg.framerate,
            "width": self.cfg.width,
            "height": self.cfg.height,
            "closeGameAfterRecording": True,
            "concatenateSequences": concatenate,
            "outputFileName": concatenate_as or "",
            "trueView": self.cfg.true_view,
            "ffmpegSettings": {
                "customLocationEnabled": False,
                "customExecutableLocation": "",
                "audioBitrate": 192,
                "constantRateFactor": self.cfg.crf,
                "videoContainer": "mp4",
                "videoCodec": self.cfg.video_codec,
                "audioCodec": "aac",
                "inputParameters": "",
                "outputParameters": "",
            },
            "sequences": sequences,
        }
        config_path = out_dir / "csdm-video-config.json"
        config_path.write_text(csdm_safe_json(config), "utf-8")
        try:
            self._run("video", "--config-file", str(config_path))
        finally:
            config_path.unlink(missing_ok=True)

        if concatenate:
            return [_find_output(out_dir, f"{concatenate_as}.mp4")]
        return [
            _find_output(out_dir, f"sequence-{s['number']}-tick-{s['startTick']}-to-{s['endTick']}.mp4")
            for s in sequences
        ]

    def _sequence(self, number: int, clip: Clip, steam_id: str, name: str, only_my_killfeed: bool) -> dict:
        return {
            "number": number,
            "startTick": clip.start_tick,
            "endTick": clip.end_tick,
            "showXRay": False,
            "showAssists": True,
            "showOnlyDeathNotices": self.cfg.hide_hud,
            "deathNoticesDuration": 5,
            "recordAudio": self.cfg.record_audio,
            "playerVoicesEnabled": self.cfg.player_voices,
            "playersOptions": [
                {"playerName": name, "steamId": steam_id, "showKill": True, "highlightKill": True,
                 "isVoiceEnabled": self.cfg.player_voices}
            ] if only_my_killfeed else [],
            "playerCameras": [{"tick": t, "playerSteamId": steam_id, "playerName": name} for t in clip.camera_ticks],
            "cameras": [],
        }


def _find_output(out_dir: Path, file_name: str) -> Path:
    # Newer CSDM versions nest the output in a per-video folder, so search below out_dir too.
    direct = out_dir / file_name
    if direct.exists():
        return direct
    found = next(out_dir.rglob(file_name), None)
    if found is None:
        raise RenderError(f"CSDM finished but {file_name} is missing in {out_dir}")
    return found


def concat_videos(ffmpeg: str, files: list[Path], dest: Path) -> Path:
    list_file = dest.with_suffix(".txt")
    lines = []
    for f in files:
        escaped = f.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'\n")
    list_file.write_text("".join(lines), "utf-8")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy",
             str(dest)],
            check=True, creationflags=NO_WINDOW,
        )
    finally:
        list_file.unlink(missing_ok=True)
    return dest
