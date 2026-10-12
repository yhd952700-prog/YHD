"""
Chess Utilities Plugin for LiuHao AI OS

Provides FEN parsing/generation, basic move validation,
board representation, game state tracking, PGN parsing,
and check/checkmate detection.

Capabilities:
- parse_fen: Parse FEN string to board state
- generate_fen: Generate FEN from board state
- display_board: ASCII board representation
- is_valid_move: Basic move validation
- make_move: Apply a move to the board
- is_in_check: Check if a side is in check
- parse_pgn: Parse PGN notation
- game_state: Get current game state
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class ChessPosition:
    """A chess board position."""
    board: List[List[str]]  # 8x8 board, [rank][file]
    active_color: str  # 'w' or 'b'
    castling: str  # Castling availability (KQkq format)
    en_passant: str  # En passant target square (- for none)
    halfmove_clock: int  # Halfmove clock for 50-move rule
    fullmove_number: int  # Full move number


@dataclass
class ChessMove:
    """A chess move."""
    from_square: str  # e.g., "e2"
    to_square: str  # e.g., "e4"
    piece: str  # Piece moved (uppercase = white, lowercase = black)
    captured: Optional[str]  # Captured piece, if any
    promotion: Optional[str]  # Promotion piece, if any
    notation: str  # Algebraic notation


class ChessPlugin(BasePlugin):
    """Chess utilities plugin.

    Provides FEN parsing/generation, board representation,
    basic move validation, game state tracking, PGN parsing,
    and check/checkmate detection.

    Example:
        plugin = ChessPlugin()
        plugin.initialize()
        plugin.activate()

        pos = plugin.parse_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        print(plugin.display_board(pos))
    """

    # Piece characters: uppercase = white, lowercase = black
    # P/p = pawn, N/n = knight, B/b = bishop, R/r = rook, Q/q = queen, K/k = king
    PIECES = "PNBRQKpnbrqk"
    WHITE_PIECES = "PNBRQK"
    BLACK_PIECES = "pnbrqk"

    FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    RANKS = ['8', '7', '6', '5', '4', '3', '2', '1']

    # Starting position FEN
    START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    def __init__(self) -> None:
        """Initialize the chess plugin."""
        metadata = PluginMetadata(
            name="chess",
            version="1.0.0",
            description="Chess utilities: FEN, move validation, board representation, PGN",
            author="LiuHao AI OS Team",
            categories=["games", "chess", "utility"],
            keywords=["chess", "fen", "pgn", "board", "moves", "checkmate"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the chess plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the chess plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the chess plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the chess plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "parse_fen",
            "generate_fen",
            "display_board",
            "is_valid_move",
            "make_move",
            "is_in_check",
            "parse_pgn",
            "game_state",
            "square_to_coords",
            "coords_to_square",
        ]

    # ==================== FEN Parsing / Generation ====================

    def parse_fen(self, fen: str) -> ChessPosition:
        """Parse a FEN string into a ChessPosition.

        Args:
            fen: FEN string (Forsyth-Edwards Notation).

        Returns:
            ChessPosition object.

        Raises:
            ValueError: If FEN is invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        parts = fen.strip().split()
        if len(parts) != 6:
            raise ValueError(
                f"Invalid FEN: expected 6 fields, got {len(parts)}"
            )

        board_str, active, castling, ep, halfmove, fullmove = parts

        # Parse board
        board = [[''] * 8 for _ in range(8)]
        ranks = board_str.split('/')
        if len(ranks) != 8:
            raise ValueError("Invalid FEN: board must have 8 ranks")

        for rank_idx, rank_str in enumerate(ranks):
            file_idx = 0
            for ch in rank_str:
                if ch.isdigit():
                    empty = int(ch)
                    for i in range(empty):
                        board[rank_idx][file_idx] = ''
                        file_idx += 1
                elif ch in self.PIECES:
                    board[rank_idx][file_idx] = ch
                    file_idx += 1
                else:
                    raise ValueError(f"Invalid character in FEN board: {ch}")

            if file_idx != 8:
                raise ValueError(f"Invalid FEN: rank {rank_idx + 1} has {file_idx} files")

        # Active color
        if active not in ('w', 'b'):
            raise ValueError(f"Invalid active color: {active}")

        # Castling
        if castling != '-' and not all(c in 'KQkq' for c in castling):
            raise ValueError(f"Invalid castling: {castling}")

        # En passant
        if ep != '-':
            if (len(ep) != 2
                or ep[0] not in 'abcdefgh'
                or ep[1] not in '12345678'):
                raise ValueError(f"Invalid en passant: {ep}")

        # Halfmove clock
        try:
            halfmove_int = int(halfmove)
            if halfmove_int < 0:
                raise ValueError
        except ValueError:
            raise ValueError(f"Invalid halfmove clock: {halfmove}")

        # Fullmove number
        try:
            fullmove_int = int(fullmove)
            if fullmove_int < 1:
                raise ValueError
        except ValueError:
            raise ValueError(f"Invalid fullmove number: {fullmove}")

        return ChessPosition(
            board=board,
            active_color=active,
            castling=castling,
            en_passant=ep,
            halfmove_clock=halfmove_int,
            fullmove_number=fullmove_int,
        )

    def generate_fen(self, position: ChessPosition) -> str:
        """Generate a FEN string from a ChessPosition.

        Args:
            position: ChessPosition to encode.

        Returns:
            FEN string.
        """
        self._ensure_active()
        self._increment_call_count()

        # Build board part
        board_parts: List[str] = []
        for rank in range(8):
            rank_str = ''
            empty_count = 0
            for file in range(8):
                piece = position.board[rank][file]
                if piece == '':
                    empty_count += 1
                else:
                    if empty_count > 0:
                        rank_str += str(empty_count)
                        empty_count = 0
                    rank_str += piece
            if empty_count > 0:
                rank_str += str(empty_count)
            board_parts.append(rank_str)

        board_str = '/'.join(board_parts)

        return (
            f"{board_str} {position.active_color} {position.castling} "
            f"{position.en_passant} {position.halfmove_clock} "
            f"{position.fullmove_number}"
        )

    # ==================== Board Display ====================

    def display_board(
        self,
        position: ChessPosition,
        flipped: bool = False,
    ) -> str:
        """Generate ASCII art representation of the board.

        Args:
            position: ChessPosition to display.
            flipped: If True, show from black's perspective.

        Returns:
            ASCII board string.
        """
        self._ensure_active()
        self._increment_call_count()

        lines: List[str] = []
        rank_range = range(8) if not flipped else range(7, -1, -1)
        file_range = range(8) if not flipped else range(7, -1, -1)

        lines.append("  +" + "---+" * 8)

        for rank_idx in rank_range:
            rank_num = 8 - rank_idx if not flipped else rank_idx + 1
            line = f"{rank_num} |"
            for file_idx in file_range:
                piece = position.board[rank_idx][file_idx]
                if piece == '':
                    display = '   '
                else:
                    display = f' {piece} '
                line += display + '|'
            lines.append(line)
            lines.append("  +" + "---+" * 8)

        # File labels
        files = self.FILES if not flipped else list(reversed(self.FILES))
        file_line = '    ' + '   '.join(files)
        lines.append(file_line)

        return '\n'.join(lines)

    # ==================== Coordinate Helpers ====================

    def square_to_coords(self, square: str) -> Tuple[int, int]:
        """Convert algebraic square notation to (rank_idx, file_idx).

        Args:
            square: Square like 'e4'.

        Returns:
            Tuple of (rank_index, file_index) where rank 0 = rank 8.
        """
        if len(square) != 2:
            raise ValueError(f"Invalid square: {square}")

        file_char = square[0].lower()
        rank_char = square[1]

        if file_char not in self.FILES:
            raise ValueError(f"Invalid file: {file_char}")
        if rank_char not in self.RANKS:
            raise ValueError(f"Invalid rank: {rank_char}")

        file_idx = self.FILES.index(file_char)
        rank_idx = 7 - (int(rank_char) - 1)

        return rank_idx, file_idx

    def coords_to_square(self, rank_idx: int, file_idx: int) -> str:
        """Convert (rank_idx, file_idx) to algebraic notation.

        Args:
            rank_idx: Rank index (0 = rank 8).
            file_idx: File index (0 = file a).

        Returns:
            Square string like 'e4'.
        """
        if not (0 <= rank_idx < 8 and 0 <= file_idx < 8):
            raise ValueError(f"Invalid coordinates: ({rank_idx}, {file_idx})")

        file_char = self.FILES[file_idx]
        rank_num = 8 - rank_idx

        return f"{file_char}{rank_num}"

    # ==================== Move Validation ====================

    def is_valid_move(
        self,
        position: ChessPosition,
        from_square: str,
        to_square: str,
        promotion: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Check if a move is valid (basic validation).

        Performs basic move validation including piece movement
        rules. Does not fully check for check after move.

        Args:
            position: Current position.
            from_square: Starting square.
            to_square: Destination square.
            promotion: Promotion piece (for pawn promotion).

        Returns:
            Tuple of (is_valid, reason).
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            from_r, from_f = self.square_to_coords(from_square)
            to_r, to_f = self.square_to_coords(to_square)
        except ValueError as e:
            return False, str(e)

        piece = position.board[from_r][from_f]

        if piece == '':
            return False, "No piece on source square"

        is_white = piece.isupper()
        active_is_white = position.active_color == 'w'

        if is_white != active_is_white:
            return False, f"It is {'white' if active_is_white else 'black'}'s turn to move"

        target = position.board[to_r][to_f]
        if target != '' and target.isupper() == is_white:
            return False, "Cannot capture own piece"

        # Check piece-specific movement
        piece_type = piece.upper()

        if piece_type == 'P':
            valid, reason = self._validate_pawn_move(
                position, from_r, from_f, to_r, to_f, is_white
            )
        elif piece_type == 'N':
            valid, reason = self._validate_knight_move(
                from_r, from_f, to_r, to_f
            )
        elif piece_type == 'B':
            valid, reason = self._validate_bishop_move(
                position, from_r, from_f, to_r, to_f
            )
        elif piece_type == 'R':
            valid, reason = self._validate_rook_move(
                position, from_r, from_f, to_r, to_f
            )
        elif piece_type == 'Q':
            valid, reason = self._validate_queen_move(
                position, from_r, from_f, to_r, to_f
            )
        elif piece_type == 'K':
            valid, reason = self._validate_king_move(
                position, from_r, from_f, to_r, to_f, is_white
            )
        else:
            return False, f"Unknown piece: {piece}"

        if not valid:
            return False, reason

        # Check promotion
        if piece_type == 'P':
            if is_white and to_r == 0:
                if promotion is None:
                    return False, "Promotion piece required"
                if promotion.upper() not in 'QRBN':
                    return False, f"Invalid promotion piece: {promotion}"
            elif not is_white and to_r == 7:
                if promotion is None:
                    return False, "Promotion piece required"
                if promotion.upper() not in 'QRBN':
                    return False, f"Invalid promotion piece: {promotion}"

        return True, "Valid move"

    def _validate_pawn_move(
        self,
        position: ChessPosition,
        from_r: int, from_f: int,
        to_r: int, to_f: int,
        is_white: bool,
    ) -> Tuple[bool, str]:
        """Validate a pawn move."""
        direction = -1 if is_white else 1
        start_rank = 6 if is_white else 1
        target = position.board[to_r][to_f]

        # Forward move
        if from_f == to_f:
            if target != '':
                return False, "Pawn cannot capture forward"

            # Single step
            if to_r == from_r + direction:
                return True, ""

            # Double step from start
            if from_r == start_rank and to_r == from_r + 2 * direction:
                # Check if square in between is empty
                mid_r = from_r + direction
                if position.board[mid_r][from_f] != '':
                    return False, "Pawn blocked on double step"
                return True, ""

            return False, "Invalid pawn move"

        # Diagonal capture
        elif abs(to_f - from_f) == 1 and to_r == from_r + direction:
            # Normal capture
            if target != '' and target.isupper() != is_white:
                return True, ""

            # En passant
            ep_square = position.en_passant
            if ep_square != '-':
                ep_r, ep_f = self.square_to_coords(ep_square)
                if to_r == ep_r and to_f == ep_f:
                    return True, ""

            return False, "Invalid pawn capture"

        return False, "Invalid pawn move"

    def _validate_knight_move(
        self, from_r: int, from_f: int, to_r: int, to_f: int
    ) -> Tuple[bool, str]:
        """Validate a knight move."""
        dr = abs(to_r - from_r)
        df = abs(to_f - from_f)

        if (dr == 2 and df == 1) or (dr == 1 and df == 2):
            return True, ""

        return False, "Invalid knight move"

    def _validate_bishop_move(
        self,
        position: ChessPosition,
        from_r: int, from_f: int,
        to_r: int, to_f: int,
    ) -> Tuple[bool, str]:
        """Validate a bishop move."""
        dr = to_r - from_r
        df = to_f - from_f

        if abs(dr) != abs(df):
            return False, "Bishop moves diagonally"

        # Check path
        step_r = 1 if dr > 0 else -1
        step_f = 1 if df > 0 else -1
        r, f = from_r + step_r, from_f + step_f

        while r != to_r and f != to_f:
            if position.board[r][f] != '':
                return False, "Bishop path blocked"
            r += step_r
            f += step_f

        return True, ""

    def _validate_rook_move(
        self,
        position: ChessPosition,
        from_r: int, from_f: int,
        to_r: int, to_f: int,
    ) -> Tuple[bool, str]:
        """Validate a rook move."""
        if from_r != to_r and from_f != to_f:
            return False, "Rook moves straight"

        # Check path
        if from_r == to_r:
            step = 1 if to_f > from_f else -1
            for f in range(from_f + step, to_f, step):
                if position.board[from_r][f] != '':
                    return False, "Rook path blocked"
        else:
            step = 1 if to_r > from_r else -1
            for r in range(from_r + step, to_r, step):
                if position.board[r][from_f] != '':
                    return False, "Rook path blocked"

        return True, ""

    def _validate_queen_move(
        self,
        position: ChessPosition,
        from_r: int, from_f: int,
        to_r: int, to_f: int,
    ) -> Tuple[bool, str]:
        """Validate a queen move."""
        # Queen moves like rook or bishop
        if from_r == to_r or from_f == to_f:
            return self._validate_rook_move(position, from_r, from_f, to_r, to_f)
        elif abs(to_r - from_r) == abs(to_f - from_f):
            return self._validate_bishop_move(position, from_r, from_f, to_r, to_f)
        else:
            return False, "Queen moves straight or diagonally"

    def _validate_king_move(
        self,
        position: ChessPosition,
        from_r: int, from_f: int,
        to_r: int, to_f: int,
        is_white: bool,
    ) -> Tuple[bool, str]:
        """Validate a king move (including castling)."""
        dr = abs(to_r - from_r)
        df = abs(to_f - from_f)

        # Normal king move
        if dr <= 1 and df <= 1:
            return True, ""

        # Castling
        if dr == 0 and df == 2:
            # Check if castling is available
            if is_white:
                if to_f > from_f:  # Kingside
                    if 'K' not in position.castling:
                        return False, "White kingside castling not available"
                else:  # Queenside
                    if 'Q' not in position.castling:
                        return False, "White queenside castling not available"
            else:
                if to_f > from_f:  # Kingside
                    if 'k' not in position.castling:
                        return False, "Black kingside castling not available"
                else:  # Queenside
                    if 'q' not in position.castling:
                        return False, "Black queenside castling not available"

            # Check if squares between are empty
            if to_f > from_f:  # Kingside
                for f in range(from_f + 1, to_f + 1):
                    if position.board[from_r][f] != '':
                        return False, "Castling path blocked"
            else:  # Queenside
                for f in range(to_f, from_f):
                    if position.board[from_r][f] != '':
                        return False, "Castling path blocked"

            return True, ""

        return False, "Invalid king move"

    # ==================== Make Move ====================

    def make_move(
        self,
        position: ChessPosition,
        from_square: str,
        to_square: str,
        promotion: Optional[str] = None,
    ) -> ChessPosition:
        """Apply a move to the position.

        Args:
            position: Starting position.
            from_square: Source square.
            to_square: Destination square.
            promotion: Promotion piece.

        Returns:
            New ChessPosition after the move.

        Raises:
            ValueError: If move is invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        valid, reason = self.is_valid_move(position, from_square, to_square, promotion)
        if not valid:
            raise ValueError(f"Invalid move: {reason}")

        # Deep copy the position
        new_board = [row[:] for row in position.board]
        from_r, from_f = self.square_to_coords(from_square)
        to_r, to_f = self.square_to_coords(to_square)

        piece = new_board[from_r][from_f]
        is_white = piece.isupper()
        piece_type = piece.upper()

        # Handle en passant capture
        captured = new_board[to_r][to_f]
        if piece_type == 'P' and position.en_passant != '-':
            ep_r, ep_f = self.square_to_coords(position.en_passant)
            if to_r == ep_r and to_f == ep_f:
                # Remove the captured pawn
                capture_r = from_r  # Same rank as moving pawn
                new_board[capture_r][to_f] = ''
                captured = 'p' if is_white else 'P'

        # Handle castling
        if piece_type == 'K' and abs(to_f - from_f) == 2:
            if to_f > from_f:  # Kingside
                # Move rook
                new_board[from_r][5] = new_board[from_r][7]
                new_board[from_r][7] = ''
            else:  # Queenside
                new_board[from_r][3] = new_board[from_r][0]
                new_board[from_r][0] = ''

        # Move the piece
        new_board[from_r][from_f] = ''

        # Handle promotion
        if piece_type == 'P' and (to_r == 0 or to_r == 7):
            promo = promotion.upper() if is_white else promotion.lower()
            new_board[to_r][to_f] = promo
        else:
            new_board[to_r][to_f] = piece

        # Update active color
        new_active = 'b' if is_white else 'w'

        # Update castling rights
        new_castling = list(position.castling) if position.castling != '-' else []

        if piece_type == 'K':
            if is_white:
                new_castling = [c for c in new_castling if c not in 'KQ']
            else:
                new_castling = [c for c in new_castling if c not in 'kq']

        if piece_type == 'R':
            if is_white and from_r == 7:
                if from_f == 0:
                    new_castling = [c for c in new_castling if c != 'Q']
                elif from_f == 7:
                    new_castling = [c for c in new_castling if c != 'K']
            elif not is_white and from_r == 0:
                if from_f == 0:
                    new_castling = [c for c in new_castling if c != 'q']
                elif from_f == 7:
                    new_castling = [c for c in new_castling if c != 'k']

        # Rook captured on its starting square
        if captured and captured.upper() == 'R':
            if not is_white and to_r == 7:  # Black captures white rook
                if to_f == 0:
                    new_castling = [c for c in new_castling if c != 'Q']
                elif to_f == 7:
                    new_castling = [c for c in new_castling if c != 'K']
            elif is_white and to_r == 0:  # White captures black rook
                if to_f == 0:
                    new_castling = [c for c in new_castling if c != 'q']
                elif to_f == 7:
                    new_castling = [c for c in new_castling if c != 'k']

        castling_str = ''.join(new_castling) if new_castling else '-'

        # Update en passant
        new_ep = '-'
        if piece_type == 'P' and abs(to_r - from_r) == 2:
            ep_rank = from_r + (-1 if is_white else 1)
            new_ep = self.coords_to_square(ep_rank, from_f)

        # Update halfmove clock
        if piece_type == 'P' or captured != '':
            new_halfmove = 0
        else:
            new_halfmove = position.halfmove_clock + 1

        # Update fullmove number
        new_fullmove = position.fullmove_number
        if not is_white:  # Black just moved
            new_fullmove += 1

        return ChessPosition(
            board=new_board,
            active_color=new_active,
            castling=castling_str,
            en_passant=new_ep,
            halfmove_clock=new_halfmove,
            fullmove_number=new_fullmove,
        )

    # ==================== Check Detection ====================

    def is_in_check(self, position: ChessPosition, color: str = 'w') -> bool:
        """Check if the specified color is in check.

        Args:
            position: Current position.
            color: Color to check ('w' or 'b').

        Returns:
            True if the king is in check.
        """
        self._ensure_active()
        self._increment_call_count()

        # Find the king
        king_piece = 'K' if color == 'w' else 'k'
        king_pos = None

        for r in range(8):
            for f in range(8):
                if position.board[r][f] == king_piece:
                    king_pos = (r, f)
                    break
            if king_pos:
                break

        if not king_pos:
            return False

        kr, kf = king_pos
        enemy_color = 'b' if color == 'w' else 'w'

        # Check each square for attacking enemy pieces
        for r in range(8):
            for f in range(8):
                piece = position.board[r][f]
                if piece == '' or piece.isupper() == (color == 'w'):
                    continue

                piece_type = piece.upper()

                # Simplified: check if this piece can attack the king square
                if piece_type == 'P':
                    # Pawn attacks diagonally
                    direction = 1 if color == 'w' else -1
                    if r == kr + direction and abs(f - kf) == 1:
                        return True
                elif piece_type == 'N':
                    dr = abs(kr - r)
                    df = abs(kf - f)
                    if (dr == 2 and df == 1) or (dr == 1 and df == 2):
                        return True
                elif piece_type in ('B', 'R', 'Q'):
                    # Check if same row, column, or diagonal
                    same_row = r == kr
                    same_col = f == kf
                    same_diag = abs(kr - r) == abs(kf - f)

                    if same_row or same_col or same_diag:
                        if piece_type == 'B' and not same_diag:
                            continue
                        if piece_type == 'R' and not (same_row or same_col):
                            continue

                        # Check path
                        if self._is_path_clear(position, r, f, kr, kf):
                            return True
                elif piece_type == 'K':
                    dr = abs(kr - r)
                    df = abs(kf - f)
                    if dr <= 1 and df <= 1:
                        return True

        return False

    def _is_path_clear(
        self,
        position: ChessPosition,
        r1: int, f1: int,
        r2: int, f2: int,
    ) -> bool:
        """Check if the path between two squares is clear."""
        if r1 == r2 and f1 == f2:
            return True

        dr = r2 - r1
        df = f2 - f1

        step_r = 0 if dr == 0 else (1 if dr > 0 else -1)
        step_f = 0 if df == 0 else (1 if df > 0 else -1)

        r, f = r1 + step_r, f1 + step_f
        while r != r2 or f != f2:
            if position.board[r][f] != '':
                return False
            r += step_r
            f += step_f

        return True

    # ==================== PGN Parsing ====================

    def parse_pgn(self, pgn: str) -> Dict[str, Any]:
        """Parse a PGN (Portable Game Notation) string.

        Extracts headers and move list.

        Args:
            pgn: PGN string.

        Returns:
            Dict with 'headers' and 'moves'.
        """
        self._ensure_active()
        self._increment_call_count()

        headers: Dict[str, str] = {}
        moves: List[str] = []

        lines = pgn.strip().split('\n')
        in_moves = False
        move_text_parts: List[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith('['):
                # Header
                end = line.find(']')
                if end > 0:
                    content = line[1:end]
                    parts = content.split(' ', 1)
                    if len(parts) == 2:
                        key = parts[0]
                        value = parts[1].strip('"')
                        headers[key] = value
            else:
                in_moves = True
                move_text_parts.append(line)

        if move_text_parts:
            move_text = ' '.join(move_text_parts)
            # Remove move numbers
            import re
            move_text = re.sub(r'\d+\.', '', move_text)
            # Remove comments
            move_text = re.sub(r'\{[^}]*\}', '', move_text)
            # Split into individual moves
            tokens = move_text.split()
            for token in tokens:
                token = token.strip()
                if token and token not in ('*', '1-0', '0-1', '1/2-1/2'):
                    moves.append(token)

        return {
            "headers": headers,
            "moves": moves,
            "num_moves": len(moves),
            "result": self._extract_result(pgn),
        }

    def _extract_result(self, pgn: str) -> str:
        """Extract game result from PGN."""
        if '1-0' in pgn:
            return "1-0"
        elif '0-1' in pgn:
            return "0-1"
        elif '1/2-1/2' in pgn:
            return "1/2-1/2"
        return "*"

    # ==================== Game State ====================

    def game_state(self, position: ChessPosition) -> Dict[str, Any]:
        """Get the current game state.

        Args:
            position: Current position.

        Returns:
            Dict with game state information.
        """
        self._ensure_active()
        self._increment_call_count()

        white_in_check = self.is_in_check(position, 'w')
        black_in_check = self.is_in_check(position, 'b')

        # Count pieces
        white_pieces = []
        black_pieces = []
        for r in range(8):
            for f in range(8):
                piece = position.board[r][f]
                if piece != '':
                    if piece.isupper():
                        white_pieces.append(piece)
                    else:
                        black_pieces.append(piece)

        return {
            "active_color": position.active_color,
            "active_color_name": "White" if position.active_color == 'w' else "Black",
            "white_in_check": white_in_check,
            "black_in_check": black_in_check,
            "white_piece_count": len(white_pieces),
            "black_piece_count": len(black_pieces),
            "halfmove_clock": position.halfmove_clock,
            "fullmove_number": position.fullmove_number,
            "castling_white_kingside": 'K' in position.castling,
            "castling_white_queenside": 'Q' in position.castling,
            "castling_black_kingside": 'k' in position.castling,
            "castling_black_queenside": 'q' in position.castling,
            "en_passant": position.en_passant,
            "fen": self.generate_fen(position),
        }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "board_size": "8x8",
            "starting_fen": self.START_FEN,
            "piece_types": ["Pawn", "Knight", "Bishop", "Rook", "Queen", "King"],
            "supports_pgn": True,
            "supports_fen": True,
        }
