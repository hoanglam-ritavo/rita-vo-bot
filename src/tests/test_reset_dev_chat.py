"""Development reset guardrails; never connects to PostgreSQL."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from src.cli import reset_dev_chat as reset
from src.database.runtime import run_async


@pytest.mark.parametrize('value',[None,'not-a-url','postgresql://localhost/rita','postgresql://remote/rita_dev','postgresql://localhost/production','sqlite:///rita_dev','postgresql://localhost/rita_dev?host=remote'])
def test_reset_rejects_implicit_or_nonlocal_development_target(value):
    with pytest.raises(ValueError):reset.development_url(value)


def test_dry_run_does_not_mutate_and_apply_requires_explicit_confirmation(monkeypatch,capsys):
    work=AsyncMock(return_value={'count':0})
    monkeypatch.setattr(reset,'reset_database',work)
    env={'CHAT_RESET_BOT_DATABASE_URL':'postgresql://localhost/bot_dev',
         'CHAT_RESET_BACKEND_DATABASE_URL':'postgresql://localhost/backend_dev'}
    run_async(reset.execute(SimpleNamespace(apply=False),env))
    assert len(work.await_args_list)==2
    assert all(not call.kwargs.get('apply',False) for call in work.await_args_list)
    with pytest.raises(ValueError):
        run_async(reset.execute(SimpleNamespace(apply=True,confirm='wrong',services_stopped=True),env))
    assert work.await_count==2
    assert 'postgresql' not in capsys.readouterr().out


def test_reset_only_owns_chat_tables():
    assert reset.BOT_TABLES==('chat_messages','chat_turns','chat_conversations')
    assert reset.BACKEND_TABLES==('chat_purge_jobs','chat_rooms')
