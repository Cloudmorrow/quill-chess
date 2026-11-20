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


def test_the_manifest_is_one_chess_board_on_the_games_shelf(q):
    (screen,) = q.manifest.screens
    assert screen["kit"] == "chess" and screen["model"] == GAME
    assert screen["play"] == "play" and screen["start"] == "start" and screen["computers"] == COMPUTER
    assert q.manifest.category == "games"


def test_a_game_between_two_people_is_theirs_and_nobody_elses(q):
    game = started(q, "sara", colour="white", clock="0")
    assert (game["white"], game["black"]) == (q.user, "sara")
    assert not game.get("clock")
    sara = q.as_user("sara")
    assert sara.get(GAME, game.id)["white"] == q.user
    assert q.as_user("ole").list(GAME) == []
    # The next game between the two is at the same table, from either side.
    again = started(sara, q.user, colour="black", clock="0")
    assert again["table"] == game["table"]


def test_a_move_is_kept_only_in_turn_and_by_the_rules(q):
    game = started(q, "sara", colour="white", clock="0")
    sara = q.as_user("sara")
    assert "move" in sara.act("play", game, move="e7e5").error
    assert "no move" in q.act("play", game, move="e2e5").error
    assert q.act("play", game, move="e4").ok
    assert sara.act("play", q.get(GAME, game.id), move="Nf6").ok
    assert q.act("play", q.get(GAME, game.id), move="g1f3").ok
    assert q.get(GAME, game.id)["moves"] == ["e2e4", "g8f6", "g1f3"]
    assert q.as_user("ole").act("play", q.get(GAME, game.id), move="e5").refused


def test_mate_ends_it(q):
    game = started(q, "sara", colour="white", clock="0")
    sara = q.as_user("sara")
    for i, move in enumerate(["f3", "e5", "g4", "Qh4"]):
        assert (q if i % 2 == 0 else sara).act("play", q.get(GAME, game.id), move=move).ok
    over = q.get(GAME, game.id)
    assert (over["result"], over["reason"]) == ("0-1", "checkmate")
    assert "over" in q.act("play", over, move="a3").error


def test_resigning_and_a_draw_by_agreement(q):
    game = started(q, "sara", colour="white", clock="0")
    sara = q.as_user("sara")
    assert q.act("play", game, move="draw").ok
    assert q.get(GAME, game.id)["draw"] == q.user
    assert sara.act("play", q.get(GAME, game.id), move="draw").ok
    assert (q.get(GAME, game.id)["result"], q.get(GAME, game.id)["reason"]) == ("1/2-1/2", "agreed")
    other = started(q, "sara", colour="black", clock="0")
    assert q.act("play", other, move="resign").ok
    assert q.get(GAME, other.id)["result"] == "1-0"


def test_the_clock_runs_once_both_have_moved_and_a_flag_falls(q):
    game = started(q, "sara", colour="white", clock="300")
    sara = q.as_user("sara")
    assert (game["clock"], game["white_left"], game["black_left"]) == (300, 300_000, 300_000)
    assert q.act("play", game, move="e4").ok
    assert not q.get(GAME, game.id).get("turn_at")
    assert sara.act("play", q.get(GAME, game.id), move="e5").ok
    running = q.get(GAME, game.id)
    assert running.get("turn_at")
    # White has been thinking for six minutes: the clock has run out.
    q.change(GAME, game.id, turn_at=(dt.datetime.now(dt.UTC) - dt.timedelta(minutes=6)).isoformat())
    assert "time left" not in sara.act("play", q.get(GAME, game.id), move="time").error
    over = q.get(GAME, game.id)
    assert (over["result"], over["reason"]) == ("0-1", "time")


def test_a_flag_that_has_not_fallen_is_not_claimed(q):
    game = started(q, "sara", colour="white", clock="600")
    sara = q.as_user("sara")
    q.act("play", game, move="e4")
    sara.act("play", q.get(GAME, game.id), move="e5")
    assert "time left" in sara.act("play", q.get(GAME, game.id), move="time").error


def test_morrow_answers_every_move(q):
    game = started(q, "Morrow", colour="white", clock="0")
    assert game["computer"] == "black" and game["black"] == "Morrow" and game["level"] == 1
    assert q.act("play", game, move="e4").ok
    moves = q.get(GAME, game.id)["moves"]
    assert len(moves) == 2 and Game(moves)  # his answer is a legal move
    # Nobody else is at his table with you.
    assert q.as_user("sara").list(GAME) == []


def test_morrow_opens_when_he_has_white(q):
    game = started(q, "morrow", colour="black", clock="600")
    assert game["computer"] == "white" and len(game["moves"]) == 1


def test_beating_morrow_puts_him_up_a_level_and_losing_does_not(q):
    game = started(q, "Morrow", colour="white", clock="0")
    # A position a move from mate, for him to lose from.
    q.change(GAME, game.id, moves=["e2e4", "e7e5", "f1c4", "a7a6", "d1h5", "a6a5"])
    assert q.act("play", q.get(GAME, game.id), move="Qxf7").ok
    assert q.get(GAME, game.id)["result"] == "1-0"
    (him,) = q.list(COMPUTER)
    assert (him["level"], him["won"]) == (2, 1)
    lost = started(q, "Morrow", colour="white", clock="0")
    assert lost["level"] == 2
    q.act("play", lost, move="resign")
    (him,) = q.list(COMPUTER)
    assert (him["level"], him["lost"]) == (2, 1)


def test_morrow_never_goes_past_his_top_level(q):
    (him,) = [c for c in q.list(COMPUTER)] or [q.seed(COMPUTER, name="Morrow", level=1)]
    q.change(COMPUTER, him.id, level=morrow.TOP)
    game = started(q, "Morrow", colour="white", clock="0")
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
