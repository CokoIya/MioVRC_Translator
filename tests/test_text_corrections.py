from __future__ import annotations

import json

import pytest

from src.asr import text_corrections


def _isolate_dictionary_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(text_corrections, "writable_app_dir", lambda: tmp_path)
    monkeypatch.setattr(text_corrections, "bundled_dictionary_paths", lambda: [])


def test_upsert_user_dictionary_entry_creates_entry(tmp_path, monkeypatch):
    _isolate_dictionary_dir(tmp_path, monkeypatch)

    result = text_corrections.upsert_user_dictionary_entry(
        "VRChat",
        "VR chat, VR C at\nVRCat",
    )

    path = text_corrections.user_dictionary_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert result["created"] is True
    assert result["pattern_count"] == 3
    assert payload["entries"] == [
        {
            "replacement": "VRChat",
            "patterns": ["VR chat", "VR C at", "VRCat"],
            "mode": "substring",
        }
    ]


def test_upsert_user_dictionary_entry_appends_to_existing_replacement(tmp_path, monkeypatch):
    _isolate_dictionary_dir(tmp_path, monkeypatch)
    path = text_corrections.user_dictionary_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "user",
                "description": "User overrides for ASR corrections.",
                "entries": [
                    {
                        "replacement": "VRChat",
                        "patterns": ["VR chat"],
                        "mode": "word",
                    },
                    {
                        "replacement": "VRChat",
                        "patterns": ["VR Cat"],
                        "mode": "substring",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = text_corrections.upsert_user_dictionary_entry(
        "VRChat",
        "VR Chat, VR Cat, V R Chat",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert result["created"] is False
    assert result["pattern_count"] == 4
    assert payload["entries"] == [
        {
            "replacement": "VRChat",
            "patterns": ["VR chat", "VR Cat", "VR Chat", "V R Chat"],
            "mode": "word",
        }
    ]


def test_user_dictionary_entries_are_applied(tmp_path, monkeypatch):
    _isolate_dictionary_dir(tmp_path, monkeypatch)
    text_corrections.upsert_user_dictionary_entry("MioCustomTerm", "me oh custom")

    corrector = text_corrections.LayeredASRCorrector(
        {"asr": {"correction": {"enabled": True}}}
    )

    assert corrector.apply("say me oh custom now") == "say MioCustomTerm now"


def test_upsert_user_dictionary_entry_keeps_invalid_json_intact(tmp_path, monkeypatch):
    _isolate_dictionary_dir(tmp_path, monkeypatch)
    path = text_corrections.user_dictionary_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = '{"version": 1,'
    path.write_text(original, encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid JSON"):
        text_corrections.upsert_user_dictionary_entry("VRChat", "VR chat")

    assert path.read_text(encoding="utf-8") == original
