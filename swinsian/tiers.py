"""Bass boost tiers.

Each tier reconstitutes the Harman over-ear 2018 bass shelf at a different strength. The
reference tier is AutoEq's own value; the rest step up from there.
"""

from typing import NamedTuple, Sequence

BASS_BOOST_FC = 105.0
BASS_BOOST_Q = 0.7

# max_gain must exceed the shelf gain or FrequencyResponse.equalize clips the boost itself;
# AutoEq's default of 6 dB would flatten everything above the reference tier. It must also never
# fall below that default, or a negative shelf would clip the whole correction curve flat.
MAX_GAIN_MARGIN = 6.0


class Tier(NamedTuple):
    key: str
    label: str
    bass_gain: float
    note: str
    bass_fc: float = BASS_BOOST_FC
    bass_q: float = BASS_BOOST_Q

    @property
    def max_gain(self) -> float:
        return max(self.bass_gain + MAX_GAIN_MARGIN, MAX_GAIN_MARGIN)


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
    """Build a tier from AutoEq's --bass-boost syntax: "gain", "gain,fc" or "gain,fc,q"."""
    fields = [field.strip() for field in spec.split(',')]
    if len(fields) > 3:
        raise ValueError(f'--bass-boost takes at most "gain,fc,q", got {spec!r}')
    try:
        gain = float(fields[0])
        fc = float(fields[1]) if len(fields) > 1 else BASS_BOOST_FC
        q = float(fields[2]) if len(fields) > 2 else BASS_BOOST_Q
    except ValueError:
        raise ValueError(f'--bass-boost expects numbers, got {spec!r}')

    key = f'bass{gain:g}'.replace('.', 'p').replace('-', 'neg')
    label = f'{gain:g}dB' if len(fields) == 1 else f'{gain:g}dB@{fc:g}Hz'
    return Tier(key, label, gain, f'ad hoc bass boost {spec}', bass_fc=fc, bass_q=q)
