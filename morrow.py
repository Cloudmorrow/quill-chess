"""Morrow at the board: how he picks a move, at each of his levels.

He looks ahead a few moves — alpha-beta over the core's rules
(`cloudmorrow.quill.chess`), captures followed to the end so he does not
stop counting in the middle of an exchange — and weighs what he sees by the
pieces and where they stand. At a low level he looks one move ahead and
plays loosely, a little noise on every score, so he leaves pieces hanging;
each level looks further and plays straighter, and at the top he looks five
moves ahead with nothing loose about it.

He has no clock worth trusting in the sandbox, so how long he thinks is
counted in positions looked at (`budget`), not in seconds.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from cloudmorrow.quill.chess import Game, Move, Position

TOP = 10
MATE = 100_000


@dataclass(frozen=True)
class Level:
    depth: int  # how many moves ahead, his and yours
    captures: bool  # whether he follows the captures to the end
    noise: int  # the most, in hundredths of a pawn, his feeling about a move may be off
    budget: int  # the most positions he looks at for one move


LEVELS = {
    1: Level(1, False, 300, 2_000),
    2: Level(1, True, 180, 4_000),
    3: Level(2, True, 120, 6_000),
    4: Level(2, True, 70, 8_000),
    5: Level(2, True, 35, 10_000),
    6: Level(3, True, 25, 14_000),
    7: Level(3, True, 12, 18_000),
    8: Level(3, True, 0, 30_000),
    9: Level(4, True, 0, 60_000),
    10: Level(5, True, 0, 100_000),
}

VALUE = {"p": 100, "n": 320, "b": 330, "r": 500, "q": 900, "k": 0}

# Where each piece likes to stand, from white's side, a8 first (as the board is read).
_TABLES = {
    "p": (
        0, 0, 0, 0, 0, 0, 0, 0,
        50, 50, 50, 50, 50, 50, 50, 50,
        10, 10, 20, 30, 30, 20, 10, 10,
        5, 5, 10, 25, 25, 10, 5, 5,
        0, 0, 0, 20, 20, 0, 0, 0,
        5, -5, -10, 0, 0, -10, -5, 5,
        5, 10, 10, -20, -20, 10, 10, 5,
        0, 0, 0, 0, 0, 0, 0, 0,
    ),
    "n": (
        -50, -40, -30, -30, -30, -30, -40, -50,
        -40, -20, 0, 0, 0, 0, -20, -40,
        -30, 0, 10, 15, 15, 10, 0, -30,
        -30, 5, 15, 20, 20, 15, 5, -30,
        -30, 0, 15, 20, 20, 15, 0, -30,
        -30, 5, 10, 15, 15, 10, 5, -30,
        -40, -20, 0, 5, 5, 0, -20, -40,
        -50, -40, -30, -30, -30, -30, -40, -50,
    ),
    "b": (
        -20, -10, -10, -10, -10, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 10, 10, 5, 0, -10,
        -10, 5, 5, 10, 10, 5, 5, -10,
        -10, 0, 10, 10, 10, 10, 0, -10,
        -10, 10, 10, 10, 10, 10, 10, -10,
        -10, 5, 0, 0, 0, 0, 5, -10,
        -20, -10, -10, -10, -10, -10, -10, -20,
    ),
    "r": (
        0, 0, 0, 0, 0, 0, 0, 0,
        5, 10, 10, 10, 10, 10, 10, 5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        0, 0, 0, 5, 5, 0, 0, 0,
    ),
    "q": (
        -20, -10, -10, -5, -5, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 5, 5, 5, 0, -10,
        -5, 0, 5, 5, 5, 5, 0, -5,
        0, 0, 5, 5, 5, 5, 0, -5,
        -10, 5, 5, 5, 5, 5, 0, -10,
        -10, 0, 5, 0, 0, 0, 0, -10,
        -20, -10, -10, -5, -5, -10, -10, -20,
    ),
    "k": (
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -20, -30, -30, -40, -40, -30, -30, -20,
        -10, -20, -20, -20, -20, -20, -20, -10,
        20, 20, 0, 0, 0, 0, 20, 20,
        20, 30, 10, 0, 0, 10, 30, 20,
    ),
    # The king once the queens are off: to the middle.
    "K": (
        -50, -40, -30, -20, -20, -30, -40, -50,
        -30, -20, -10, 0, 0, -10, -20, -30,
        -30, -10, 20, 30, 30, 20, -10, -30,
        -30, -10, 30, 40, 40, 30, -10, -30,
        -30, -10, 30, 40, 40, 30, -10, -30,
        -30, -10, 20, 30, 30, 20, -10, -30,
        -30, -30, 0, 0, 0, 0, -30, -30,
        -50, -30, -30, -30, -30, -30, -30, -50,
    ),
}


def _by_square(table: tuple, side: str) -> list[int]:
    """The table by square (a1 = 0): white reads it upside down, black as it stands, mirrored."""
    out = [0] * 64
    for sq in range(64):
        f, r = sq % 8, sq // 8
        out[sq] = table[(7 - r) * 8 + f] if side == "w" else table[r * 8 + f]
    return out


# For each piece letter, its worth on each square, from its own side: white's positive.
WORTH: dict[str, list[int]] = {}
for _kind, _table in _TABLES.items():
    if _kind == "K":
        continue
    WORTH[_kind.upper()] = [VALUE[_kind] + v for v in _by_square(_table, "w")]
    WORTH[_kind] = [VALUE[_kind] + v for v in _by_square(_table, "b")]
LATE_KING = {"K": _by_square(_TABLES["K"], "w"), "k": _by_square(_TABLES["K"], "b")}


def evaluate(pos: Position) -> int:
    """How the board stands for the side to move, in hundredths of a pawn."""
    score = 0
    queens = 0
    kings = {}
    for sq, piece in enumerate(pos.board):
        if not piece:
            continue
        if piece in "Kk":
            kings[piece] = sq
            continue
        if piece in "Qq":
            queens += 1
        worth = WORTH[piece][sq]
        score += worth if piece.isupper() else -worth
    late = queens == 0
    for piece, sq in kings.items():
        worth = (LATE_KING if late else WORTH)[piece][sq]
        score += worth if piece == "K" else -worth
    return score if pos.turn == "w" else -score


class Search:
    def __init__(self, level: Level, rng: random.Random) -> None:
        self.level = level
        self.rng = rng
        self.nodes = 0
        self.out_of_time = False

    def _ordered(self, pos: Position, moves: list[Move], first: Move | None = None) -> list[Move]:
        board = pos.board

        def weight(m: Move) -> int:
            if m == first:
                return 1_000_000
            taken = board[m.to]
            w = 0
            if taken:
                w = 10 * VALUE[taken.lower()] - VALUE[board[m.frm].lower()] + 10_000
            if m.promote:
                w += 9_000
            return w

        return sorted(moves, key=weight, reverse=True)

    def _children(self, pos: Position, captures_only: bool = False):
        """The legal moves, best-looking first, with the positions after them."""
        side = pos.turn
        moves = pos.pseudo()
        if captures_only:
            moves = [m for m in moves if pos.board[m.to] or m.promote or (m.to == pos.ep and pos.board[m.frm] in "Pp")]
        for m in self._ordered(pos, moves):
            after = pos.apply(m)
            if not after.in_check(side):
                yield m, after

    def quiet(self, pos: Position, alpha: int, beta: int, depth: int = 0) -> int:
        self.nodes += 1
        stand = evaluate(pos)
        if stand >= beta:
            return stand
        if not self.level.captures or depth > 6:
            return stand
        alpha = max(alpha, stand)
        for _m, after in self._children(pos, captures_only=True):
            score = -self.quiet(after, -beta, -alpha, depth + 1)
            if score >= beta:
                return score
            alpha = max(alpha, score)
        return alpha

    def negamax(self, pos: Position, depth: int, alpha: int, beta: int, ply: int) -> int:
        self.nodes += 1
        if self.nodes > self.level.budget:
            self.out_of_time = True
        if depth <= 0 or self.out_of_time:
            return self.quiet(pos, alpha, beta)
        if pos.halfmove >= 100:
            return 0
        best = -MATE
        any_move = False
        for _m, after in self._children(pos):
            any_move = True
            score = -self.negamax(after, depth - 1, -beta, -alpha, ply + 1)
            if score > best:
                best = score
            if score > alpha:
                alpha = score
            if alpha >= beta:
                break
        if not any_move:
            return -MATE + ply if pos.in_check() else 0
        return best


def choose(game: Game, level: int, rng: random.Random | None = None) -> Move:
    """Morrow's move in this game, at this level (1 to TOP)."""
    rng = rng or random.Random()
    lv = LEVELS[max(1, min(TOP, int(level)))]
    pos = game.position
    legal = pos.legal()
    if not legal:
        raise ValueError("there is no move to make")
    if len(legal) == 1:
        return legal[0]
    search = Search(lv, rng)
    seen = {p.key() for p in game.positions[:-1]}
    scores: dict[Move, int] = {}
    best_first: Move | None = None
    for depth in range(1, lv.depth + 1):
        found: dict[Move, int] = {}
        alpha = -MATE - 1
        for m in search._ordered(pos, legal, best_first):
            after = pos.apply(m)
            score = -search.negamax(after, depth - 1, -MATE - 1, -alpha if lv.noise == 0 else MATE + 1, 1)
            if after.key() in seen and score > 0:
                score = 0  # going back to where it was is a draw he does not want when ahead
            found[m] = score
            if lv.noise == 0 and score > alpha:
                alpha = score
            if search.out_of_time:
                break
        if search.out_of_time and depth > 1:
            break  # a deeper look cut short is no look: the last whole one stands
        scores = found
        best_first = max(scores, key=scores.get)
    # How he feels about each move: what he worked out, give or take his level's noise.
    felt = {m: s + (rng.randint(-lv.noise, lv.noise) if lv.noise else 0) for m, s in scores.items()}
    top = max(felt.values())
    return rng.choice([m for m, s in felt.items() if s == top])


# How long he takes over a move, in milliseconds: from, to — longer at the low
# levels, where a person expects to see him puzzle it out; a little shorter
# at the top, where the looking itself takes a while.
PAUSES = {1: (3_000, 6_500), 2: (3_000, 6_000), 3: (2_800, 5_500), 4: (2_600, 5_000), 5: (2_500, 4_800),
          6: (2_400, 4_500), 7: (2_200, 4_200), 8: (2_000, 4_000), 9: (2_000, 3_800), 10: (2_000, 3_600)}


def pause(level: int, played: int, clock: int = 0, left: int | None = None, rng: random.Random | None = None) -> int:
    """How long Morrow takes over his next move, in milliseconds.

    The opening moves come quicker, a five-minute game is quicker throughout,
    and he never spends more than a twentieth of what is left on his clock."""
    rng = rng or random.Random()
    low, high = PAUSES[max(1, min(TOP, int(level)))]
    wait = rng.randint(low, high)
    if played < 4:
        wait = wait * 6 // 10
    if clock and clock <= 300:
        wait = wait * 6 // 10
    if left is not None:
        wait = min(wait, max(300, left // 20))
    return wait
