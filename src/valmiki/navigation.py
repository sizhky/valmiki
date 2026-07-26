"""Reading-position navigation rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadingPosition:
    kanda: int
    sarga: int
    sloka: int

    @property
    def path(self) -> str:
        return f"/kanda/{self.kanda}/sarga/{self.sarga}/sloka/{self.sloka}"


@dataclass(frozen=True)
class Navigation:
    previous: ReadingPosition | None
    next: ReadingPosition | None


def build_navigation(
    position: ReadingPosition,
    sloka_count: int,
    previous_sarga: tuple[int, int, int] | None,
    next_sarga: tuple[int, int] | None,
) -> Navigation:
    """Return both adjacent reading positions from one small interface."""
    if position.sloka > 1:
        previous = ReadingPosition(position.kanda, position.sarga, position.sloka - 1)
    elif previous_sarga:
        previous = ReadingPosition(*previous_sarga)
    else:
        previous = None

    if position.sloka < sloka_count:
        next_position = ReadingPosition(position.kanda, position.sarga, position.sloka + 1)
    elif next_sarga:
        next_position = ReadingPosition(*next_sarga, 1)
    else:
        next_position = None
    return Navigation(previous=previous, next=next_position)
