from app.single_instance import SingleInstanceLock, SingleInstanceLockError


def test_single_instance_lock_rejects_second_local_acquire(tmp_path):
    lock_path = tmp_path / "threadbot.lock"

    with SingleInstanceLock(lock_path):
        try:
            with SingleInstanceLock(lock_path):
                raise AssertionError("second lock acquire must fail")
        except SingleInstanceLockError as exc:
            assert "already running" in str(exc)


def test_single_instance_lock_releases_after_context(tmp_path):
    lock_path = tmp_path / "threadbot.lock"

    with SingleInstanceLock(lock_path):
        pass

    with SingleInstanceLock(lock_path):
        pass
