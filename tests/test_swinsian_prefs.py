"""The preferences write path, which is where user data can be lost.

`export_domain` and `import_domain` are the only two seams that touch the real preferences, so
they are monkeypatched here and nothing in this file goes near Swinsian.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from swinsian import prefs  # noqa: E402


@pytest.fixture
def fake_domain(monkeypatch):
    """Stand in for the real preferences domain, with Swinsian reported as not running."""
    domain = {
        'EqualizerPresets': {'Existing': b'old-payload'},
        'SomeUnrelatedSetting': 'do not lose me',
    }
    monkeypatch.setattr(prefs, 'is_running', lambda: False)
    monkeypatch.setattr(prefs, 'export_domain', lambda: {**domain,
                                                        'EqualizerPresets': dict(domain['EqualizerPresets'])})
    monkeypatch.setattr(prefs, 'import_domain', lambda new: domain.update(new))
    return domain


def test_write_merges_into_existing_presets(fake_domain):
    prefs.write_presets({'New': b'new-payload'})
    assert fake_domain['EqualizerPresets'] == {'Existing': b'old-payload', 'New': b'new-payload'}


def test_write_preserves_unrelated_settings(fake_domain):
    prefs.write_presets({'New': b'new-payload'})
    assert fake_domain['SomeUnrelatedSetting'] == 'do not lose me'


def test_write_overwrites_a_preset_of_the_same_name(fake_domain):
    prefs.write_presets({'Existing': b'replacement'})
    assert fake_domain['EqualizerPresets'] == {'Existing': b'replacement'}


def test_replace_all_drops_presets_not_being_written(fake_domain):
    prefs.write_presets({'New': b'new-payload'}, replace_all=True)
    assert fake_domain['EqualizerPresets'] == {'New': b'new-payload'}
    assert fake_domain['SomeUnrelatedSetting'] == 'do not lose me'


def test_write_refuses_while_swinsian_is_running(fake_domain, monkeypatch):
    """Swinsian rewrites the whole preset dict from memory, so a write now would be discarded."""
    monkeypatch.setattr(prefs, 'is_running', lambda: True)
    with pytest.raises(prefs.SwinsianRunning, match='Quit it first'):
        prefs.write_presets({'New': b'new-payload'})
    assert fake_domain['EqualizerPresets'] == {'Existing': b'old-payload'}


def test_restore_refuses_while_swinsian_is_running(monkeypatch, tmp_path):
    monkeypatch.setattr(prefs, 'is_running', lambda: True)
    with pytest.raises(prefs.SwinsianRunning):
        prefs.restore(tmp_path / 'backup.xml')


def test_defaults_failure_surfaces_the_error_output(monkeypatch):
    class Failed:
        returncode = 1
        stdout = b''
        stderr = b'Domain com.example does not exist'

    monkeypatch.setattr(prefs.subprocess, 'run', lambda *a, **kw: Failed())
    with pytest.raises(RuntimeError, match='does not exist'):
        prefs.export_domain()
