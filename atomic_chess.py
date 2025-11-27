import sys
import os
import json
import random
import pygame
from pygame.locals import QUIT, MOUSEBUTTONDOWN, KEYDOWN, K_r, K_ESCAPE, K_d

from pythonosc.udp_client import SimpleUDPClient


# -----------------------------
# Configuration
# -----------------------------

BOARD_PIXELS = 640
INFO_BAR_HEIGHT = 100
WINDOW_SIZE = (BOARD_PIXELS, BOARD_PIXELS + INFO_BAR_HEIGHT)
FPS = 60

# Colours
COLOUR_BG = (8, 4, 10)
COLOUR_LIGHT = (255, 240, 120)   # atomic yellow
COLOUR_DARK = (190, 40, 40)      # atomic red
COLOUR_HIGHLIGHT = (255, 255, 0)
COLOUR_HIGHLIGHT_MOVE = (120, 220, 255)
COLOUR_LAST_MOVE = (200, 255, 120)
COLOUR_TEXT_MAIN = (220, 255, 240)
COLOUR_TEXT_SUB = (140, 190, 170)
COLOUR_EXPLOSION = (255, 120, 80)
COLOUR_DAMAGED_FILL = (80, 0, 0, 160)   # semi-transparent dark red
COLOUR_DAMAGED_X = (255, 200, 200)      # crack / X color

# Piece colours
WHITE_PIECE_FILL = (230, 250, 255)
BLACK_PIECE_FILL = (40, 10, 50)
WHITE_PIECE_OUTLINE = (20, 40, 60)
BLACK_PIECE_OUTLINE = (220, 200, 255)
WHITE_PIECE_TEXT = (10, 40, 50)
BLACK_PIECE_TEXT = (245, 235, 255)

# OSC
OSC_HOST = "127.0.0.1"
OSC_PORT = 9001


# -----------------------------
# Helper classes
# -----------------------------

class PieceType:
    def __init__(
        self,
        name,
        kind,
        directions=None,
        max_range=1,
        can_jump=False,
        explosion_radius=None,
        immune_to_explosion=False,
        damaged_ok=False,
        is_royal=False,
        script_name=None,
        script_param=0.0,
    ):
        """
        kind: 'king', 'queen', 'rook', 'bishop', 'knight', 'pawn', or 'custom'
        directions: list of (dx, dy) for sliders or leapers
        explosion_radius: if not None, overrides global atomic_radius for this type
        immune_to_explosion: if True, piece is not destroyed by explosions
        damaged_ok: if True, piece may enter damaged squares even if globally blocked
        is_royal: if True, used for 'king_capture' victory condition
        script_name: 'jitter', 'stutter', 'decay', 'charge', 'heat', 'entropy', or None
        script_param: float/int parameter for the script
        """
        self.name = name
        self.kind = kind
        self.directions = directions or []
        self.max_range = max_range
        self.can_jump = can_jump
        self.explosion_radius = explosion_radius
        self.immune_to_explosion = immune_to_explosion
        self.damaged_ok = damaged_ok
        self.is_royal = is_royal
        self.script_name = script_name
        self.script_param = script_param


class Piece:
    def __init__(self, color, type_name, symbol=None):
        self.color = color  # 'white' or 'black'
        self.type_name = type_name  # key into piece_types
        self.symbol = symbol if symbol is not None else type_name[0].upper()
        self.state = {}  # for stateful scripts and overrides


# -----------------------------
# Explosion Animation
# -----------------------------

class ExplosionManager:
    """Manages atomic explosion animations on the board."""

    def __init__(self):
        # Each explosion: {"cells": [...], "start_ms": int, "radius": int}
        self.explosions = []
        self.duration_ms = 500  # explosion life

    def trigger_explosion(self, center, now_ms, radius_cells=1):
        cx, cy = center
        cells = []
        for dy in range(-radius_cells, radius_cells + 1):
            for dx in range(-radius_cells, radius_cells + 1):
                cells.append((cx + dx, cy + dy))
        self.explosions.append({"cells": cells, "start_ms": now_ms, "radius": radius_cells})

    def update_and_draw(self, surface, now_ms, square_size, rows, cols):
        still_active = []
        for exp in self.explosions:
            age = now_ms - exp["start_ms"]
            if age > self.duration_ms:
                continue

            t = age / self.duration_ms  # 0..1
            max_radius = square_size * 0.7
            radius = int((0.3 + 0.7 * t) * max_radius)
            alpha = int(255 * (1.0 - t))

            for (x, y) in exp["cells"]:
                if 0 <= x < cols and 0 <= y < rows:
                    sx, sy = square_to_screen(x, y, square_size, rows)
                    overlay = pygame.Surface((square_size, square_size), pygame.SRCALPHA)
                    pygame.draw.circle(
                        overlay,
                        COLOUR_EXPLOSION + (alpha,),
                        (square_size // 2, square_size // 2),
                        radius,
                        0
                    )
                    surface.blit(overlay, (sx, sy))

            still_active.append(exp)

        self.explosions = still_active


# -----------------------------
# Coordinate helpers
# -----------------------------

def square_to_screen(x, y, square_size, rows):
    """Board (x,y) -> top-left pixel. y=0 is bottom row."""
    row_from_top = rows - 1 - y
    px = x * square_size
    py = row_from_top * square_size
    return px, py


def screen_to_square(px, py, square_size, rows, cols):
    if px < 0 or py < 0:
        return None
    max_w = cols * square_size
    max_h = rows * square_size
    if px >= max_w or py >= max_h:
        return None
    col = px // square_size
    row_from_top = py // square_size
    y = rows - 1 - row_from_top
    x = col
    if 0 <= x < cols and 0 <= y < rows:
        return (x, y)
    return None


def coord_to_alg(x, y):
    """Convert (x,y) to algebraic like 'a1', with y=0 -> rank 1."""
    file_char = chr(ord('a') + x)
    rank = y + 1
    return f"{file_char}{rank}"


def piece_name(piece):
    """Return a human-friendly name like 'white_queen' or 'black_pawn'."""
    if piece is None:
        return "none"
    color = piece.color
    base = piece.type_name
    return f"{color}_{base}"


def pos_in_rect(pos, rect):
    """Check if a board pos is inside an inclusive rect [x1,y1,x2,y2]."""
    if pos is None or rect is None or len(rect) != 4:
        return False
    x, y = pos
    x1, y1, x2, y2 = rect
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1 <= x <= x2 and y1 <= y <= y2


# -----------------------------
# Game Class
# -----------------------------

class AtomicChessGame:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Atomic Chess – OSC @ 9001")
        self.screen = pygame.display.set_mode(WINDOW_SIZE)
        self.clock = pygame.time.Clock()

        # Fonts
        self.font_pieces = pygame.font.SysFont(None, 36, bold=True)
        self.font_info = pygame.font.SysFont(None, 22)
        self.font_big = pygame.font.SysFont(None, 28, bold=True)
        self.font_dsl = pygame.font.SysFont(None, 20)
        self.font_dsl_title = pygame.font.SysFont(None, 26, bold=True)

        # Geometry / board
        self.cols = 8
        self.rows = 8
        self.square_size = self.compute_square_size()

        # Logic
        self.board = []
        self.piece_types = {}
        self.turn = 'white'
        self.fullmove = 1  # increments after black's move
        self.last_move = None  # ((from_x,from_y),(to_x,to_y))
        self.game_over = False
        self.result_text = ""

        # Selection
        self.selected_square = None
        self.legal_targets = []

        # Explosion manager
        self.explosions = ExplosionManager()

        # Damaged squares (x, y) that can no longer be used as destinations
        self.damaged = set()

        # Global rules
        self.init_default_rules()

        # Timing for time-based win conditions
        self.start_time_ms = pygame.time.get_ticks()

        # DSL state
        self.dsl_mode = False
        self.dsl_input = ""
        self.dsl_messages = []

        # OSC
        self.osc_client = SimpleUDPClient(OSC_HOST, OSC_PORT)

        # Chunks & edge triggers
        self.chunks = {}         # name -> {rect:[x1,y1,x2,y2], fill:{color,type}, active:bool, owner:str, enter_script, leave_script}
        self.edge_triggers = []  # list of {type, dist, chunk, ...}
        self.rand_chunk_counter = 0

        # Init rules & position
        self.init_default_piece_types()
        self.setup_standard_position()

    # -------------------------
    # Rules
    # -------------------------

    def init_default_rules(self):
        self.rules = {
            # Capture / explosion
            "capture_mode": "atomic",       # "atomic", "normal", "none"
            "atomic_radius": 1,            # >=0
            "center_damages": True,        # explosion damages centre square
            "center_survives": True,       # capturing piece survives centre blast
            "damage_blocks_move": True,    # damaged squares block movement

            # Victory
            "victory": "elimination",      # "elimination", "king_capture", "none"
            "max_fullmoves": 0,            # 0 = no move limit
            "max_seconds": 0,              # 0 = no time limit
            "limit_result": "draw",        # "draw", "white", "black"

            # OSC behaviour
            "osc_neighbour_radius": 2,
            "osc_neighbours": True,
            "osc_label": "",
        }

    # -------------------------
    # Geometry helpers
    # -------------------------

    def compute_square_size(self):
        return BOARD_PIXELS // max(self.cols, self.rows)

    def resize_board(self, cols, rows):
        """Hard reset of board size, used only for 'standard' / board command."""
        self.cols = max(2, min(20, cols))
        self.rows = max(2, min(20, rows))
        self.square_size = self.compute_square_size()
        self.board = [[None for _ in range(self.cols)] for _ in range(self.rows)]
        self.selected_square = None
        self.legal_targets = []
        self.last_move = None
        self.turn = 'white'
        self.fullmove = 1
        self.game_over = False
        self.result_text = ""
        self.damaged = set()
        self.start_time_ms = pygame.time.get_ticks()

    def clear_board(self):
        self.board = [[None for _ in range(self.cols)] for _ in range(self.rows)]
        self.damaged = set()

    # -------------------------
    # Piece type definitions
    # -------------------------

    def init_default_piece_types(self):
        self.piece_types = {}

        # King-like
        self.piece_types['king'] = PieceType(
            'king',
            'king',
            directions=[
                (1, 0), (-1, 0), (0, 1), (0, -1),
                (1, 1), (1, -1), (-1, 1), (-1, -1)
            ],
            max_range=1,
            can_jump=False,
            explosion_radius=None,
            immune_to_explosion=False,
            damaged_ok=False,
            is_royal=True,
        )

        # Rook-like
        self.piece_types['rook'] = PieceType(
            'rook',
            'rook',
            directions=[(1, 0), (-1, 0), (0, 1), (0, -1)],
            max_range=None,
            can_jump=False,
        )

        # Bishop-like
        self.piece_types['bishop'] = PieceType(
            'bishop',
            'bishop',
            directions=[(1, 1), (1, -1), (-1, 1), (-1, -1)],
            max_range=None,
            can_jump=False,
        )

        # Queen-like
        self.piece_types['queen'] = PieceType(
            'queen',
            'queen',
            directions=[
                (1, 0), (-1, 0), (0, 1), (0, -1),
                (1, 1), (1, -1), (-1, 1), (-1, -1)
            ],
            max_range=None,
            can_jump=False,
        )

        # Knight / leaper
        self.piece_types['knight'] = PieceType(
            'knight',
            'knight',
            directions=[
                (2, 1), (1, 2), (-1, 2), (-2, 1),
                (-2, -1), (-1, -2), (1, -2), (2, -1)
            ],
            max_range=1,
            can_jump=True,
        )

        # Pawn moves handled specially
        self.piece_types['pawn'] = PieceType(
            'pawn',
            'pawn',
            directions=[],
            max_range=1,
            can_jump=False,
        )

    # -------------------------
    # Setup positions
    # -------------------------

    def setup_standard_position(self):
        self.resize_board(8, 8)
        # Pawns
        for x in range(8):
            self.place_piece(x, 1, Piece('white', 'pawn', 'P'))
            self.place_piece(x, 6, Piece('black', 'pawn', 'P'))
        # Rooks
        self.place_piece(0, 0, Piece('white', 'rook', 'R'))
        self.place_piece(7, 0, Piece('white', 'rook', 'R'))
        self.place_piece(0, 7, Piece('black', 'rook', 'R'))
        self.place_piece(7, 7, Piece('black', 'rook', 'R'))
        # Knights
        self.place_piece(1, 0, Piece('white', 'knight', 'N'))
        self.place_piece(6, 0, Piece('white', 'knight', 'N'))
        self.place_piece(1, 7, Piece('black', 'knight', 'N'))
        self.place_piece(6, 7, Piece('black', 'knight', 'N'))
        # Bishops
        self.place_piece(2, 0, Piece('white', 'bishop', 'B'))
        self.place_piece(5, 0, Piece('white', 'bishop', 'B'))
        self.place_piece(2, 7, Piece('black', 'bishop', 'B'))
        self.place_piece(5, 7, Piece('black', 'bishop', 'B'))
        # Queens
        self.place_piece(3, 0, Piece('white', 'queen', 'Q'))
        self.place_piece(3, 7, Piece('black', 'queen', 'Q'))
        # Kings
        self.place_piece(4, 0, Piece('white', 'king', 'K'))
        self.place_piece(4, 7, Piece('black', 'king', 'K'))

        self.turn = 'white'
        self.fullmove = 1
        self.game_over = False
        self.result_text = ""
        self.last_move = None
        self.selected_square = None
        self.legal_targets = []
        self.damaged = set()
        self.start_time_ms = pygame.time.get_ticks()

    def place_piece(self, x, y, piece):
        if 0 <= x < self.cols and 0 <= y < self.rows:
            self.board[y][x] = piece

    # -------------------------
    # OSC helpers
    # -------------------------

    def get_neighbour_pieces(self, center, radius=None):
        """
        Return list of strings 'coord:color_type' for all pieces within
        Chebyshev distance <= radius of center (including center).
        DSL can change radius and whether this is sent at all.
        """
        if not self.rules.get("osc_neighbours", True):
            return []
        if radius is None:
            radius = int(self.rules.get("osc_neighbour_radius", 2))
        radius = max(0, radius)

        cx, cy = center
        neighbours = []
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                x = cx + dx
                y = cy + dy
                if 0 <= x < self.cols and 0 <= y < self.rows:
                    p = self.board[y][x]
                    if p is not None:
                        coord = coord_to_alg(x, y)
                        pname = piece_name(p)
                        neighbours.append(f"{coord}:{pname}")
        return neighbours

    def count_pieces(self):
        white = 0
        black = 0
        for y in range(self.rows):
            for x in range(self.cols):
                p = self.board[y][x]
                if p is not None:
                    if p.color == 'white':
                        white += 1
                    else:
                        black += 1
        return white, black

    def send_move_osc(self, from_sq, to_sq, captured_piece_name, neighbour_list):
        try:
            from_alg = coord_to_alg(*from_sq)
            to_alg = coord_to_alg(*to_sq)
            uci = from_alg + to_alg
            san = uci  # placeholder
            fen = f"{self.cols}x{self.rows}"  # placeholder

            white_count, black_count = self.count_pieces()

            args = [
                self.fullmove,
                uci,
                san,
                fen,
                from_alg,
                to_alg,
                captured_piece_name,
                white_count,
                black_count,
            ] + neighbour_list

            label = self.rules.get("osc_label", "")
            if label:
                args.append(label)

            print("OSC /move", args)
            self.osc_client.send_message("/move", args)
        except Exception as e:
            print(f"OSC /move send error: {e}")

    def send_game_over_osc(self, result, outcome_str):
        try:
            args = [result, outcome_str]
            print("OSC /game_over", args)
            self.osc_client.send_message("/game_over", args)
        except Exception as e:
            print(f"OSC /game_over send error: {e}")

    # -------------------------
    # Move generation
    # -------------------------

    def in_bounds(self, x, y):
        return 0 <= x < self.cols and 0 <= y < self.rows

    def square_blocked_by_damage(self, x, y, piece_type):
        """Check if a square is blocked by damage for this piece."""
        if (x, y) not in self.damaged:
            return False
        if not self.rules.get("damage_blocks_move", True):
            return False
        if piece_type and piece_type.damaged_ok:
            return False
        return True

    def generate_moves_for_piece(self, x, y):
        piece = self.board[y][x]
        if piece is None:
            return []

        ptype = self.piece_types.get(piece.type_name)
        if ptype is None:
            return []

        moves = []  # list of (tx, ty)

        # Pawn movement
        if ptype.kind == 'pawn':
            dir_y = 1 if piece.color == 'white' else -1
            start_rank = 1 if piece.color == 'white' else (self.rows - 2)

            # Forward 1
            nx = x
            ny = y + dir_y
            if self.in_bounds(nx, ny) and self.board[ny][nx] is None and not self.square_blocked_by_damage(nx, ny, ptype):
                moves.append((nx, ny))
                # Forward 2 from start
                ny2 = y + 2 * dir_y
                if (
                    y == start_rank
                    and self.in_bounds(nx, ny2)
                    and self.board[ny2][nx] is None
                    and not self.square_blocked_by_damage(nx, ny2, ptype)
                ):
                    moves.append((nx, ny2))

            # Captures
            for dx in (-1, 1):
                nx = x + dx
                ny = y + dir_y
                if self.in_bounds(nx, ny) and not self.square_blocked_by_damage(nx, ny, ptype):
                    target = self.board[ny][nx]
                    if target is not None and target.color != piece.color:
                        moves.append((nx, ny))

            return moves

        # Knight / leaper-like
        if ptype.kind == 'knight' or (ptype.can_jump and ptype.directions):
            for (dx, dy) in ptype.directions:
                nx = x + dx
                ny = y + dy
                if not self.in_bounds(nx, ny):
                    continue
                if self.square_blocked_by_damage(nx, ny, ptype):
                    continue
                target = self.board[ny][nx]
                if target is None or target.color != piece.color:
                    moves.append((nx, ny))
            if ptype.kind == 'knight' or ptype.can_jump:
                return moves

        # Sliders / king / queen / rook / bishop / custom
        if ptype.directions:
            base_max = ptype.max_range if ptype.max_range is not None else max(self.cols, self.rows)
            bonus = int(piece.state.get("range_bonus", 0))
            max_range = base_max + bonus if ptype.max_range is not None else base_max
            for (dx, dy) in ptype.directions:
                for step in range(1, max_range + 1):
                    nx = x + dx * step
                    ny = y + dy * step
                    if not self.in_bounds(nx, ny):
                        break
                    if self.square_blocked_by_damage(nx, ny, ptype):
                        break
                    target = self.board[ny][nx]
                    if target is None:
                        moves.append((nx, ny))
                    else:
                        if target.color != piece.color:
                            moves.append((nx, ny))
                        break

        return moves

    # -------------------------
    # Script hooks
    # -------------------------

    def resolve_script_descriptor(self, piece, ptype):
        """Return (script_name, script_param) considering per-piece override and type default."""
        if ptype is None:
            return None, 0.0
        sname = piece.state.get("script_name_override")
        if sname is None:
            sname = ptype.script_name
        if not sname:
            return None, 0.0
        param = piece.state.get("script_param_override")
        if param is None:
            param = ptype.script_param or 0.0
        return sname, param

    def apply_script_pre_move(self, piece, ptype, from_sq, to_sq, legal_moves):
        """
        Allow piece-type scripts to alter or cancel the chosen move.
        Returns new (to_x,to_y) or None to cancel.
        """
        sname, param = self.resolve_script_descriptor(piece, ptype)
        if not sname:
            return to_sq
        sname = sname.lower()

        if sname == "stutter":
            # chance that the move simply doesn't happen
            try:
                p = float(param)
            except Exception:
                p = 0.0
            p = max(0.0, min(1.0, p if p <= 1.0 else p / 100.0))
            if random.random() < p:
                # cancel move
                return None
            return to_sq

        if sname == "jitter":
            # chance to redirect to a random legal square
            try:
                p = float(param)
            except Exception:
                p = 0.0
            p = max(0.0, min(1.0, p if p <= 1.0 else p / 100.0))
            if legal_moves and random.random() < p:
                return random.choice(legal_moves)
            return to_sq

        if sname == "entropy":
            # entropy = jitter that ramps up over time
            try:
                base_p = float(param)
            except Exception:
                base_p = 0.0
            level = int(piece.state.get("entropy_level", 0))
            p = base_p * (1.0 + 0.1 * level)
            p = max(0.0, min(1.0, p))
            if legal_moves and random.random() < p:
                return random.choice(legal_moves)
            return to_sq

        # decay / charge / heat are post-move only
        return to_sq

    def apply_script_post_move(self, piece, ptype, from_sq, to_sq):
        """
        Post-move scripts (decay, charge, heat, entropy-count).
        May kill the piece or modify its state.
        """
        sname, param = self.resolve_script_descriptor(piece, ptype)
        if not sname:
            return
        sname = sname.lower()

        if sname == "decay":
            try:
                max_moves = int(param)
            except Exception:
                max_moves = 0
            if max_moves <= 0:
                return
            left = piece.state.get("decay_left", max_moves)
            left -= 1
            piece.state["decay_left"] = left
            if left <= 0:
                tx, ty = to_sq
                if self.in_bounds(tx, ty) and self.board[ty][tx] is piece:
                    self.board[ty][tx] = None

        elif sname == "charge":
            # every N moves, increase movement range bonus by +1
            try:
                threshold = int(param)
            except Exception:
                threshold = 0
            if threshold <= 0:
                return
            count = int(piece.state.get("charge_count", 0)) + 1
            piece.state["charge_count"] = count
            if count >= threshold:
                piece.state["charge_count"] = 0
                bonus = int(piece.state.get("range_bonus", 0)) + 1
                piece.state["range_bonus"] = bonus

        elif sname == "heat":
            # every N moves, increase explosion radius bonus by +1
            try:
                threshold = int(param)
            except Exception:
                threshold = 0
            if threshold <= 0:
                return
            count = int(piece.state.get("heat_count", 0)) + 1
            piece.state["heat_count"] = count
            if count >= threshold:
                piece.state["heat_count"] = 0
                bonus = int(piece.state.get("heat_bonus", 0)) + 1
                piece.state["heat_bonus"] = bonus

        elif sname == "entropy":
            # entropy level used by pre-move jitter; just count moves
            level = int(piece.state.get("entropy_level", 0)) + 1
            piece.state["entropy_level"] = level

    # -------------------------
    # Game state helpers
    # -------------------------

    def update_game_over(self):
        # If already decided, don't change it
        if self.game_over:
            return

        mode = self.rules.get("victory", "elimination")

        if mode == "king_capture":
            white_royals = 0
            black_royals = 0
            for y in range(self.rows):
                for x in range(self.cols):
                    p = self.board[y][x]
                    if p is None:
                        continue
                    pt = self.piece_types.get(p.type_name)
                    if pt is None or not pt.is_royal:
                        continue
                    if p.color == "white":
                        white_royals += 1
                    else:
                        black_royals += 1

            if white_royals == 0 and black_royals == 0:
                self.game_over = True
                self.result_text = "½–½ (No royals left)"
                self.send_game_over_osc("1/2-1/2", "NO_ROYALS")
                return
            elif white_royals == 0:
                self.game_over = True
                self.result_text = "0–1 (White royal lost)"
                self.send_game_over_osc("0-1", "WHITE_ROYAL_LOST")
                return
            elif black_royals == 0:
                self.game_over = True
                self.result_text = "1–0 (Black royal lost)"
                self.send_game_over_osc("1-0", "BLACK_ROYAL_LOST")
                return

        if mode == "elimination":
            white_count, black_count = self.count_pieces()
            if white_count == 0 and black_count == 0:
                self.game_over = True
                self.result_text = "½–½ (Mutual destruction)"
                self.send_game_over_osc("1/2-1/2", "MUTUAL_DESTRUCTION")
                return
            elif white_count == 0:
                self.game_over = True
                self.result_text = "0–1 (White eliminated)"
                self.send_game_over_osc("0-1", "WHITE_ELIMINATED")
                return
            elif black_count == 0:
                self.game_over = True
                self.result_text = "1–0 (Black eliminated)"
                self.send_game_over_osc("1-0", "BLACK_ELIMINATED")
                return

        # Time / move limits as secondary conditions
        self.check_limits_game_over()

    def check_limits_game_over(self):
        if self.game_over:
            return

        max_moves = int(self.rules.get("max_fullmoves", 0) or 0)
        max_seconds = int(self.rules.get("max_seconds", 0) or 0)
        limit_result = self.rules.get("limit_result", "draw")

        # Move limit
        if max_moves > 0 and self.fullmove > max_moves:
            self.apply_limit_result("Move limit reached", "MOVE_LIMIT", limit_result)
            return

        # Time limit
        if max_seconds > 0:
            elapsed_ms = pygame.time.get_ticks() - self.start_time_ms
            if elapsed_ms >= max_seconds * 1000:
                self.apply_limit_result("Time limit reached", "TIME_LIMIT", limit_result)

    def apply_limit_result(self, reason_text, osc_reason, limit_result):
        if self.game_over:
            return
        self.game_over = True
        if limit_result == "white":
            self.result_text = f"1–0 ({reason_text})"
            self.send_game_over_osc("1-0", osc_reason + "_WHITE")
        elif limit_result == "black":
            self.result_text = f"0–1 ({reason_text})"
            self.send_game_over_osc("0-1", osc_reason + "_BLACK")
        else:
            # draw
            self.result_text = f"½–½ ({reason_text})"
            self.send_game_over_osc("1/2-1/2", osc_reason + "_DRAW")

    # -------------------------
    # Configuration save/load
    # -------------------------

    def export_config(self):
        """Serialize current rules and position to a dict."""
        cfg = {
            "cols": self.cols,
            "rows": self.rows,
            "turn": self.turn,
            "fullmove": self.fullmove,
            "damaged": list([x, y] for (x, y) in self.damaged),
            "piece_types": {},
            "board": [],
            "rules": self.rules,
            "chunks": self.chunks,
            "edge_triggers": self.edge_triggers,
        }

        for name, pt in self.piece_types.items():
            cfg["piece_types"][name] = {
                "kind": pt.kind,
                "directions": list([dx, dy] for (dx, dy) in pt.directions),
                "max_range": pt.max_range,
                "can_jump": pt.can_jump,
                "explosion_radius": pt.explosion_radius,
                "immune_to_explosion": pt.immune_to_explosion,
                "damaged_ok": pt.damaged_ok,
                "is_royal": pt.is_royal,
                "script_name": pt.script_name,
                "script_param": pt.script_param,
            }

        for y in range(self.rows):
            for x in range(self.cols):
                p = self.board[y][x]
                if p is not None:
                    cfg["board"].append({
                        "x": x,
                        "y": y,
                        "color": p.color,
                        "type": p.type_name,
                        "symbol": p.symbol
                    })

        return cfg

    def import_config(self, cfg):
        """Load rules and position from a dict."""
        try:
            cols = int(cfg.get("cols", 8))
            rows = int(cfg.get("rows", 8))
        except Exception:
            cols, rows = 8, 8

        self.cols = max(2, min(20, cols))
        self.rows = max(2, min(20, rows))
        self.square_size = self.compute_square_size()

        self.turn = cfg.get("turn", "white")
        self.fullmove = int(cfg.get("fullmove", 1))
        self.game_over = False
        self.result_text = ""
        self.last_move = None
        self.selected_square = None
        self.legal_targets = []
        self.start_time_ms = pygame.time.get_ticks()

        # Rules
        self.init_default_rules()
        cfg_rules = cfg.get("rules")
        if isinstance(cfg_rules, dict):
            self.rules.update(cfg_rules)

        # Chunks & edge triggers
        self.chunks = {}
        cfg_chunks = cfg.get("chunks", {})
        if isinstance(cfg_chunks, dict):
            for name, data in cfg_chunks.items():
                rect = data.get("rect")
                fill = data.get("fill")
                active = bool(data.get("active", False))
                owner = data.get("owner", "any")
                enter_script = data.get("enter_script")
                leave_script = data.get("leave_script")
                if isinstance(rect, (list, tuple)) and len(rect) == 4:
                    x1, y1, x2, y2 = rect
                    self.chunks[str(name)] = {
                        "rect": [int(x1), int(y1), int(x2), int(y2)],
                        "fill": fill if isinstance(fill, dict) else None,
                        "active": active,
                        "owner": owner if owner in ("white", "black", "any") else "any",
                        "enter_script": enter_script if isinstance(enter_script, dict) else None,
                        "leave_script": leave_script if isinstance(leave_script, dict) else None,
                    }

        self.edge_triggers = []
        cfg_trigs = cfg.get("edge_triggers", [])
        if isinstance(cfg_trigs, list):
            for t in cfg_trigs:
                if not isinstance(t, dict):
                    continue
                mode = t.get("mode", "chunk")
                if mode == "random":
                    # preserve random edge trigger as-is
                    try:
                        dist_val = int(t.get("dist", 0))
                    except Exception:
                        dist_val = 0
                    try:
                        minw = int(t.get("minw", 1))
                    except Exception:
                        minw = 1
                    try:
                        maxw = int(t.get("maxw", self.cols))
                    except Exception:
                        maxw = self.cols
                    try:
                        minh = int(t.get("minh", 1))
                    except Exception:
                        minh = 1
                    try:
                        maxh = int(t.get("maxh", self.rows))
                    except Exception:
                        maxh = self.rows
                    trig = {
                        "type": str(t.get("type", "any")),
                        "dist": max(0, dist_val),
                        "mode": "random",
                        "minw": minw,
                        "maxw": maxw,
                        "minh": minh,
                        "maxh": maxh,
                    }
                    self.edge_triggers.append(trig)
                else:
                    ttype = t.get("type", "any")
                    dist = int(t.get("dist", 0))
                    chunk = t.get("chunk")
                    if not chunk:
                        continue
                    self.edge_triggers.append({
                        "type": str(ttype),
                        "dist": max(0, dist),
                        "chunk": str(chunk),
                    })

        # Piece types
        self.piece_types = {}
        pt_cfg = cfg.get("piece_types", {})
        for name, data in pt_cfg.items():
            kind = data.get("kind", "custom")
            dirs = data.get("directions", [])
            max_range = data.get("max_range", 1)
            can_jump = bool(data.get("can_jump", False))
            explosion_radius = data.get("explosion_radius", None)
            immune_to_explosion = bool(data.get("immune_to_explosion", False))
            damaged_ok = bool(data.get("damaged_ok", False))
            is_royal = bool(data.get("is_royal", False))
            script_name = data.get("script_name", None)
            script_param = data.get("script_param", 0.0)

            directions = []
            for d in dirs:
                if isinstance(d, (list, tuple)) and len(d) == 2:
                    directions.append((int(d[0]), int(d[1])))

            self.piece_types[name] = PieceType(
                name,
                kind,
                directions,
                max_range,
                can_jump,
                explosion_radius,
                immune_to_explosion,
                damaged_ok,
                is_royal,
                script_name,
                script_param,
            )

        # Ensure standard archetypes exist at the minimum
        if "king" not in self.piece_types or "queen" not in self.piece_types:
            self.init_default_piece_types()

        # Board
        self.board = [[None for _ in range(self.cols)] for _ in range(self.rows)]
        for entry in cfg.get("board", []):
            try:
                x = int(entry["x"])
                y = int(entry["y"])
                color = entry["color"]
                tname = entry["type"]
                symbol = entry.get("symbol", tname[0].upper())
            except Exception:
                continue

            if tname not in self.piece_types:
                # fall back to queen-like if unknown
                base = self.piece_types.get("queen")
                self.piece_types[tname] = PieceType(
                    tname,
                    base.kind,
                    list(base.directions),
                    base.max_range,
                    base.can_jump,
                )

            if 0 <= x < self.cols and 0 <= y < self.rows:
                self.board[y][x] = Piece(color, tname, symbol)

        # Damaged squares
        damaged_list = cfg.get("damaged", [])
        self.damaged = set()
        for d in damaged_list:
            try:
                x, y = int(d[0]), int(d[1])
                if 0 <= x < self.cols and 0 <= y < self.rows:
                    self.damaged.add((x, y))
            except Exception:
                continue

    def save_config_to_file(self, name):
        """Save current config to a JSON file in ./configs/."""
        if not name:
            self.add_dsl_message("save: missing name")
            return
        safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_"))
        if not safe_name:
            self.add_dsl_message("save: invalid name")
            return
        filename = safe_name + ".json"
        folder = "configs"
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        try:
            cfg = self.export_config()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            self.add_dsl_message(f"Saved config to {path}")
        except Exception as e:
            self.add_dsl_message(f"save error: {e}")

    def load_config_from_file(self, name):
        """Load config from a JSON file in ./configs/."""
        if not name:
            self.add_dsl_message("load: missing name")
            return
        safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_"))
        if not safe_name:
            self.add_dsl_message("load: invalid name")
            return
        filename = safe_name + ".json"
        folder = "configs"
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        if not os.path.isfile(path):
            self.add_dsl_message(f"load: file not found: {path}")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.import_config(cfg)
            self.add_dsl_message(f"Loaded config from {path}")
        except Exception as e:
            self.add_dsl_message(f"load error: {e}")

    # -------------------------
    # Chunk / edge logic
    # -------------------------

    def toggle_chunk(self, name, mover_color=None):
        ch = self.chunks.get(name)
        if not ch:
            self.add_dsl_message(f"chunk: unknown '{name}'")
            return
        rect = ch.get("rect")
        if not rect or len(rect) != 4:
            self.add_dsl_message(f"chunk '{name}' has invalid rect")
            return
        x1, y1, x2, y2 = rect
        # normalize
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1

        owner = ch.get("owner", "any")
        if mover_color is not None and owner in ("white", "black") and mover_color != owner:
            # Edge-triggered toggle blocked by ownership
            return

        active = ch.get("active", False)
        if active:
            # Turn OFF: mark damaged and clear pieces
            for y in range(y1, y2 + 1):
                for x in range(x1, x2 + 1):
                    if not self.in_bounds(x, y):
                        continue
                    self.board[y][x] = None
                    self.damaged.add((x, y))
            ch["active"] = False
            self.add_dsl_message(f"chunk '{name}' disabled (voided).")
        else:
            # Turn ON: undamage region and optionally fill pieces
            for y in range(y1, y2 + 1):
                for x in range(x1, x2 + 1):
                    if not self.in_bounds(x, y):
                        continue
                    if (x, y) in self.damaged:
                        self.damaged.remove((x, y))
            ch["active"] = True
            fill = ch.get("fill")
            if isinstance(fill, dict):
                color = fill.get("color", "white").lower()
                tname = fill.get("type", "pawn").lower()
                if tname not in self.piece_types:
                    base = self.piece_types.get("queen")
                    self.piece_types[tname] = PieceType(
                        tname,
                        base.kind,
                        list(base.directions),
                        base.max_range,
                        base.can_jump,
                    )
                # fill every empty square in the chunk
                for y in range(y1, y2 + 1):
                    for x in range(x1, x2 + 1):
                        if not self.in_bounds(x, y):
                            continue
                        if self.board[y][x] is None:
                            self.board[y][x] = Piece(color, tname, tname[0].upper())
            self.add_dsl_message(f"chunk '{name}' enabled.")

    def spawn_random_chunk_near_edge(self, piece, pos, trig):
        """
        Spawn a new chunk of random size attached to the edge closest to the piece.
        trig: {type, dist, mode='random', minw, maxw, minh, maxh}
        """
        if piece is None or pos is None:
            return
        x, y = pos
        # board distances
        d_left = x
        d_right = self.cols - 1 - x
        d_bottom = y
        d_top = self.rows - 1 - y
        dists = {
            "left": d_left,
            "right": d_right,
            "bottom": d_bottom,
            "top": d_top,
        }
        mind = min(dists.values())
        # only trigger if within specified dist
        try:
            thresh = int(trig.get("dist", 0))
        except Exception:
            thresh = 0
        if mind > thresh:
            return

        # choose one of the nearest edges (if tied, choose one that is tied at random)
        nearest_edges = [e for e, d in dists.items() if d == mind]
        edge = random.choice(nearest_edges)

        # random size
        minw = max(1, int(trig.get("minw", 1)))
        maxw = max(minw, int(trig.get("maxw", self.cols)))
        minh = max(1, int(trig.get("minh", 1)))
        maxh = max(minh, int(trig.get("maxh", self.rows)))
        w = random.randint(minw, min(maxw, self.cols))
        h = random.randint(minh, min(maxh, self.rows))

        # clamp to board and attach to edge closest to piece
        if edge in ("left", "right"):
            # vertical strip attached to left or right, centred on y
            y1 = max(0, y - h // 2)
            y2 = min(self.rows - 1, y1 + h - 1)
            if edge == "left":
                x1 = 0
                x2 = min(self.cols - 1, x1 + w - 1)
            else:
                x2 = self.cols - 1
                x1 = max(0, x2 - w + 1)
        else:
            # horizontal strip attached to bottom or top, centred on x
            x1 = max(0, x - w // 2)
            x2 = min(self.cols - 1, x1 + w - 1)
            if edge == "bottom":
                y1 = 0
                y2 = min(self.rows - 1, y1 + h - 1)
            else:
                y2 = self.rows - 1
                y1 = max(0, y2 - h + 1)

        name = f"rand_{self.rand_chunk_counter}"
        self.rand_chunk_counter += 1

        self.chunks[name] = {
            "rect": [x1, y1, x2, y2],
            "fill": None,
            "active": True,
            "owner": "any",
            "enter_script": None,
            "leave_script": None,
        }
        self.add_dsl_message(f"Random chunk '{name}' spawned at ({x1},{y1})..({x2},{y2}) near {edge} edge.")

    def apply_chunk_script_override(self, piece, scr):
        """Apply a chunk-defined script override to a piece."""
        if not scr or not isinstance(scr, dict):
            return
        name = scr.get("name")
        param = scr.get("param", 0.0)
        if not name:
            return
        if name == "none":
            piece.state.pop("script_name_override", None)
            piece.state.pop("script_param_override", None)
        else:
            piece.state["script_name_override"] = name
            piece.state["script_param_override"] = param

    def check_edge_triggers(self, piece, pos):
        if not self.edge_triggers:
            return
        if piece is None:
            return
        x, y = pos
        d_left = x
        d_right = self.cols - 1 - x
        d_bottom = y
        d_top = self.rows - 1 - y
        min_dist = min(d_left, d_right, d_bottom, d_top)

        for trig in self.edge_triggers:
            ttype = trig.get("type", "any")
            dist = trig.get("dist", 0)
            mode = trig.get("mode", "chunk")
            if ttype != "any" and ttype != piece.type_name:
                continue
            if min_dist > dist:
                continue
            if mode == "random":
                self.spawn_random_chunk_near_edge(piece, pos, trig)
            else:
                chunk = trig.get("chunk")
                if not chunk:
                    continue
                self.toggle_chunk(chunk, mover_color=piece.color)

    def apply_chunk_entry_exit(self, piece, from_sq, to_sq):
        """When a piece enters/leaves chunks, apply script overrides."""
        if piece is None:
            return
        for name, ch in self.chunks.items():
            if not ch.get("active", False):
                continue
            rect = ch.get("rect")
            if not rect or len(rect) != 4:
                continue
            owner = ch.get("owner", "any")
            if owner in ("white", "black") and piece.color != owner:
                continue
            in_from = pos_in_rect(from_sq, rect)
            in_to = pos_in_rect(to_sq, rect)
            # Enter
            if in_to and not in_from:
                scr = ch.get("enter_script")
                if scr:
                    self.apply_chunk_script_override(piece, scr)
                    self.add_dsl_message(f"{piece.color} {piece.type_name} entered chunk '{name}' script zone.")
            # Leave
            if in_from and not in_to:
                scr = ch.get("leave_script")
                if scr:
                    self.apply_chunk_script_override(piece, scr)
                    self.add_dsl_message(f"{piece.color} {piece.type_name} left chunk '{name}' script zone.")

    # -------------------------
    # DSL stuff
    # -------------------------

    def add_dsl_message(self, text):
        self.dsl_messages.append(text)
        self.dsl_messages = self.dsl_messages[-16:]  # keep last few

    def process_dsl_command(self, cmd):
        cmd = cmd.strip()
        if not cmd:
            return
        parts = cmd.split()
        if not parts:
            return

        op = parts[0].lower()

        if op == "help":
            self.add_dsl_message("Commands:")
            self.add_dsl_message("  standard           -> reset to 8x8 chess-ish atomic + rules")
            self.add_dsl_message("  board W H          -> set board size (hard reset)")
            self.add_dsl_message("  clear              -> remove all pieces")
            self.add_dsl_message("  add c t x y [sym]  -> add piece (color,type,x,y)")
            self.add_dsl_message("  move t base        -> type t moves like base (king,queen,rook,bishop,knight,pawn)")
            self.add_dsl_message("  ptype t explode R  -> set explosion radius for type (R>=0, 0 = no blast)")
            self.add_dsl_message("  ptype t immune on/off      -> explosion immunity")
            self.add_dsl_message("  ptype t damaged_ok on/off  -> can enter damaged squares")
            self.add_dsl_message("  ptype t royal on/off       -> used for king_capture victory")
            self.add_dsl_message("  ptype t range N            -> set max_range (0 = infinite)")
            self.add_dsl_message("  ptype t jump on/off        -> treat dirs as leaper moves")
            self.add_dsl_message("  ptype t dirs dx dy ...     -> overwrite directions with custom vectors")
            self.add_dsl_message("  ptype t add_dir dx dy      -> append a single direction")
            self.add_dsl_message("  ptype t clear_dirs         -> remove all directions")
            self.add_dsl_message("  ptype t script none|jitter P|stutter P|decay N|charge N|heat N|entropy P")
            self.add_dsl_message("  rule capture atomic|normal|none")
            self.add_dsl_message("  rule atomic_radius N")
            self.add_dsl_message("  rule center_damages on/off")
            self.add_dsl_message("  rule center_survives on/off")
            self.add_dsl_message("  rule damage_blocks on/off")
            self.add_dsl_message("  rule victory elimination|king|none")
            self.add_dsl_message("  rule max_moves N           -> full-move limit (0 disables)")
            self.add_dsl_message("  rule max_time S            -> time limit in seconds (0 disables)")
            self.add_dsl_message("  rule limit_result draw|white|black")
            self.add_dsl_message("  osc neighbour_radius N")
            self.add_dsl_message("  osc neighbours on/off")
            self.add_dsl_message("  osc label TEXT...")
            self.add_dsl_message("  chunk define NAME x1 y1 x2 y2")
            self.add_dsl_message("  chunk fill NAME color type")
            self.add_dsl_message("  chunk owner NAME white|black|any")
            self.add_dsl_message("  chunk script NAME enter|leave MODE [ARG]")
            self.add_dsl_message("  chunk on NAME / chunk off NAME")
            self.add_dsl_message("  chunk reset")
            self.add_dsl_message("  edge add TYPE DIST CHUNK   (TYPE or 'any')")
            self.add_dsl_message("  edge clear")
            self.add_dsl_message("  save NAME          -> save config to configs/NAME.json")
            self.add_dsl_message("  load NAME          -> load config from configs/NAME.json")
            return

        if op == "standard":
            self.init_default_rules()
            self.init_default_piece_types()
            self.setup_standard_position()
            self.chunks = {}
            self.edge_triggers = []
            self.add_dsl_message("Standard position + rules + cleared chunks/edges.")
            return

        if op == "board" and len(parts) >= 3:
            try:
                w = int(parts[1])
                h = int(parts[2])
                self.resize_board(w, h)
                self.clear_board()
                self.chunks = {}
                self.edge_triggers = []
                self.add_dsl_message(f"Board resized to {self.cols}x{self.rows}.")
            except ValueError:
                self.add_dsl_message("board: usage board W H")
            return

        if op == "clear":
            self.clear_board()
            self.add_dsl_message("Board cleared.")
            return

        if op == "add" and len(parts) >= 5:
            color = parts[1].lower()
            tname = parts[2].lower()
            try:
                x = int(parts[3])
                y = int(parts[4])
            except ValueError:
                self.add_dsl_message("add: x,y must be integers.")
                return
            sym = parts[5] if len(parts) >= 6 else tname[0].upper()

            if color not in ("white", "black"):
                self.add_dsl_message("add: colour must be white or black.")
                return

            if tname not in self.piece_types:
                # default to queen movement if unknown
                base = self.piece_types.get('queen')
                self.piece_types[tname] = PieceType(
                    tname,
                    base.kind,
                    list(base.directions),
                    base.max_range,
                    base.can_jump,
                )
                self.add_dsl_message(f"Type '{tname}' created (queen-like).")

            self.place_piece(x, y, Piece(color, tname, sym))
            self.add_dsl_message(f"Added {color} {tname} at {x},{y}.")
            return

        if op == "move" and len(parts) >= 3:
            tname = parts[1].lower()
            base_name = parts[2].lower()
            base = self.piece_types.get(base_name)
            if base is None:
                self.add_dsl_message(f"move: base '{base_name}' unknown.")
                return
            pt = self.piece_types.get(tname)
            if pt is None:
                pt = PieceType(tname, base.kind)
                self.piece_types[tname] = pt
            pt.kind = base.kind
            pt.directions = list(base.directions)
            pt.max_range = base.max_range
            pt.can_jump = base.can_jump
            self.add_dsl_message(f"Type '{tname}' now moves like '{base_name}'.")
            return

        if op == "ptype" and len(parts) >= 3:
            tname = parts[1].lower()
            sub = parts[2].lower()
            pt = self.piece_types.get(tname)
            if pt is None:
                self.add_dsl_message(f"ptype: unknown type '{tname}'")
                return

            if sub == "explode" and len(parts) >= 4:
                try:
                    r = int(parts[3])
                    if r < 0:
                        r = 0
                    pt.explosion_radius = r
                    self.add_dsl_message(f"Type '{tname}' explosion radius set to {r}.")
                except ValueError:
                    self.add_dsl_message("ptype explode: radius must be an integer.")
                return

            if sub == "immune" and len(parts) >= 4:
                val = parts[3].lower()
                pt.immune_to_explosion = val in ("on", "true", "1", "yes")
                self.add_dsl_message(f"Type '{tname}' immune_to_explosion = {pt.immune_to_explosion}.")
                return

            if sub == "damaged_ok" and len(parts) >= 4:
                val = parts[3].lower()
                pt.damaged_ok = val in ("on", "true", "1", "yes")
                self.add_dsl_message(f"Type '{tname}' damaged_ok = {pt.damaged_ok}.")
                return

            if sub == "royal" and len(parts) >= 4:
                val = parts[3].lower()
                pt.is_royal = val in ("on", "true", "1", "yes")
                self.add_dsl_message(f"Type '{tname}' is_royal = {pt.is_royal}.")
                return

            if sub == "range" and len(parts) >= 4:
                try:
                    r = int(parts[3])
                    if r <= 0:
                        pt.max_range = None
                        self.add_dsl_message(f"Type '{tname}' max_range = infinite.")
                    else:
                        pt.max_range = r
                        self.add_dsl_message(f"Type '{tname}' max_range = {r}.")
                except ValueError:
                    self.add_dsl_message("ptype range: N must be integer.")
                return

            if sub == "jump" and len(parts) >= 4:
                val = parts[3].lower()
                pt.can_jump = val in ("on", "true", "1", "yes")
                self.add_dsl_message(f"Type '{tname}' can_jump = {pt.can_jump}.")
                return

            if sub == "dirs" and len(parts) >= 5:
                # ptype t dirs dx dy [dx dy]...
                if (len(parts) - 3) % 2 != 0:
                    self.add_dsl_message("ptype dirs: need pairs dx dy ...")
                    return
                new_dirs = []
                try:
                    for i in range(3, len(parts), 2):
                        dx = int(parts[i])
                        dy = int(parts[i + 1])
                        new_dirs.append((dx, dy))
                    pt.directions = new_dirs
                    self.add_dsl_message(f"Type '{tname}' directions overwritten with {len(new_dirs)} vectors.")
                except ValueError:
                    self.add_dsl_message("ptype dirs: dx,dy must be integers.")
                return

            if sub == "add_dir" and len(parts) >= 5:
                try:
                    dx = int(parts[3])
                    dy = int(parts[4])
                    pt.directions.append((dx, dy))
                    self.add_dsl_message(f"Type '{tname}' added direction ({dx},{dy}).")
                except ValueError:
                    self.add_dsl_message("ptype add_dir: dx,dy must be integers.")
                return

            if sub == "clear_dirs":
                pt.directions = []
                self.add_dsl_message(f"Type '{tname}' directions cleared.")
                return

            if sub == "script":
                # ptype t script none|jitter P|stutter P|decay N|charge N|heat N|entropy P
                if len(parts) >= 4:
                    mode = parts[3].lower()
                    if mode == "none":
                        pt.script_name = None
                        pt.script_param = 0.0
                        self.add_dsl_message(f"Type '{tname}' script cleared.")
                        return
                    if mode in ("jitter", "stutter", "entropy"):
                        if len(parts) >= 5:
                            try:
                                p = float(parts[4])
                                pt.script_name = mode
                                pt.script_param = p
                                self.add_dsl_message(f"Type '{tname}' script = {mode} ({p}).")
                            except ValueError:
                                self.add_dsl_message(f"ptype script {mode} P: P must be number.")
                        else:
                            self.add_dsl_message(f"ptype script {mode} P: missing P.")
                        return
                    if mode in ("decay", "charge", "heat"):
                        if len(parts) >= 5:
                            try:
                                n = int(parts[4])
                                if n <= 0:
                                    n = 1
                                pt.script_name = mode
                                pt.script_param = n
                                self.add_dsl_message(f"Type '{tname}' script = {mode} ({n}).")
                            except ValueError:
                                self.add_dsl_message(f"ptype script {mode} N: N must be integer.")
                        else:
                            self.add_dsl_message(f"ptype script {mode} N: missing N.")
                        return
                self.add_dsl_message("ptype script usage: ptype t script none|jitter P|stutter P|decay N|charge N|heat N|entropy P")
                return

            self.add_dsl_message(f"ptype: unknown subcommand '{sub}'")
            return

        if op == "rule" and len(parts) >= 2:
            key = parts[1].lower()

            if key == "capture" and len(parts) >= 3:
                val = parts[2].lower()
                if val in ("atomic", "normal", "none"):
                    self.rules["capture_mode"] = val
                    self.add_dsl_message(f"capture_mode = {val}")
                else:
                    self.add_dsl_message("rule capture: use atomic|normal|none")
                return

            if key == "atomic_radius" and len(parts) >= 3:
                try:
                    r = int(parts[2])
                    if r < 0:
                        r = 0
                    self.rules["atomic_radius"] = r
                    self.add_dsl_message(f"atomic_radius = {r}")
                except ValueError:
                    self.add_dsl_message("rule atomic_radius: integer required")
                return

            if key == "center_damages" and len(parts) >= 3:
                val = parts[2].lower()
                self.rules["center_damages"] = val in ("on", "true", "1", "yes")
                self.add_dsl_message(f"center_damages = {self.rules['center_damages']}")
                return

            if key == "center_survives" and len(parts) >= 3:
                val = parts[2].lower()
                self.rules["center_survives"] = val in ("on", "true", "1", "yes")
                self.add_dsl_message(f"center_survives = {self.rules['center_survives']}")
                return

            if key == "damage_blocks" and len(parts) >= 3:
                val = parts[2].lower()
                self.rules["damage_blocks_move"] = val in ("on", "true", "1", "yes")
                self.add_dsl_message(f"damage_blocks_move = {self.rules['damage_blocks_move']}")
                return

            if key == "victory" and len(parts) >= 3:
                val = parts[2].lower()
                if val in ("elimination", "king", "king_capture", "none"):
                    if val == "king":
                        val = "king_capture"
                    self.rules["victory"] = val
                    self.add_dsl_message(f"victory = {val}")
                else:
                    self.add_dsl_message("rule victory: use elimination|king|none")
                return

            if key == "max_moves" and len(parts) >= 3:
                try:
                    n = int(parts[2])
                    if n < 0:
                        n = 0
                    self.rules["max_fullmoves"] = n
                    self.add_dsl_message(f"max_fullmoves = {n}")
                except ValueError:
                    self.add_dsl_message("rule max_moves: integer required")
                return

            if key == "max_time" and len(parts) >= 3:
                try:
                    s = int(parts[2])
                    if s < 0:
                        s = 0
                    self.rules["max_seconds"] = s
                    if s > 0:
                        self.start_time_ms = pygame.time.get_ticks()
                    self.add_dsl_message(f"max_seconds = {s}")
                except ValueError:
                    self.add_dsl_message("rule max_time: integer required")
                return

            if key == "limit_result" and len(parts) >= 3:
                val = parts[2].lower()
                if val in ("draw", "white", "black"):
                    self.rules["limit_result"] = val
                    self.add_dsl_message(f"limit_result = {val}")
                else:
                    self.add_dsl_message("rule limit_result: use draw|white|black")
                return

            self.add_dsl_message(f"rule: unknown key '{key}'")
            return

        if op == "osc" and len(parts) >= 2:
            sub = parts[1].lower()

            if sub == "neighbour_radius" and len(parts) >= 3:
                try:
                    r = int(parts[2])
                    if r < 0:
                        r = 0
                    self.rules["osc_neighbour_radius"] = r
                    self.add_dsl_message(f"osc_neighbour_radius = {r}")
                except ValueError:
                    self.add_dsl_message("osc neighbour_radius: integer required")
                return

            if sub == "neighbours" and len(parts) >= 3:
                val = parts[2].lower()
                self.rules["osc_neighbours"] = val in ("on", "true", "1", "yes")
                self.add_dsl_message(f"osc_neighbours = {self.rules['osc_neighbours']}")
                return

            if sub == "label" and len(parts) >= 3:
                label = " ".join(parts[2:])
                self.rules["osc_label"] = label
                self.add_dsl_message(f"osc_label set to '{label}'")
                return

            self.add_dsl_message(f"osc: unknown subcommand '{sub}'")
            return

        if op == "chunk" and len(parts) >= 2:
            sub = parts[1].lower()
            if sub == "define" and len(parts) >= 7:
                name = parts[2]
                try:
                    x1 = int(parts[3])
                    y1 = int(parts[4])
                    x2 = int(parts[5])
                    y2 = int(parts[6])
                except ValueError:
                    self.add_dsl_message("chunk define NAME x1 y1 x2 y2  (integers required)")
                    return
                self.chunks[name] = {
                    "rect": [x1, y1, x2, y2],
                    "fill": None,
                    "active": True,
                    "owner": "any",
                    "enter_script": None,
                    "leave_script": None,
                }
                self.add_dsl_message(f"chunk '{name}' defined at ({x1},{y1})..({x2},{y2}) and active.")
                return
            if sub == "fill" and len(parts) >= 5:
                name = parts[2]
                ch = self.chunks.get(name)
                if not ch:
                    self.add_dsl_message(f"chunk fill: unknown '{name}'")
                    return
                color = parts[3].lower()
                tname = parts[4].lower()
                ch["fill"] = {"color": color, "type": tname}
                self.add_dsl_message(f"chunk '{name}' fill = {colour} {tname}")
                return
            if sub == "owner" and len(parts) >= 4:
                name = parts[2]
                ch = self.chunks.get(name)
                if not ch:
                    self.add_dsl_message(f"chunk owner: unknown '{name}'")
                    return
                color = parts[3].lower()
                if color not in ("white", "black", "any"):
                    self.add_dsl_message("chunk owner NAME white|black|any")
                    return
                ch["owner"] = color
                self.add_dsl_message(f"chunk '{name}' owner = {color}")
                return
            if sub == "script" and len(parts) >= 5:
                name = parts[2]
                ch = self.chunks.get(name)
                if not ch:
                    self.add_dsl_message(f"chunk script: unknown '{name}'")
                    return
                phase = parts[3].lower()
                mode = parts[4].lower()
                if phase not in ("enter", "leave"):
                    self.add_dsl_message("chunk script NAME enter|leave MODE [ARG]")
                    return
                key = "enter_script" if phase == "enter" else "leave_script"
                if mode == "none":
                    ch[key] = {"name": "none", "param": 0.0}
                    self.add_dsl_message(f"chunk '{name}' {phase} script cleared.")
                    return
                # modes mirroring ptype scripts
                if mode in ("jitter", "stutter", "entropy"):
                    if len(parts) < 6:
                        self.add_dsl_message(f"chunk script {name} {phase} {mode} ARG  (missing ARG)")
                        return
                    try:
                        p = float(parts[5])
                    except ValueError:
                        self.add_dsl_message(f"chunk script {mode}: ARG must be number")
                        return
                    ch[key] = {"name": mode, "param": p}
                    self.add_dsl_message(f"chunk '{name}' {phase} script = {mode} ({p}).")
                    return
                if mode in ("decay", "charge", "heat"):
                    if len(parts) < 6:
                        self.add_dsl_message(f"chunk script {name} {phase} {mode} ARG  (missing ARG)")
                        return
                    try:
                        n = int(parts[5])
                    except ValueError:
                        self.add_dsl_message(f"chunk script {mode}: ARG must be integer")
                        return
                    if n <= 0:
                        n = 1
                    ch[key] = {"name": mode, "param": n}
                    self.add_dsl_message(f"chunk '{name}' {phase} script = {mode} ({n}).")
                    return
                self.add_dsl_message("chunk script: MODE must be one of none|jitter|stutter|entropy|decay|charge|heat")
                return
            if sub == "on" and len(parts) >= 3:
                name = parts[2]
                self.toggle_chunk(name)
                return
            if sub == "off" and len(parts) >= 3:
                name = parts[2]
                self.toggle_chunk(name)
                return
            if sub == "reset":
                self.chunks = {}
                self.edge_triggers = []
                self.add_dsl_message("All chunks and edge triggers cleared.")
                return
            self.add_dsl_message("chunk commands: define/fill/owner/script/on/off/reset")
            return

        if op == "edge" and len(parts) >= 2:
            sub = parts[1].lower()
            if sub == "add" and len(parts) >= 5:
                ttype = parts[2].lower()
                try:
                    dist = int(parts[3])
                    if dist < 0:
                        dist = 0
                except ValueError:
                    self.add_dsl_message("edge add TYPE DIST CHUNK: DIST must be integer")
                    return
                chunk = parts[4]
                if chunk not in self.chunks:
                    self.add_dsl_message(f"edge add: unknown chunk '{chunk}'")
                    return
                self.edge_triggers.append({
                    "type": ttype,
                    "dist": dist,
                    "chunk": chunk,
                })
                self.add_dsl_message(f"edge trigger added: type={ttype}, dist<={dist}, chunk='{chunk}'")
                return
            if sub == "random" and len(parts) >= 8:
                # edge random TYPE DIST MINW MAXW MINH MAXH
                ttype = parts[2].lower()
                try:
                    dist = int(parts[3])
                    minw = int(parts[4])
                    maxw = int(parts[5])
                    minh = int(parts[6])
                    maxh = int(parts[7])
                except ValueError:
                    self.add_dsl_message("edge random TYPE DIST MINW MAXW MINH MAXH  (all integers)")
                    return
                if dist < 0:
                    dist = 0
                if minw < 1:
                    minw = 1
                if minh < 1:
                    minh = 1
                trig = {
                    "type": ttype,
                    "dist": dist,
                    "mode": "random",
                    "minw": minw,
                    "maxw": maxw,
                    "minh": minh,
                    "maxh": maxh,
                }
                self.edge_triggers.append(trig)
                self.add_dsl_message(f"edge random trigger added: type={ttype}, dist<={dist}, size=({minw}-{maxw} x {minh}-{maxh})")
                return
            if sub == "clear":
                self.edge_triggers = []
                self.add_dsl_message("All edge triggers cleared.")
                return
            self.add_dsl_message("edge commands: add TYPE DIST CHUNK | random TYPE DIST MINW MAXW MINH MAXH | clear")
            return

        if op == "save" and len(parts) >= 2:
            self.save_config_to_file(parts[1])
            return

        if op == "load" and len(parts) >= 2:
            self.load_config_from_file(parts[1])
            return

        self.add_dsl_message(f"Unknown command: {cmd}")

    # -------------------------
    # Drawing
    # -------------------------

    def draw_board(self):
        # Board background
        self.screen.fill(COLOUR_BG)

        # Squares
        for y in range(self.rows):
            for x in range(self.cols):
                sx, sy = square_to_screen(x, y, self.square_size, self.rows)
                if (x + y) % 2 == 0:
                    color = COLOUR_LIGHT
                else:
                    color = COLOUR_DARK
                pygame.draw.rect(self.screen, color, (sx, sy, self.square_size, self.square_size))

        # Damaged squares overlay
        for (dx, dy) in self.damaged:
            if 0 <= dx < self.cols and 0 <= dy < self.rows:
                sx, sy = square_to_screen(dx, dy, self.square_size, self.rows)
                overlay = pygame.Surface((self.square_size, self.square_size), pygame.SRCALPHA)
                overlay.fill(COLOUR_DAMAGED_FILL)
                self.screen.blit(overlay, (sx, sy))
                # Draw an "X" crack
                pygame.draw.line(self.screen, COLOUR_DAMAGED_X, (sx + 4, sy + 4),
                                 (sx + self.square_size - 4, sy + self.square_size - 4), 2)
                pygame.draw.line(self.screen, COLOUR_DAMAGED_X, (sx + self.square_size - 4, sy + 4),
                                 (sx + 4, sy + self.square_size - 4), 2)

        # Last move highlight
        if self.last_move is not None:
            (fx, fy), (tx, ty) = self.last_move
            for (x, y) in [(fx, fy), (tx, ty)]:
                if x is None:
                    continue
                sx, sy = square_to_screen(x, y, self.square_size, self.rows)
                overlay = pygame.Surface((self.square_size, self.square_size), pygame.SRCALPHA)
                overlay.fill(COLOUR_LAST_MOVE + (80,))
                self.screen.blit(overlay, (sx, sy))

        # Selected square highlight
        if self.selected_square is not None:
            x, y = self.selected_square
            sx, sy = square_to_screen(x, y, self.square_size, self.rows)
            pygame.draw.rect(self.screen, COLOUR_HIGHLIGHT, (sx, sy, self.square_size, self.square_size), 3)

        # Legal move dots
        for (x, y) in self.legal_targets:
            sx, sy = square_to_screen(x, y, self.square_size, self.rows)
            center = (sx + self.square_size // 2, sy + self.square_size // 2)
            radius = self.square_size // 8
            pygame.draw.circle(self.screen, COLOUR_HIGHLIGHT_MOVE, center, radius)

        # Pieces
        for y in range(self.rows):
            for x in range(self.cols):
                piece = self.board[y][x]
                if piece is None:
                    continue
                sx, sy = square_to_screen(x, y, self.square_size, self.rows)
                center = (sx + self.square_size // 2, sy + self.square_size // 2)
                radius = int(self.square_size * 0.38)

                if piece.color == 'white':
                    fill = WHITE_PIECE_FILL
                    outline = WHITE_PIECE_OUTLINE
                    text_color = WHITE_PIECE_TEXT
                else:
                    fill = BLACK_PIECE_FILL
                    outline = BLACK_PIECE_OUTLINE
                    text_color = BLACK_PIECE_TEXT

                # Soft drop shadow
                shadow_surf = pygame.Surface((self.square_size, self.square_size), pygame.SRCALPHA)
                pygame.draw.circle(
                    shadow_surf,
                    (0, 0, 0, 120),
                    (self.square_size // 2 + 2, self.square_size // 2 + 3),
                    radius + 2,
                )
                self.screen.blit(shadow_surf, (sx, sy))

                # Base piece disc
                pygame.draw.circle(self.screen, fill, center, radius)
                pygame.draw.circle(self.screen, outline, center, radius, 2)

                # Inner highlight ring
                inner_radius = int(radius * 0.6)
                highlight_surf = pygame.Surface((self.square_size, self.square_size), pygame.SRCALPHA)
                pygame.draw.circle(
                    highlight_surf,
                    (255, 255, 255, 40),
                    (self.square_size // 2 - 2, self.square_size // 2 - 3),
                    inner_radius,
                    0,
                )
                self.screen.blit(highlight_surf, (sx, sy))

                txt_surf = self.font_pieces.render(piece.symbol, True, text_color)
                rect = txt_surf.get_rect(center=center)
                self.screen.blit(txt_surf, rect.topleft)

        # Explosions on top
        now_ms = pygame.time.get_ticks()
        self.explosions.update_and_draw(self.screen, now_ms, self.square_size, self.rows, self.cols)

    def draw_info_bar(self):
        bar_rect = (0, BOARD_PIXELS, BOARD_PIXELS, INFO_BAR_HEIGHT)
        pygame.draw.rect(self.screen, COLOUR_BG, bar_rect)

        turn_txt = "White" if self.turn == 'white' else "Black"
        if not self.game_over:
            main_text = f"Atomic Chess – {turn_txt} to move"
        else:
            main_text = f"Game Over – {self.result_text}"

        main_surf = self.font_big.render(main_text, True, COLOUR_TEXT_MAIN)
        self.screen.blit(main_surf, (20, BOARD_PIXELS + 8))

        if not self.game_over:
            sub = "Click to move. R: reset. D: DSL. Esc: quit."
        else:
            sub = "R: new game. D: DSL. Esc: quit."
        sub_surf = self.font_info.render(sub, True, COLOUR_TEXT_SUB)
        self.screen.blit(sub_surf, (20, BOARD_PIXELS + 36))

        # DSL panel
        if self.dsl_mode:
            panel_width = int(WINDOW_SIZE[0] * 0.85)
            panel_height = int(WINDOW_SIZE[1] * 0.7)
            panel_x = (WINDOW_SIZE[0] - panel_width) // 2
            panel_y = (WINDOW_SIZE[1] - panel_height) // 2

            panel_surface = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
            panel_surface.fill((10, 10, 30, 240))
            pygame.draw.rect(panel_surface, (120, 255, 200), (0, 0, panel_width, panel_height), 2)

            # Title
            title = "DSL Rule Editor"
            title_surf = self.font_dsl_title.render(title, True, (240, 255, 250))
            panel_surface.blit(title_surf, (16, 12))

            # Hint line
            hint = "Enter commands, press Enter to apply, Esc to close, 'help' for options."
            hint_surf = self.font_dsl.render(hint, True, (180, 220, 210))
            panel_surface.blit(hint_surf, (16, 40))

            # Prompt
            prompt = f"DSL> {self.dsl_input}"
            prompt_surf = self.font_dsl.render(prompt, True, (255, 255, 255))
            panel_surface.blit(prompt_surf, (16, 70))

            # Divider
            pygame.draw.line(panel_surface, (120, 255, 200), (12, 95), (panel_width - 12, 95), 1)

            # Recent messages, scroll from bottom up
            max_lines = 16
            messages = self.dsl_messages[-max_lines:]
            y = panel_height - 24
            for msg in reversed(messages):
                msg_surf = self.font_dsl.render(msg, True, (190, 230, 210))
                panel_surface.blit(msg_surf, (16, y))
                y -= 20
                if y < 110:
                    break

            self.screen.blit(panel_surface, (panel_x, panel_y))

    # -------------------------
    # Input Handling
    # -------------------------

    def handle_click(self, pos):
        if self.game_over or self.dsl_mode:
            return

        px, py = pos
        sq = screen_to_square(px, py, self.square_size, self.rows, self.cols)
        if sq is None:
            return
        x, y = sq
        piece = self.board[y][x]

        if self.selected_square is None:
            # Select if piece of side to move
            if piece is not None and piece.color == self.turn:
                self.select_square(x, y)
        else:
            if (x, y) == self.selected_square:
                self.selected_square = None
                self.legal_targets = []
                return

            moved = self.try_make_move(self.selected_square, (x, y))
            if not moved:
                # maybe select new piece
                if piece is not None and piece.color == self.turn:
                    self.select_square(x, y)

    def select_square(self, x, y):
        self.selected_square = (x, y)
        self.legal_targets = self.generate_moves_for_piece(x, y)

    def try_make_move(self, from_sq, to_sq):
        fx, fy = from_sq
        tx, ty = to_sq
        piece = self.board[fy][fx]
        if piece is None:
            return False

        legal = self.generate_moves_for_piece(fx, fy)
        if (tx, ty) not in legal:
            return False

        ptype = self.piece_types.get(piece.type_name)

        # Script pre-move hook
        new_target = self.apply_script_pre_move(piece, ptype, (fx, fy), (tx, ty), legal)
        if new_target is None:
            # Move cancelled by script
            return False
        tx, ty = new_target
        if not self.in_bounds(tx, ty):
            return False
        target = self.board[ty][tx]
        captured_name = piece_name(target)

        # Determine explosion behaviour
        capture_mode = self.rules.get("capture_mode", "atomic")
        is_capture = target is not None and capture_mode != "none"

        # Move piece first
        self.board[fy][fx] = None
        self.board[ty][tx] = piece

        # Handle capture modes
        if is_capture:
            if capture_mode == "normal":
                # standard capture, no explosion
                pass
            elif capture_mode == "atomic":
                # Determine explosion radius
                base_radius = self.rules.get("atomic_radius", 1)
                if base_radius < 0:
                    base_radius = 0
                if ptype and ptype.explosion_radius is not None:
                    base_radius = max(0, int(ptype.explosion_radius))

                # heat-based bonus
                heat_bonus = 0
                if piece.state.get("heat_bonus") is not None:
                    try:
                        heat_bonus = int(piece.state.get("heat_bonus", 0))
                    except Exception:
                        heat_bonus = 0
                radius = max(0, base_radius + max(0, heat_bonus))

                if radius > 0:
                    now_ms = pygame.time.get_ticks()
                    self.explosions.trigger_explosion((tx, ty), now_ms, radius_cells=radius)

                    center_survives = self.rules.get("center_survives", True)
                    # Remove all pieces in neighbourhood; optionally spare the center
                    for dy in range(-radius, radius + 1):
                        for dx in range(-radius, radius + 1):
                            nx = tx + dx
                            ny = ty + dy
                            if not self.in_bounds(nx, ny):
                                continue
                            if center_survives and nx == tx and ny == ty:
                                continue
                            victim = self.board[ny][nx]
                            if victim is None:
                                continue
                            vtype = self.piece_types.get(victim.type_name)
                            if vtype and vtype.immune_to_explosion:
                                continue
                            self.board[ny][nx] = None

                    # If center does not survive, remove capturing piece too
                    if not center_survives:
                        self.board[ty][tx] = None

                # Mark center damaged if rule says so
                if self.rules.get("center_damages", True):
                    self.damaged.add((tx, ty))

        self.last_move = ((fx, fy), (tx, ty))

        # Apply post-move script if piece survived
        if self.in_bounds(tx, ty):
            piece_after = self.board[ty][tx]
            if piece_after is piece:
                self.apply_script_post_move(piece_after, ptype, (fx, fy), (tx, ty))

        # Neighbour pieces AFTER explosion and post-script, using OSC-configurable radius
        neighbours = self.get_neighbour_pieces((tx, ty))

        # Toggle turn & fullmove
        if self.turn == 'black':
            self.fullmove += 1
        self.turn = 'white' if self.turn == 'black' else 'black'

        # Send OSC
        self.send_move_osc((fx, fy), (tx, ty), captured_name, neighbours)

        # Clear selection
        self.selected_square = None
        self.legal_targets = []

        # Edge triggers – modify chunks and pieces based on proximity to edge
        self.check_edge_triggers(piece, (tx, ty))

        # Chunk entry/exit scripts
        self.apply_chunk_entry_exit(piece, (fx, fy), (tx, ty))

        # Game over?
        self.update_game_over()

        return True

    def reset_game(self):
        self.init_default_rules()
        self.init_default_piece_types()
        self.setup_standard_position()
        self.chunks = {}
        self.edge_triggers = []

    # -------------------------
    # Main Loop
    # -------------------------

    def run(self):
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == QUIT:
                    running = False

                elif event.type == MOUSEBUTTONDOWN and event.button == 1:
                    self.handle_click(event.pos)

                elif event.type == KEYDOWN:
                    if self.dsl_mode:
                        # DSL input handling
                        if event.key == K_ESCAPE:
                            self.dsl_mode = False
                        elif event.key == pygame.K_RETURN:
                            self.process_dsl_command(self.dsl_input)
                            self.dsl_input = ""
                        elif event.key == pygame.K_BACKSPACE:
                            self.dsl_input = self.dsl_input[:-1]
                        else:
                            ch = event.unicode
                            if ch and ch.isprintable():
                                self.dsl_input += ch
                    else:
                        # Normal game keys
                        if event.key == K_ESCAPE:
                            running = False
                        elif event.key == K_r:
                            self.reset_game()
                        elif event.key == K_d:
                            self.dsl_mode = True
                            self.dsl_input = ""
                            self.add_dsl_message("DSL mode. Type 'help' for commands.")
                        # other keys ignored

            # Check time limits even if no moves are made
            self.check_limits_game_over()

            self.draw_board()
            self.draw_info_bar()

            pygame.display.flip()
            self.clock.tick(FPS)

        pygame.quit()
        sys.exit()


# -----------------------------
# Entry Point
# -----------------------------

if __name__ == "__main__":
    game = AtomicChessGame()
    game.run()
