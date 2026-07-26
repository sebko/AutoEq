import plistlib
import random
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from swinsian import augraphiceq as au  # noqa: E402
from swinsian.config import load_band_config  # noqa: E402


CAPTURED_STATE = Path(__file__).parent / 'data' / 'swinsian_equalizer_manual_settings.plist'


def _live_manual_settings():
    """Swinsian's current 'manual' equalizer state.

    Absent whenever a named preset is active rather than manual settings, so this is a bonus
    check on top of the captured fixture, never the only one.
    """
    result = subprocess.run(
        ['/usr/bin/defaults', 'export', 'com.swinsian.Swinsian', '-'],
        capture_output=True)
    if result.returncode != 0:
        return None
    domain = plistlib.loads(result.stdout)
    return domain.get('EqualizerManualSettings')


@pytest.mark.parametrize('source', ['captured', 'live'])
def test_reencodes_swinsian_written_state_byte_for_byte(source):
    """The decisive proof of the blob layout: rebuild state Swinsian wrote and compare bytes.

    The fixture is a real payload captured from Swinsian 3.0.8, so this holds without Swinsian
    installed; the live variant additionally catches a format change in a future version.
    """
    if source == 'captured':
        data = CAPTURED_STATE.read_bytes()
    else:
        data = _live_manual_settings()
        if data is None:
            pytest.skip('Swinsian is currently using a named preset, not manual settings')
    classinfo = au.parse_classinfo(data)
    blob = classinfo['data']
    params = au.decode_param_blob(blob)

    assert len(blob) == au.CLASSINFO_BLOB_LEN
    assert sorted(params) == list(range(31)) + [au.NUM_BANDS_PARAM_ID]

    num_bands = int(classinfo['Num EQ Bands'])
    gains = [params[i] for i in range(31)]
    assert au.encode_param_blob(gains, num_bands=num_bands) == blob


def test_blob_round_trip_for_random_gains():
    rng = random.Random(20250726)
    gains = [round(rng.uniform(au.GAIN_MIN, au.GAIN_MAX), 3) for _ in range(31)]
    params = au.decode_param_blob(au.encode_param_blob(gains))
    assert [params[i] for i in range(31)] == [au.f32(g) for g in gains]


def test_blob_length_and_parameter_order():
    blob = au.encode_param_blob([0.0] * 31)
    assert len(blob) == au.CLASSINFO_BLOB_LEN
    assert list(au.decode_param_blob(blob)) == list(range(31)) + [au.NUM_BANDS_PARAM_ID]


@pytest.mark.parametrize('num_bands,flag', [(31, 1.0), (10, 0.0)])
def test_band_count_is_consistent_across_parameter_and_key(num_bands, flag):
    classinfo = au.build_classinfo([0.0] * 31, num_bands=num_bands)
    assert classinfo['Num EQ Bands'] == num_bands
    assert au.decode_param_blob(classinfo['data'])[au.NUM_BANDS_PARAM_ID] == flag


def test_rejects_out_of_range_gain():
    with pytest.raises(ValueError, match='outside'):
        au.encode_param_blob([0.0] * 30 + [20.5])


def test_rejects_wrong_gain_count():
    with pytest.raises(ValueError, match='expected 31 gains'):
        au.encode_param_blob([0.0] * 30)


def test_rejects_unknown_band_count():
    with pytest.raises(ValueError, match='must be 10 or 31'):
        au.encode_param_blob([0.0] * 31, num_bands=15)


def test_classinfo_serialization_round_trip():
    classinfo = au.build_classinfo([1.5] * 31, name='Test')
    data = au.serialize_classinfo(classinfo)
    assert data.startswith(b'bplist00')
    assert au.parse_classinfo(data) == classinfo


def test_parse_rejects_foreign_audio_unit():
    classinfo = au.build_classinfo([0.0] * 31)
    classinfo['subtype'] = 0x64656C79  # 'dely', AUDelay
    with pytest.raises(ValueError, match='not AUGraphicEQ'):
        au.parse_classinfo(au.serialize_classinfo(classinfo))


def test_gains_from_preset_data():
    gains = [round(-10.0 + i * 0.5, 2) for i in range(31)]
    data = au.serialize_classinfo(au.build_classinfo(gains))
    decoded, num_bands = au.gains_from_preset_data(data)
    assert num_bands == 31
    assert decoded == [au.f32(g) for g in gains]


def test_band_config_matches_audio_unit_centers():
    """Guards against the YAML config and the AU parameter table drifting apart."""
    config = load_band_config()
    assert len(au.ISO_CENTERS_31) == 31
    assert [f['fc'] for f in config['filters']] == list(au.ISO_CENTERS_31)
    assert config['filter_defaults']['min_gain'] == au.GAIN_MIN
    assert config['filter_defaults']['max_gain'] == au.GAIN_MAX
