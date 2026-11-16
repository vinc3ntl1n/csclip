import os
from pathlib import Path

import pandas as pd
import pytest

from faceit_highlights.highlights import (
    CT,
    TICKRATE,
    HighlightRules,
    Kill,
    Round,
    T,
    analyze_demo,
    find_clutch,
    find_highlights,
    find_pov_segments,
    load_rounds,
)

ME, MATE1, MATE2, ENEMY1, ENEMY2, ENEMY3 = "1", "2", "3", "11", "12", "13"
TEAMS = {ME: CT, MATE1: CT, MATE2: CT, ENEMY1: T, ENEMY2: T, ENEMY3: T}


def s(seconds: float) -> int:
    """Tick `seconds` into a round starting at tick 10_000."""
    return 10_000 + round(seconds * TICKRATE)


def make_round(kills, winner=CT, end=90, label="r05") -> Round:
    return Round(
        number=5, label=label, start_tick=s(0), end_tick=s(end), last_tick=s(end + 10), winner=winner,
        teams=dict(TEAMS), alive_at_start=set(TEAMS),
        kills=[Kill(s(t), attacker, victim, weapon, list(tags)) for t, attacker, victim, weapon, *tags in kills],
    )


def test_three_kills_make_one_highlight():
    r = make_round([(10, ME, ENEMY1, "ak47"), (12, ME, ENEMY2, "ak47"), (14, ME, ENEMY3, "ak47")])
    [h] = find_highlights([r], ME, HighlightRules())
    assert h.label == "r05_3k"
    assert len(h.parts) == 1
    assert (h.parts[0].start_tick, h.parts[0].end_tick) == (s(10) - 3 * TICKRATE, s(14) + 2 * TICKRATE)


def test_two_kills_are_not_a_highlight_and_teamkills_dont_count():
    r = make_round([(10, ME, ENEMY1, "ak47"), (12, ME, MATE1, "ak47"), (14, ME, ENEMY2, "ak47")])
    assert find_highlights([r], ME, HighlightRules()) == []


def test_quiet_time_between_kills_is_cut_out():
    r = make_round([(10, ME, ENEMY1, "ak47"), (60, ME, ENEMY2, "ak47"), (63, ME, ENEMY3, "ak47")])
    [h] = find_highlights([r], ME, HighlightRules(max_quiet_seconds=8))
    assert [p.label for p in h.parts] == ["r05_3k_part1", "r05_3k_part2"]
    assert h.parts[0].end_tick == s(12) and h.parts[1].start_tick == s(57)
    assert all(p.camera_ticks == [p.start_tick] for p in h.parts)


def test_won_clutch_is_found_from_the_moment_you_are_alone():
    r = make_round([
        (5, ENEMY1, MATE1, "ak47"),
        (8, ENEMY1, MATE2, "ak47"),  # now 1v3
        (20, ME, ENEMY1, "m4a1"),
        (25, ME, ENEMY2, "m4a1"),
        (30, ME, ENEMY3, "m4a1"),
    ], end=30)
    assert find_clutch(r, ME) == (s(8), 3)
    [h] = find_highlights([r], ME, HighlightRules())
    assert h.reasons == ["3k", "1v3"]
    assert h.parts[0].start_tick == s(5)  # 3 s before the clutch started


def test_lost_clutch_is_not_a_highlight():
    r = make_round([(5, ENEMY1, MATE1, "ak47"), (8, ENEMY1, MATE2, "ak47"), (20, ENEMY2, ME, "ak47")], winner=T)
    assert find_clutch(r, ME) is None


def test_special_kills_only_when_enabled():
    r = make_round([(10, ME, ENEMY1, "awp", "noscope")])
    assert find_highlights([r], ME, HighlightRules()) == []
    [h] = find_highlights([r], ME, HighlightRules(special_kills=["noscope"]))
    assert h.label == "r05_noscope"


def test_pov_alive_mode_stops_after_death():
    r = make_round([(30, ENEMY1, ME, "ak47")])
    [seg] = find_pov_segments([r], ME, "alive")
    assert seg.start_tick == s(-1) and seg.end_tick == s(33)


def test_pov_full_mode_is_one_clip_relocking_camera_each_round():
    r1 = make_round([])
    r2 = make_round([], label="r06")
    for attr in ("start_tick", "end_tick", "last_tick"):
        setattr(r2, attr, getattr(r1, attr) + 100 * TICKRATE)
    [clip] = find_pov_segments([r1, r2], ME, "full")
    assert clip.camera_ticks == [clip.start_tick, r2.start_tick]


class FakeParser:
    """Mimics the demoparser2 calls load_rounds makes, including a warmup and a knife round."""

    def parse_event(self, name, other=None):
        events = {
            "round_freeze_end": [
                {"tick": 100, "is_warmup_period": False},  # knife round
                {"tick": 500, "is_warmup_period": True},  # warmup after the knife round
                {"tick": 1000, "is_warmup_period": False},  # real round 1
            ],
            "round_end": [
                {"tick": 1, "winner": None, "total_rounds_played": 0},
                {"tick": 300, "winner": "CT", "total_rounds_played": 1},
                {"tick": 2000, "winner": "T", "total_rounds_played": 1},
            ],
            "player_death": [
                {"tick": 200, "attacker_steamid": ME, "user_steamid": ENEMY1, "weapon": "knife_t", "is_warmup_period": False},
                {"tick": 600, "attacker_steamid": ME, "user_steamid": ENEMY2, "weapon": "ak47", "is_warmup_period": True},
                {"tick": 1500, "attacker_steamid": ENEMY1, "user_steamid": ME, "weapon": "ak47", "is_warmup_period": False},
            ],
        }
        return pd.DataFrame(events[name])

    def parse_ticks(self, props, ticks):
        return pd.DataFrame([
            {"tick": t, "steamid": int(sid), "name": f"p{sid}", "team_num": team, "is_alive": True}
            for t in ticks for sid, team in TEAMS.items()
        ])


def test_load_rounds_skips_warmup_and_labels_knife_round():
    rounds, names = load_rounds(FakeParser())
    assert [r.label for r in rounds] == ["knife", "r01"]
    assert [len(r.kills) for r in rounds] == [1, 1]  # the warmup kill is gone
    assert rounds[1].winner == T
    assert names[ME] == f"p{ME}"


@pytest.mark.skipif(not os.environ.get("CS2_TEST_DEMO"), reason="set CS2_TEST_DEMO=path/to/demo.dem and CS2_TEST_STEAMID")
def test_real_demo():
    analysis = analyze_demo(Path(os.environ["CS2_TEST_DEMO"]), os.environ["CS2_TEST_STEAMID"], HighlightRules(), "alive")
    assert analysis.rounds and analysis.pov
    for h in analysis.highlights:
        assert all(p.start_tick < p.end_tick for p in h.parts)
