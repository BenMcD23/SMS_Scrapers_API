"""The lesson QR codes are printed on slides, so they must never change. These
tests are the lock: they fail if a code is edited, removed or reused, and if a
lesson has no code."""

import hashlib

from core.lesson_codes import LESSON_BY_CODE, LESSON_CODE_ALPHABET, LESSON_CODES
from core.theory_lessons import THEORY_LESSONS

# Fingerprint of every (subject, lesson) → code pair already handed out. If this
# fails you've changed a code that is on someone's slides: put it back. Adding
# codes for a new lesson is fine; update the fingerprint to include them.
PUBLISHED = "4991c9398b4e1845e78c2308ed32b4fc5b79b3fffeb5cc250e47c76ce71459a3"


def _fingerprint(codes) -> str:
    lines = (f"{key}:{part}={code}" for (key, part), code in sorted(codes.items()))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def test_no_published_code_has_changed():
    assert _fingerprint(LESSON_CODES) == PUBLISHED


def test_every_lesson_in_the_catalogue_has_a_code():
    wanted = {(s.key, p) for s in THEORY_LESSONS for p in range(1, s.parts + 1)}
    missing = wanted - set(LESSON_CODES)
    assert not missing, f"Add codes for {sorted(missing)} to core/lesson_codes.py"


def test_no_two_lessons_share_a_code():
    assert len(LESSON_BY_CODE) == len(LESSON_CODES)


def test_codes_are_long_enough_not_to_guess_and_easy_to_read():
    for code in LESSON_CODES.values():
        assert len(code) == 8, code
        assert set(code) <= set(LESSON_CODE_ALPHABET), code
