"""`.env` is read at start; real environment variables win."""

import os

from app import config, paths


def test_a_value_in_env_file_changes_the_data_folder(tmp_path, monkeypatch):
    target = tmp_path / "from-dotenv"
    env = tmp_path / ".env"
    env.write_text(f'# my settings\nexport {paths.DATA_DIR_ENV}="{target}"\nAUTH_USER=me\nnot a setting\n', encoding="utf-8")
    monkeypatch.delenv(paths.DATA_DIR_ENV)
    monkeypatch.delenv("AUTH_USER", raising=False)
    loaded = config.load_env_file(env)
    try:
        assert set(loaded) == {paths.DATA_DIR_ENV, "AUTH_USER"}
        assert paths.data_dir() == target
        assert os.environ["AUTH_USER"] == "me"
    finally:
        for key in loaded:
            os.environ.pop(key, None)


def test_a_real_environment_variable_wins_over_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SYNC_INTERVAL_HOURS=1\n", encoding="utf-8")
    monkeypatch.setenv("SYNC_INTERVAL_HOURS", "12")
    assert config.load_env_file(env) == []
    assert os.environ["SYNC_INTERVAL_HOURS"] == "12"


def test_no_env_file_is_fine(tmp_path):
    assert config.load_env_file(tmp_path / "missing") == []


def test_env_example_lists_only_settings_the_app_reads():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    names = set(re.findall(r"^#?\s*([A-Z_]+)=", (root / ".env.example").read_text(encoding="utf-8"), re.M))
    source = "".join(p.read_text(encoding="utf-8") for p in (root / "app").rglob("*.py"))
    assert names and all(name in source for name in names), names
