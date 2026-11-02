from uuid import UUID

from healthops.assistant.limits import RateLimiter

A = UUID("00000000-0000-4000-8000-000000000001")
B = UUID("00000000-0000-4000-8000-000000000002")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_limit_is_per_user_and_slides_after_a_minute():
    clock = Clock()
    limiter = RateLimiter(per_minute=2, clock=clock)
    assert limiter.allow(A) and limiter.allow(A)
    assert not limiter.allow(A)
    assert limiter.allow(B)  # another user is not affected
    assert limiter.retry_after_seconds(A) == 61
    clock.now += 59
    assert not limiter.allow(A)
    clock.now += 1.5
    assert limiter.allow(A)  # the first event aged out


def test_zero_disables_the_limiter():
    limiter = RateLimiter(per_minute=0)
    assert all(limiter.allow(A) for _ in range(50))
