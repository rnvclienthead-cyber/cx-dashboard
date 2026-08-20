"""Режим темы Авто/Согласование/Менеджер — отдельно по площадкам."""
from unittest.mock import MagicMock

from backend.routers import chat_playbook


def test_get_zone_prefers_mp_row_over_legacy():
    db = MagicMock()
    # 1) theme active + legacy zone=auto
    # 2) mp-specific ozon = human
    db.execute.return_value.fetchone.side_effect = [
        ("auto", True),
        ("human",),
    ]
    assert chat_playbook.get_zone(db, "vozvrat", mp="ozon") == "human"


def test_get_zone_falls_back_to_legacy_when_no_mp_row():
    db = MagicMock()
    db.execute.return_value.fetchone.side_effect = [
        ("draft", True),
        None,
    ]
    assert chat_playbook.get_zone(db, "zapchasti", mp="ym") == "draft"


def test_get_zone_inactive_theme_is_human():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = ("auto", False)
    assert chat_playbook.get_zone(db, "teh_sboi", mp="wb") == "human"
