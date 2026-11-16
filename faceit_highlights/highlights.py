"""Find highlight moments and full-POV segments in a CS2 demo using demoparser2."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

TICKRATE = 64  # FACEIT and Valve CS2 demos are recorded at 64 ticks per second.
T, CT = 2, 3
WINNER_TEAM = {"T": T, "CT": CT}
POST_ROUND_SECONDS = 10  # Exit frags after round_end still belong to the round.
MIN_GAP_SECONDS = 5  # CSDM seeks between sequences, so clips closer than this are merged.
SPECIAL_KILL_TAGS = ("noscope", "wallbang", "smoke", "blind", "air")


@dataclass
class Kill:
    tick: int
    attacker: str
    victim: str
    weapon: str
    tags: list[str]


@dataclass
class Round:
    number: int  # The match's own round counter (total_rounds_played after the round).
    label: str  # "r07", or "knife" / "r07x" for rounds the match threw away.
    start_tick: int  # End of freeze time.
    end_tick: int  # round_end event.
    last_tick: int  # Last tick that still belongs to this round (exit frags).
    winner: int | None
    teams: dict[str, int]  # steam id -> team (T/CT) at the start of the round
    alive_at_start: set[str]
    kills: list[Kill]


@dataclass
class Clip:
    label: str
    start_tick: int
    end_tick: int
    camera_ticks: list[int]
    reasons: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return (self.end_tick - self.start_tick) / TICKRATE


@dataclass
class Highlight:
    label: str
    reasons: list[str]
    parts: list[Clip]  # Recorded separately and joined, so the quiet time between kills is cut out.

    @property
    def seconds(self) -> float:
        return sum(part.seconds for part in self.parts)


@dataclass
class HighlightRules:
    min_kills: int = 3
    clutches: bool = True
    min_clutch_enemies: int = 2
    special_kills: list[str] = field(default_factory=list)
    seconds_before: float = 3.0
    seconds_after: float = 2.0
    max_quiet_seconds: float = 8.0


@dataclass
class MatchAnalysis:
    map_name: str
    steam_id: str
    player_name: str
    rounds: list[Round]
    highlights: list[Highlight]
    pov: list[Clip]


def analyze_demo(demo_path: Path, steam_id: str, rules: HighlightRules, pov_mode: str) -> MatchAnalysis:
    from demoparser2 import DemoParser  # Imported lazily so the rest of the package works without it.

    parser = DemoParser(str(demo_path))
    rounds, names = load_rounds(parser)
    if not any(steam_id in r.teams for r in rounds):
        raise ValueError(f"Steam ID {steam_id} did not play in {demo_path.name}")
    return MatchAnalysis(
        map_name=parser.parse_header().get("map_name", "unknown"),
        steam_id=steam_id,
        player_name=names.get(steam_id, steam_id),
        rounds=rounds,
        highlights=find_highlights(rounds, steam_id, rules),
        pov=find_pov_segments(rounds, steam_id, pov_mode),
    )


def _rows(df) -> list[dict]:
    # demoparser2 returns an empty list instead of a DataFrame when an event never fires.
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")


def _steam_id(value) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ""


def _kill_tags(row: dict) -> list[str]:
    checks = {
        "hs": row.get("headshot"),
        "noscope": row.get("noscope"),
        "wallbang": row.get("penetrated"),
        "smoke": row.get("thrusmoke"),
        "blind": row.get("attackerblind"),
        "air": row.get("attackerinair"),
    }
    return [tag for tag, hit in checks.items() if hit]


def _is_knife(weapon: str) -> bool:
    return weapon.startswith("knife") or "bayonet" in weapon


def load_rounds(parser) -> tuple[list[Round], dict[str, str]]:
    freeze_ticks = sorted(
        int(row["tick"])
        for row in _rows(parser.parse_event("round_freeze_end", other=["is_warmup_period"]))
        if not row.get("is_warmup_period")
    )
    round_ends = [
        (int(row["tick"]), WINNER_TEAM.get(row.get("winner")), int(row.get("total_rounds_played") or 0))
        for row in _rows(parser.parse_event("round_end", other=["total_rounds_played"]))
    ]
    kills = sorted(
        (
            Kill(
                tick=int(row["tick"]),
                attacker=_steam_id(row.get("attacker_steamid")),
                victim=_steam_id(row.get("user_steamid")),
                weapon=str(row.get("weapon") or ""),
                tags=_kill_tags(row),
            )
            for row in _rows(parser.parse_event("player_death", other=["is_warmup_period"]))
            if not row.get("is_warmup_period")
        ),
        key=lambda k: k.tick,
    )

    teams_by_tick: dict[int, dict[str, int]] = {}
    alive_by_tick: dict[int, set[str]] = {}
    names: dict[str, str] = {}
    if freeze_ticks:
        for row in _rows(parser.parse_ticks(["team_num", "is_alive"], ticks=freeze_ticks)):
            sid = _steam_id(row.get("steamid"))
            tick = int(row["tick"])
            names.setdefault(sid, str(row.get("name") or sid))
            if row.get("team_num") in (T, CT):
                teams_by_tick.setdefault(tick, {})[sid] = int(row["team_num"])
                if row.get("is_alive"):
                    alive_by_tick.setdefault(tick, set()).add(sid)

    rounds: list[Round] = []
    for i, start in enumerate(freeze_ticks):
        next_start = freeze_ticks[i + 1] if i + 1 < len(freeze_ticks) else None
        end = next(
            (e for e in round_ends if e[0] > start and (next_start is None or e[0] < next_start)),
            None,
        )
        if end is None or end[1] is None:
            continue  # No real round_end (demo cut off, or a draw/restart).
        end_tick, winner, number = end
        last_tick = end_tick + POST_ROUND_SECONDS * TICKRATE
        if next_start is not None:
            last_tick = min(last_tick, next_start - 1)
        rounds.append(
            Round(
                number=number,
                label=f"r{number:02d}",
                start_tick=start,
                end_tick=end_tick,
                last_tick=last_tick,
                winner=winner,
                teams=teams_by_tick.get(start, {}),
                alive_at_start=alive_by_tick.get(start, set()),
                kills=[k for k in kills if start <= k.tick <= last_tick],
            )
        )

    # A round followed by one with the same or lower number didn't count: FACEIT's knife
    # round, a restart, or a backup restore.
    for current, following in zip(rounds, rounds[1:]):
        if following.number <= current.number:
            knife_only = current.kills and all(_is_knife(k.weapon) for k in current.kills)
            current.label = "knife" if knife_only else f"{current.label}x"
    return rounds, names


def find_clutch(r: Round, me: str) -> tuple[int, int] | None:
    """Return (tick the clutch started, enemies alive then) if `me` won a 1vX in this round."""
    my_team = r.teams.get(me)
    if my_team is None or me not in r.alive_at_start:
        return None
    alive = set(r.alive_at_start)
    for kill in r.kills:
        if kill.tick > r.end_tick:
            break
        alive.discard(kill.victim)
        if me not in alive:
            return None
        mates = [p for p in alive if r.teams.get(p) == my_team]
        enemies = [p for p in alive if r.teams.get(p) not in (None, my_team)]
        if mates == [me] and enemies:
            return (kill.tick, len(enemies)) if r.winner == my_team else None
    return None


def find_highlights(rounds: list[Round], me: str, rules: HighlightRules) -> list[Highlight]:
    before = round(rules.seconds_before * TICKRATE)
    after = round(rules.seconds_after * TICKRATE)
    quiet = round(max(rules.max_quiet_seconds, MIN_GAP_SECONDS) * TICKRATE)
    highlights = []
    for r in rounds:
        my_team = r.teams.get(me)
        if my_team is None:
            continue
        my_kills = [k for k in r.kills if k.attacker == me and k.victim != me and r.teams.get(k.victim) != my_team]

        reasons: list[str] = []
        moments: list[int] = []
        if len(my_kills) >= rules.min_kills:
            reasons.append("ace" if len(my_kills) >= 5 else f"{len(my_kills)}k")
            moments += [k.tick for k in my_kills]
        clutch = find_clutch(r, me) if rules.clutches else None
        if clutch and clutch[1] >= rules.min_clutch_enemies:
            reasons.append(f"1v{clutch[1]}")
            moments += [clutch[0], r.end_tick] + [k.tick for k in my_kills if k.tick >= clutch[0]]
        for kill in my_kills:
            special = [t for t in kill.tags if t in rules.special_kills]
            if special:
                reasons += [t for t in special if t not in reasons]
                moments.append(kill.tick)
        if not reasons:
            continue

        label = f"{r.label}_{'_'.join(reasons)}"
        windows = [
            Clip(label, max(1, t - before, r.start_tick - TICKRATE), min(t + after, r.last_tick), [])
            for t in sorted(set(moments))
        ]
        parts = merge_close_clips(windows, quiet)
        for n, part in enumerate(parts, 1):
            part.label = f"{label}_part{n}" if len(parts) > 1 else label
            part.camera_ticks = [part.start_tick]
        highlights.append(Highlight(label, reasons, parts))
    return highlights


def find_pov_segments(rounds: list[Round], me: str, mode: str) -> list[Clip]:
    played = [r for r in rounds if me in r.teams]
    if not played:
        return []
    if mode == "full":
        start = max(1, played[0].start_tick - 2 * TICKRATE)
        end = min(played[-1].end_tick + 5 * TICKRATE, played[-1].last_tick)
        # Re-lock the camera on the player every round, the game drifts to other players after a death.
        return [Clip("full_pov", start, end, [start] + [r.start_tick for r in played[1:]])]
    if mode != "alive":
        raise ValueError(f"Unknown full_pov mode {mode!r}, expected 'alive' or 'full'")

    clips = []
    for r in played:
        if me not in r.alive_at_start:
            continue
        death = next((k.tick for k in r.kills if k.victim == me), None)
        start = max(1, r.start_tick - TICKRATE)
        end = (death if death is not None else r.end_tick) + 3 * TICKRATE
        clips.append(Clip(f"{r.label}_pov", start, min(end, r.last_tick), [start]))
    return merge_close_clips(clips)


def merge_close_clips(clips: list[Clip], min_gap_ticks: int = MIN_GAP_SECONDS * TICKRATE) -> list[Clip]:
    merged: list[Clip] = []
    for clip in sorted(clips, key=lambda c: c.start_tick):
        previous = merged[-1] if merged else None
        if previous and clip.start_tick - previous.end_tick < min_gap_ticks:
            previous.end_tick = max(previous.end_tick, clip.end_tick)
            previous.camera_ticks += clip.camera_ticks
            previous.reasons += [r for r in clip.reasons if r not in previous.reasons]
            previous.label = f"{previous.label}+{clip.label}"
        else:
            merged.append(clip)
    return merged
