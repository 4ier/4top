"""The update notice: which version to offer, how often to ask, and never an error."""
from fourtop.update import CHECK_SECONDS, check, newest, version_key


def test_versions_order_like_pep_440_for_our_own_strings():
    assert version_key("0.2.0a5") < version_key("0.2.0a10") < version_key("0.2.0b1")
    assert version_key("0.2.0rc1") < version_key("0.2.0") < version_key("0.2.1a1")
    assert version_key("not a version") is None


def test_pre_releases_are_offered_only_to_pre_release_users():
    assert newest(["0.2.0a4", "0.2.0a6"], "0.2.0a5") == "0.2.0a6"
    assert newest(["0.2.1a1"], "0.2.0") is None
    assert newest(["0.2.1a1", "0.2.1"], "0.2.0") == "0.2.1"
    assert newest(["0.2.0a5"], "0.2.0a5") is None


def test_pypi_is_asked_at_most_once_a_day(tmp_path):
    calls, clock = [], [1000.0]

    def fetch():
        calls.append(1)
        return ["0.2.0a4", "99.0.0a1"]
    notice = check(tmp_path, fetch=fetch, now=lambda: clock[0])
    assert notice and notice.latest == "99.0.0a1" and notice.command
    check(tmp_path, fetch=fetch, now=lambda: clock[0] + 60)
    assert len(calls) == 1
    clock[0] += CHECK_SECONDS + 1
    check(tmp_path, fetch=fetch, now=lambda: clock[0])
    assert len(calls) == 2


def test_an_unreachable_index_says_nothing(tmp_path):
    def offline():
        raise OSError("no network")
    assert check(tmp_path, fetch=offline) is None
