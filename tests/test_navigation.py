from valmiki.navigation import ReadingPosition, build_navigation


def test_moves_between_slokas_in_the_same_sarga():
    navigation = build_navigation(
        ReadingPosition(2, 70, 2),
        sloka_count=30,
        previous_sarga=None,
        next_sarga=None,
    )

    assert navigation.previous == ReadingPosition(2, 70, 1)
    assert navigation.next == ReadingPosition(2, 70, 3)


def test_moves_to_the_next_available_sarga():
    navigation = build_navigation(
        ReadingPosition(2, 70, 30),
        sloka_count=30,
        previous_sarga=None,
        next_sarga=(2, 72),
    )

    assert navigation.next == ReadingPosition(2, 72, 1)


def test_moves_to_the_last_sloka_of_the_previous_sarga():
    navigation = build_navigation(
        ReadingPosition(2, 72, 1),
        sloka_count=100,
        previous_sarga=(2, 70, 30),
        next_sarga=None,
    )

    assert navigation.previous == ReadingPosition(2, 70, 30)
