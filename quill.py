"""Chess: the code behind the two buttons the chess kit presses.

`start` sets up a game — between the person and somebody else on the
cloud, at the table the two of them share, or against Morrow, at their own
table — and `play` is everything done in one: a move, checked by the
core's rules (`cloudmorrow.quill.chess`) before it is kept, the clock
brought up to date, the end of the game noticed, and, against Morrow, his
answer (morrow.py). Beating him puts him up a level.

The board, the clocks ticking and the pieces dragged are the kit's; what
is kept is the moves, what each side had left, and since when the side to
move has been thinking (docs/CHESS.md in the Cloudmorrow repository).
"""

from __future__ import annotations

import datetime as dt
import random

import morrow
from cloudmorrow.quill import Conflict, action, error, go, toast
from cloudmorrow.quill.chess import BLACK_WINS, DRAWN, WHITE_WINS, Game, lost_on_time, thinking, time_left

TABLE = "chess.table"
GAME = "chess.game"
COMPUTER = "chess.computer"
CLOCKS = {"0": 0, "600": 600, "300": 300}
WORDS = ("resign", "draw", "decline", "time")


def _now(ctx) -> dt.datetime:
    now = ctx.now()
    return now if now.tzinfo else now.replace(tzinfo=dt.UTC)


def _stamp(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def _morrow(ctx):
    """The person's Morrow: the one the dataset gave them, or a new one at level 1."""
    found = ctx.records.list(COMPUTER)
    for computer in found:
        if computer.get("name") == "Morrow":
            return computer
    if found:
        return found[0]
    return ctx.records.create(COMPUTER, name="Morrow", level=1)


def _table_with(ctx, other: str | None):
    """The table between the person and *other*, from either side — or their own, against the computer."""
    me = ctx.user.username
    for table in ctx.records.list(TABLE):
        if other is None:
            if table.get("kind") == "computer" and table.owner == me:
                return table
            continue
        if table.get("kind") != "people":
            continue
        if {table.owner, *table.members} == {me, other}:
            return table
    if other is None:
        return ctx.records.create(TABLE, name=ctx.user.name or me, kind="computer", scope="personal")
    return ctx.records.create(TABLE, name=f"{me} and {other}", kind="people", scope="shared", members=[other])


@action("start")
def start(ctx, opponent, clock="600"):
    me = ctx.user.username
    opponent = str(opponent).strip()
    seconds = CLOCKS.get(str(clock), 600)
    computer = None
    try:
        computer = ctx.records.get(COMPUTER, opponent)
    except Exception:  # not a record id: a person, or Morrow by name
        if opponent.lower() == "morrow":
            computer = _morrow(ctx)
    if computer is None:
        if opponent == me:
            return error("Pick somebody else to play, or Morrow")
        if opponent not in ctx.people():
            return error(f"There is nobody called {opponent} on this cloud")
    table = _table_with(ctx, None if computer else opponent)
    colour = _colour_next(ctx, table, me, computer)
    them = computer["name"] if computer else opponent
    white, black = (me, them) if colour == "white" else (them, me)
    fields = {
        "table": table.id,
        "name": f"{white} – {black}",
        "white": white,
        "black": black,
        "moves": [],
    }
    if seconds:
        fields.update(clock=seconds, white_left=seconds * 1000, black_left=seconds * 1000)
    if computer:
        fields.update(computer="black" if colour == "white" else "white", level=int(computer.get("level") or 1))
    game = ctx.records.create(GAME, fields)
    if computer and colour == "black":
        # Morrow has white: he opens.
        _answer(ctx, game, Game(), dict(fields))
    return go("games", game=game.id)


def _colour_next(ctx, table, me: str, computer) -> str:
    """The colour the person has this time: the other one from the last game at
    this table — the same table from either side, so the two of them agree — and
    a toss of a coin for the first."""
    games = [g for g in ctx.records.list(GAME, table=table.id) if not computer or g.get("computer")]
    if not games:
        return random.choice(("white", "black"))
    # The newest; two made in one second are told apart by the order the store keeps them in.
    last = max(enumerate(games), key=lambda pair: (str(pair[1].created_at or ""), pair[0]))[1]
    was_white = last.get("white") == me and last.get("computer") != "white"
    return "black" if was_white else "white"


def _side_of(fields: dict, who: str) -> str:
    """The side the person plays here: w, b, or "" for somebody looking on."""
    if fields.get("white") == who and fields.get("computer") != "white":
        return "w"
    if fields.get("black") == who and fields.get("computer") != "black":
        return "b"
    return ""


def _finish(ctx, game, fields: dict, result: str, reason: str) -> str:
    """The game is over: it says how, the clock stands, and Morrow's level moves if he was beaten."""
    # The clock stands where it was: the side thinking keeps what it had left.
    left = time_left(fields, _now(ctx)) if fields.get("turn_at") else None
    if left:
        fields.update(white_left=left["w"], black_left=left["b"])
    fields.update(result=result, reason=reason, turn_at=None, draw="")
    said = ""
    side = fields.get("computer")
    if side:
        computer = _morrow(ctx)
        person_won = (result == WHITE_WINS and side == "black") or (result == BLACK_WINS and side == "white")
        level = int(computer.get("level") or 1)
        if person_won:
            up = min(morrow.TOP, level + 1)
            ctx.records.patch(COMPUTER, computer.id, level=up, won=int(computer.get("won") or 0) + 1)
            said = (
                f"You beat Morrow! He is level {up} now"
                if up > level
                else "You beat Morrow at his very best"
            )
        elif result == DRAWN:
            ctx.records.patch(COMPUTER, computer.id, drawn=int(computer.get("drawn") or 0) + 1)
            said = "A draw with Morrow"
        else:
            ctx.records.patch(COMPUTER, computer.id, lost=int(computer.get("lost") or 0) + 1)
            said = "Morrow won this one"
    return said


def _save(ctx, game, fields: dict):
    changed = {k: v for k, v in fields.items() if k not in ("table",)}
    return ctx.records.patch(GAME, game.id, changed, rev=game.rev)


def _clock_after_move(fields: dict, board: Game, side: str, now: dt.datetime) -> bool:
    """Take the time the side just spent off its clock. False when its flag fell first."""
    if not fields.get("clock"):
        return True
    left = time_left(fields, now)
    key = "white_left" if side == "w" else "black_left"
    if left is not None and fields.get("turn_at"):
        fields[key] = left[side]
        if left[side] <= 0:
            return False
    return True


def _after_move(fields: dict, board: Game, now: dt.datetime) -> None:
    fields["moves"] = board.ucis
    # The clock runs once both sides have made their first move.
    fields["turn_at"] = _stamp(now) if fields.get("clock") and len(board.moves) >= 2 else None


def _answer(ctx, game, board: Game, fields: dict) -> str:
    """Morrow's move, played and kept. What to say about the game, if it ended.

    He works it out at once, but takes his time over it as a person would
    (`morrow.pause`): the move is kept back until `answer_at`, every screen
    shows him thinking with his clock running until then, and the time comes
    off his clock as yours does."""
    started = _now(ctx)
    level = int(fields.get("level") or 1)
    choice = morrow.choose(board, level)
    side = board.turn
    key = "white_left" if side == "w" else "black_left"
    left = int(fields[key]) if fields.get("clock") and fields.get(key) is not None else None
    pause = morrow.pause(level, len(board.moves), int(fields.get("clock") or 0), left)
    worked = int((_now(ctx) - started).total_seconds() * 1000)
    spent = max(pause, worked)
    answer = started + dt.timedelta(milliseconds=spent)
    if left is not None and fields.get("turn_at"):
        fields[key] = max(1, left - spent)
    board.play(choice)
    _after_move(fields, board, answer)
    ended = board.outcome()
    said = _finish(ctx, game, fields, *ended) if ended else ""
    fields["answer_at"] = _stamp(answer)
    _save(ctx, game, fields)
    return said


@action("play")
def play(ctx, game, move):
    me = ctx.user.username
    fields = dict(game.fields)
    fields["moves"] = list(fields.get("moves") or [])
    if fields.get("result"):
        return error("This game is over")
    side = _side_of(fields, me)
    if not side:
        return error("You are not playing in this game")
    board = Game(fields["moves"])
    now = _now(ctx)
    word = str(move).strip().lower()
    other = fields["black"] if side == "w" else fields["white"]
    try:
        if word == "resign":
            said = _finish(ctx, game, fields, BLACK_WINS if side == "w" else WHITE_WINS, "resigned")
            _save(ctx, game, fields)
            return toast(said or "You resigned")
        if word == "draw":
            if fields.get("computer"):
                # Morrow takes a draw when he stands worse, and only then.
                if morrow.evaluate(board.position) * (1 if board.turn != side else -1) < -80:
                    said = _finish(ctx, game, fields, DRAWN, "agreed")
                    _save(ctx, game, fields)
                    return toast("Morrow takes the draw")
                return toast("Morrow would rather play on")
            if fields.get("draw") and fields["draw"] != me:
                _finish(ctx, game, fields, DRAWN, "agreed")
                _save(ctx, game, fields)
                return toast("Drawn by agreement")
            fields["draw"] = me
            _save(ctx, game, fields)
            return toast(f"Draw offered to {other}")
        if word == "decline":
            if fields.get("draw") and fields["draw"] != me:
                fields["draw"] = ""
                _save(ctx, game, fields)
            return toast("Playing on")
        if word == "time":
            left = time_left(fields, now)
            loser = board.turn
            if left is None or left[loser] > 0 or not fields.get("turn_at"):
                return error("There is time left on the clock")
            said = _finish(ctx, game, fields, *lost_on_time(loser, board.position))
            _save(ctx, game, fields)
            return toast(said or "Time")
        if thinking(fields, now):
            return error(f"{other} is still thinking")
        if board.turn != side:
            return error(f"It is {other}'s move")
        try:
            chosen = board.parse(word if len(word) in (4, 5) and word[1].isdigit() else str(move).strip())
        except ValueError as exc:
            return error(str(exc)[:1].upper() + str(exc)[1:])
        if not _clock_after_move(fields, board, side, now):
            said = _finish(ctx, game, fields, *lost_on_time(side, board.position))
            _save(ctx, game, fields)
            return toast(said or "Your time ran out")
        board.play(chosen)
        fields["draw"] = ""
        _after_move(fields, board, now)
        ended = board.outcome()
        if ended:
            said = _finish(ctx, game, fields, *ended)
            _save(ctx, game, fields)
            return toast(said) if said else None
        if fields.get("computer"):
            saved = _save(ctx, game, fields)
            said = _answer(ctx, saved, board, fields)
            return toast(said) if said else None
        _save(ctx, game, fields)
    except Conflict:
        return error("The game moved on meanwhile; look again")
    return None
