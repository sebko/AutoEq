"""Level offset policy and bass tier arithmetic."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from autoeq.frequency_response import FrequencyResponse  # noqa: E402
from swinsian.augraphiceq import GAIN_MAX, GAIN_MIN  # noqa: E402
from swinsian.fit import CLIP_EPSILON, _level_offset  # noqa: E402
from swinsian.tiers import MAX_GAIN_MARGIN, TIERS, Tier, ad_hoc_tier, resolve_tiers  # noqa: E402

HEADROOM = 0.2


def curve(highest, lowest):
    """A response whose equalization spans exactly [lowest, highest] across the band centres."""
    frequency = np.array([20.0, 1000.0, 20000.0])
    return FrequencyResponse(
        name='synthetic', frequency=frequency,
        equalization=np.array([highest, (highest + lowest) / 2, lowest]))


def test_none_mode_never_shifts():
    assert _level_offset(curve(30.0, -30.0), 'none', HEADROOM) == 0.0


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match='unknown normalize mode'):
        _level_offset(curve(1.0, -1.0), 'louder', HEADROOM)


@pytest.mark.parametrize('highest,lowest', [(6.0, -2.0), (0.0, -15.0), (12.0, -5.0)])
def test_peak_mode_pins_the_peak_below_unity(highest, lowest):
    offset = _level_offset(curve(highest, lowest), 'peak', HEADROOM)
    assert highest + offset == pytest.approx(-HEADROOM)


def test_auto_matches_peak_when_the_span_fits():
    fr = curve(6.0, -8.0)  # span 14, comfortably inside the 20 dB of downward range
    assert _level_offset(fr, 'auto', HEADROOM) == _level_offset(fr, 'peak', HEADROOM)


def test_auto_keeps_the_trough_clear_of_the_floor_when_the_span_does_not_fit():
    highest, lowest = 18.0, -12.0  # span 30 > 20, so peak mode would sink the trough
    fr = curve(highest, lowest)
    assert lowest + _level_offset(fr, 'peak', HEADROOM) < GAIN_MIN

    offset = _level_offset(fr, 'auto', HEADROOM)
    assert lowest + offset == pytest.approx(GAIN_MIN + CLIP_EPSILON)
    assert highest + offset <= GAIN_MAX


def test_auto_trades_boost_for_shape_only_as_far_as_needed():
    """The boost auto accepts should be exactly the span's overflow past the downward range."""
    highest, lowest = 18.0, -12.0
    offset = _level_offset(curve(highest, lowest), 'auto', HEADROOM)
    overflow = (highest - lowest) - (-GAIN_MIN)
    assert highest + offset == pytest.approx(overflow + CLIP_EPSILON)


def test_auto_respects_the_ceiling_when_the_span_exceeds_the_whole_range():
    highest, lowest = 25.0, -20.0  # span 45 > 40; nothing fits, the ceiling has to win
    offset = _level_offset(curve(highest, lowest), 'auto', HEADROOM)
    assert highest + offset == pytest.approx(GAIN_MAX)
    assert lowest + offset < GAIN_MIN  # documented: the floor clips in this case


def test_auto_never_inverts_the_clamps():
    for highest in range(-20, 30, 5):
        for span in range(0, 60, 5):
            offset = _level_offset(curve(float(highest), highest - span), 'auto', HEADROOM)
            assert np.isfinite(offset)
            assert highest + offset <= GAIN_MAX + 1e-9


def test_tier_max_gain_never_falls_below_the_autoeq_default():
    """A negative shelf must not drag max_gain down, or equalize() clips the whole curve flat."""
    assert Tier('x', 'X', -8.0, '').max_gain == MAX_GAIN_MARGIN
    assert Tier('x', 'X', 0.0, '').max_gain == MAX_GAIN_MARGIN
    assert Tier('x', 'X', 18.0, '').max_gain == 18.0 + MAX_GAIN_MARGIN


def test_shipped_tiers_leave_headroom_above_their_shelf():
    for tier in TIERS:
        assert tier.max_gain > tier.bass_gain


def test_ad_hoc_tier_parses_the_full_autoeq_syntax():
    assert ad_hoc_tier('6') == Tier('bass6', '6dB', 6.0, 'ad hoc bass boost 6', 105.0, 0.7)

    with_fc = ad_hoc_tier('9.5,150,0.69')
    assert (with_fc.bass_gain, with_fc.bass_fc, with_fc.bass_q) == (9.5, 150.0, 0.69)

    assert ad_hoc_tier('6,150').bass_q == 0.7  # Q defaults when omitted


def test_ad_hoc_tier_rejects_what_it_cannot_honour():
    with pytest.raises(ValueError, match='at most'):
        ad_hoc_tier('6,150,0.7,extra')
    with pytest.raises(ValueError, match='expects numbers'):
        ad_hoc_tier('loud')


def test_resolve_tiers():
    assert resolve_tiers(['all']) == TIERS
    assert [t.key for t in resolve_tiers(['max', 'harman'])] == ['max', 'harman']
    with pytest.raises(ValueError, match='unknown tier'):
        resolve_tiers(['thumpy'])
