"""Chess's tests: the real record store, its spaces and gate, on this machine.

What they hold: a game is between its two players and nobody else, a move
is kept only when the rules allow it and it is that player's turn, the
clock is kept, the game ends as chess ends, and Morrow answers — and goes
up a level when he is beaten, and only then.
"""

import datetime as dt
import random

import pytest

import morrow
from cloudmorrow.quill.chess import Game
from cloudmorrow.quill.testing import Harness

GAME = "chess.game"
COMPUTER = "chess.computer"
TABLE = "chess.table"


@pytest.fixture()
def q():
    with Harness(".") as harness:
        harness.as_user("sara")  # somebody else on the cloud to play
        yield harness


def started(q, opponent, **fields):
    result = q.act("start", opponent=opponent, **fields)
    assert result.ok, result
    (effect,) = [e for e in result.effects if e["effect"] == "go"]
    assert effect["screen"] == "games"
    return q.get(GAME, effect["params"]["game"])


def sides(q, game, other="sara"):
    """The two players of a game between people, white first."""
    them = q.as_user(other)
    return (q, them) if game["white"] == q.user else (them, q)


def answered(q, game):
    """Morrow's move, seen at once: his time over it is the screens' to wait out, not a test's."""
    q.change(GAME, game.id, answer_at=None)
    return q.get(GAME, game.id)


def as_white_against_morrow(q, **fields):
    """A game against Morrow with the person on white: the colours take turns, so at most two tries."""
    for _ in range(2):
        game = started(q, "Morrow", clock="0", **fields)
        if game["computer"] == "black":
            return game
        q.act("play", answered(q, game), move="resign")
    raise AssertionError("the colours did not take turns")


def test_the_manifest_is_one_chess_board_on_the_games_shelf(q):
    (screen,) = q.manifest.screens
    assert screen["kit"] == "chess" and screen["model"] == GAME
    assert screen["play"] == "play" and screen["start"] == "start" and screen["computers"] == COMPUTER
    assert q.manifest.category == "games"


def test_a_game_between_two_people_is_theirs_and_nobody_elses(q):
    game = started(q, "sara", clock="0")
    assert {game["white"], game["black"]} == {q.user, "sara"}
    assert not game.get("clock")
    sara = q.as_user("sara")
    assert sara.get(GAME, game.id)["white"] == game["white"]
    assert q.as_user("ole").list(GAME) == []
    # The next game between the two is at the same table, from either side.
    again = started(sara, q.user, clock="0")
    assert again["table"] == game["table"]


def test_the_colours_take_turns_whoever_starts_the_game(q):
    sara = q.as_user("sara")
    first = started(q, "sara", clock="0")
    second = started(sara, q.user, clock="0")
    third = started(q, "sara", clock="0")
    assert second["white"] == first["black"] and third["white"] == second["black"]
    # Against Morrow too: the other colour from the last game with him.
    one = started(q, "Morrow", clock="0")
    two = started(q, "Morrow", clock="0")
    assert one["computer"] != two["computer"]
    # And nobody picks: the action has no colour to give.
    assert "colour" not in {f["name"] if isinstance(f, dict) else f for f in q.manifest.actions[0]["fields"]}


def test_a_move_is_kept_only_in_turn_and_by_the_rules(q):
    game = started(q, "sara", clock="0")
    white, black = sides(q, game)
    assert "move" in black.act("play", game, move="e7e5").error
    assert "no move" in white.act("play", game, move="e2e5").error
    assert white.act("play", game, move="e4").ok
    assert black.act("play", q.get(GAME, game.id), move="Nf6").ok
    assert white.act("play", q.get(GAME, game.id), move="g1f3").ok
    assert q.get(GAME, game.id)["moves"] == ["e2e4", "g8f6", "g1f3"]
    assert q.as_user("ole").act("play", q.get(GAME, game.id), move="e5").refused


def test_mate_ends_it(q):
    game = started(q, "sara", clock="0")
    white, black = sides(q, game)
    for i, move in enumerate(["f3", "e5", "g4", "Qh4"]):
        assert (white if i % 2 == 0 else black).act("play", q.get(GAME, game.id), move=move).ok
    over = q.get(GAME, game.id)
    assert (over["result"], over["reason"]) == ("0-1", "checkmate")
    assert "over" in white.act("play", over, move="a3").error


def test_resigning_and_a_draw_by_agreement(q):
    game = started(q, "sara", clock="0")
    sara = q.as_user("sara")
    assert q.act("play", game, move="draw").ok
    assert q.get(GAME, game.id)["draw"] == q.user
    assert sara.act("play", q.get(GAME, game.id), move="draw").ok
    assert (q.get(GAME, game.id)["result"], q.get(GAME, game.id)["reason"]) == ("1/2-1/2", "agreed")
    other = started(q, "sara", clock="0")
    assert q.act("play", other, move="resign").ok
    assert q.get(GAME, other.id)["result"] == ("0-1" if other["white"] == q.user else "1-0")


def test_the_clock_runs_once_both_have_moved_and_a_flag_falls(q):
    game = started(q, "sara", clock="300")
    white, black = sides(q, game)
    assert (game["clock"], game["white_left"], game["black_left"]) == (300, 300_000, 300_000)
    assert white.act("play", game, move="e4").ok
    assert not q.get(GAME, game.id).get("turn_at")
    assert black.act("play", q.get(GAME, game.id), move="e5").ok
    running = q.get(GAME, game.id)
    assert running.get("turn_at")
    # White has been thinking for six minutes: the clock has run out.
    q.change(GAME, game.id, turn_at=(dt.datetime.now(dt.UTC) - dt.timedelta(minutes=6)).isoformat())
    assert "time left" not in black.act("play", q.get(GAME, game.id), move="time").error
    over = q.get(GAME, game.id)
    assert (over["result"], over["reason"]) == ("0-1", "time")


def test_a_flag_that_has_not_fallen_is_not_claimed(q):
    game = started(q, "sara", clock="600")
    white, black = sides(q, game)
    white.act("play", game, move="e4")
    black.act("play", q.get(GAME, game.id), move="e5")
    assert "time left" in black.act("play", q.get(GAME, game.id), move="time").error


def test_morrow_answers_every_move_and_takes_his_time_over_it(q):
    game = as_white_against_morrow(q)
    assert game["black"] == "Morrow" and game["level"] == 1
    assert q.act("play", game, move="e4").ok
    after = q.get(GAME, game.id)
    assert len(after["moves"]) == 2 and Game(after["moves"])  # his answer is a legal move
    # Kept back a while, as a person would think: nobody moves before it is seen.
    assert after["answer_at"] and after["answer_at"] > dt.datetime.now(dt.UTC).isoformat()[:19]
    assert "thinking" in q.act("play", after, move="d4").error
    assert q.act("play", answered(q, after), move="d4").ok
    # Nobody else is at his table with you.
    assert q.as_user("sara").list(GAME) == []


def test_morrow_opens_when_he_has_white(q):
    for _ in range(2):
        game = started(q, "morrow", clock="600")
        if game["computer"] == "white":
            break
    assert game["computer"] == "white" and len(game["moves"]) == 1


def test_his_time_over_a_move_comes_off_his_clock():
    assert 3_000 <= morrow.pause(1, 10, rng=random.Random(1)) <= 6_500
    assert morrow.pause(10, 10, rng=random.Random(1)) <= 3_600
    assert morrow.pause(5, 0, 300, rng=random.Random(1)) < morrow.pause(5, 20, rng=random.Random(1))
    assert morrow.pause(1, 20, 600, left=10_000, rng=random.Random(1)) == 500  # a twentieth of what he has


def test_beating_morrow_puts_him_up_a_level_and_losing_does_not(q):
    game = as_white_against_morrow(q)
    # A position a move from mate, for him to lose from.
    q.change(GAME, game.id, moves=["e2e4", "e7e5", "f1c4", "a7a6", "d1h5", "a6a5"])
    assert q.act("play", q.get(GAME, game.id), move="Qxf7").ok
    assert q.get(GAME, game.id)["result"] == "1-0"
    (him,) = q.list(COMPUTER)
    assert (him["level"], him["won"]) == (2, 1)
    lost = started(q, "Morrow", clock="0")
    assert lost["level"] == 2
    before = q.list(COMPUTER)[0]["lost"]
    q.act("play", answered(q, lost), move="resign")
    (him,) = q.list(COMPUTER)
    assert (him["level"], him["lost"]) == (2, before + 1)


def test_morrow_never_goes_past_his_top_level(q):
    (him,) = [c for c in q.list(COMPUTER)] or [q.seed(COMPUTER, name="Morrow", level=1)]
    q.change(COMPUTER, him.id, level=morrow.TOP)
    game = as_white_against_morrow(q)
    q.change(GAME, game.id, moves=["e2e4", "e7e5", "f1c4", "a7a6", "d1h5", "a6a5"])
    q.act("play", q.get(GAME, game.id), move="Qxf7")
    assert q.list(COMPUTER)[0]["level"] == morrow.TOP


@pytest.mark.parametrize("level", range(1, morrow.TOP + 1))
def test_morrow_plays_a_legal_move_at_every_level(level):
    game = Game(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6"])
    move = morrow.choose(game, level, random.Random(level))
    assert move in game.legal()


def test_at_his_best_morrow_takes_a_queen_left_hanging_and_mates_when_he_can():
    hanging = Game(["e2e4", "d7d6", "d1g4"])  # white's queen, where the bishop takes it
    assert morrow.choose(hanging, morrow.TOP, random.Random(1)).uci == "c8g4"
    mate = Game(["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6"])
    assert morrow.choose(mate, morrow.TOP, random.Random(1)).uci == "h5f7"
