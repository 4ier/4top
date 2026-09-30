"""4top's own small state: what the person has already looked at survives a restart."""
from fourtop import state
from fourtop.state import StateStore


def test_seen_survives_a_restart_and_keeps_only_recent_looks(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SEEN_LIMIT", 2)
    StateStore(tmp_path).save_seen({"local:a": "2026-09-01", "mac:b": "2026-09-03",
                                    "local:c": "2026-09-02"})
    assert StateStore(tmp_path).load_seen() == {"local:c": "2026-09-02", "mac:b": "2026-09-03"}


def test_unreadable_seen_is_empty(tmp_path):
    (tmp_path / "seen.json").write_text("{not json")
    assert StateStore(tmp_path).load_seen() == {}
