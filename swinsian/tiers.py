"""Bass boost tiers.

Each tier reconstitutes the Harman over-ear 2018 bass shelf at a different strength. The
reference tier is AutoEq's own value; the rest step up from there.
"""

from typing import NamedTuple, Sequence

BASS_BOOST_FC = 105.0
BASS_BOOST_Q = 0.7

# max_gain must exceed the shelf gain or FrequencyResponse.equalize clips the boost itself;
# AutoEq's default of 6 dB would flatten everything above the reference tier.
MAX_GAIN_MARGIN = 6.0


class Tier(NamedTuple):
    key: str
    label: str
    bass_gain: float
    note: str

    @property
    def max_gain(self) -> float:
        return self.bass_gain + MAX_GAIN_MARGIN


TIERS = (
    Tier('harman', 'Harman', 6.0, 'AutoEq reference bass shelf'),
    Tier('warm', 'Warm', 9.0, '+3 dB, roughly one standard deviation of listener preference'),
    Tier('heavy', 'Heavy', 12.0, '+6 dB, top of the bass preferring cluster'),
    Tier('max', 'MAX', 18.0, '+12 dB, at the audio unit\'s structural ceiling; treble bands clip'),
)

TIERS_BY_KEY = {tier.key: tier for tier in TIERS}


def resolve_tiers(keys: Sequence[str]) -> Sequence[Tier]:
    if len(keys) == 1 and keys[0] == 'all':
        return TIERS
    unknown = [key for key in keys if key not in TIERS_BY_KEY]
    if unknown:
        raise ValueError(
            f'unknown tier(s) {unknown}; choose from {sorted(TIERS_BY_KEY)} or "all"')
    return tuple(TIERS_BY_KEY[key] for key in keys)


def ad_hoc_tier(spec: str) -> Tier:
    """Build a tier from AutoEq's --bass-boost syntax: "gain" or "gain,fc,q"."""
    gain = float(spec.split(',')[0])
    key = f'bass{gain:g}'.replace('.', 'p').replace('-', 'neg')
    return Tier(key, f'{gain:g}dB', gain, f'ad hoc bass boost {spec}')
