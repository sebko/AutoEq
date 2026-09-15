"""Fitting AutoEq measurements onto AUGraphicEQ's 31 fixed bands."""

import re
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence

import numpy as np
from autoeq.frequency_response import FrequencyResponse

from .augraphiceq import GAIN_MAX, GAIN_MIN, ISO_CENTERS_31
from .config import load_band_config
from .tiers import Tier

CLIP_EPSILON = 0.05  # dB from a rail before a band counts as clipped
DEFAULT_GAIN_RANGE = 3.0  # dB a band may stray from the ideal curve at its own centre
REPO_ROOT = Path(__file__).parent.parent
DEFAULT_TARGET = REPO_ROOT / 'targets' / 'Harman over-ear 2018 without bass.csv'
DEFAULT_MEASUREMENTS_ROOT = REPO_ROOT / 'measurements'
DEFAULT_DB = 'oratory1990'


class MeasurementNotFound(LookupError):
    pass


class AmbiguousMeasurement(LookupError):
    pass


class ClippedBand(NamedTuple):
    index: int
    frequency: float
    gain: float
    ideal: float

    @property
    def shortfall(self) -> float:
        return abs(self.ideal - self.gain)


class Fit(NamedTuple):
    measurement: Path
    tier: Tier
    gains: List[float]
    offset: float
    clipped: List[ClippedBand]
    rmse: float
    rmse_to_10k: float
    realized_peak: float
    fr: FrequencyResponse


def find_measurement(
        name: str,
        db: str = DEFAULT_DB,
        measurements_root: Path = DEFAULT_MEASUREMENTS_ROOT) -> Path:
    """Resolve a headphone name or path to a measurement CSV.

    An exact stem match wins outright, so "Apple AirPods Max" cannot silently resolve to
    "Apple AirPods Max (3rd party pleather earpads)".
    """
    candidate = Path(name)
    if candidate.suffix.lower() == '.csv' and candidate.is_file():
        return candidate.resolve()

    search_root = measurements_root / db / 'data'
    if not search_root.is_dir():
        raise MeasurementNotFound(f'no measurement database at {search_root}')

    all_csvs = sorted(search_root.rglob('*.csv'))
    exact = [p for p in all_csvs if p.stem.lower() == name.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousMeasurement(_ambiguity_message(name, exact))

    needle = name.lower()
    partial = [p for p in all_csvs if needle in p.stem.lower()]
    if not partial:
        raise MeasurementNotFound(f'no measurement in {search_root} matching {name!r}')
    if len(partial) > 1:
        raise AmbiguousMeasurement(_ambiguity_message(name, partial))
    return partial[0]


def _ambiguity_message(name: str, matches: Sequence[Path]) -> str:
    listing = '\n'.join(f'  {p.stem}' for p in matches)
    return f'{name!r} matches {len(matches)} measurements; be more specific:\n{listing}'


def list_measurements(
        db: str = DEFAULT_DB,
        measurements_root: Path = DEFAULT_MEASUREMENTS_ROOT,
        pattern: Optional[str] = None) -> List[Path]:
    search_root = measurements_root / db / 'data'
    if not search_root.is_dir():
        raise MeasurementNotFound(f'no measurement database at {search_root}')
    paths = sorted(search_root.rglob('*.csv'))
    if pattern:
        needle = pattern.lower()
        paths = [p for p in paths if needle in p.stem.lower()]
    return paths


def fit_measurement(
        measurement_path: Path,
        tier: Tier,
        target_path: Path = DEFAULT_TARGET,
        headroom: float = 0.2,
        normalize: str = 'auto',
        max_gain: Optional[float] = None,
        fs: int = 44100,
        refit: bool = False,
        gain_range: Optional[float] = DEFAULT_GAIN_RANGE,
        **process_kwargs) -> Fit:
    """Fit one measurement at one bass tier onto the 31 AUGraphicEQ bands.

    AUGraphicEQ has no preamp, so the headroom AutoEq would put in a preamp value has to live in
    the band gains. Rather than subtracting a constant afterwards (which the third-octave bells
    do not sum flat to, and which would push bands past the rails with no chance to redistribute),
    the offset is folded into the optimizer's target via the `preamp` argument, so the +-20 dB box
    constraints apply to the already shifted curve.

    `gain_range` additionally pins each band to within that many dB of the ideal curve at its own
    centre frequency. Without it the optimizer exploits how third-octave biquads misbehave near
    Nyquist at 44.1 kHz, producing enormous cancelling gains in the top octave that model well but
    would not survive a different filter implementation.
    """
    fr = FrequencyResponse.read_csv(measurement_path)
    target = FrequencyResponse.read_csv(target_path)
    fr.process(
        target=target,
        min_mean_error=True,
        bass_boost_gain=tier.bass_gain,
        bass_boost_fc=tier.bass_fc,
        bass_boost_q=tier.bass_q,
        max_gain=tier.max_gain if max_gain is None else max_gain,
        fs=fs,
        **process_kwargs)

    offset = _level_offset(fr, normalize, headroom)

    ideal_at_bands = _interpolate_at_bands(fr.frequency, fr.equalization + offset)
    gains = _optimize(fr, offset, fs, ideal_at_bands, gain_range)
    if refit and normalize != 'none':
        # Correct the fit overshooting the level the offset intended, not the offset itself:
        # under 'auto' the intended peak is deliberately above unity, and comparing against
        # -headroom here would undo that and drive the deepest cuts into the floor.
        intended_peak = max(ideal_at_bands)
        realized = float(np.max(fr.fixed_band_eq))
        if realized > intended_peak + CLIP_EPSILON:
            offset -= realized - intended_peak
            ideal_at_bands = _interpolate_at_bands(fr.frequency, fr.equalization + offset)
            gains = _optimize(fr, offset, fs, ideal_at_bands, gain_range)

    clipped = [
        ClippedBand(i, ISO_CENTERS_31[i], gain, ideal_at_bands[i])
        for i, gain in enumerate(gains)
        if gain <= GAIN_MIN + CLIP_EPSILON or gain >= GAIN_MAX - CLIP_EPSILON]

    error = fr.fixed_band_eq - (fr.equalization + offset)
    in_band = (fr.frequency >= 20) & (fr.frequency <= 20000)
    to_10k = (fr.frequency >= 20) & (fr.frequency <= 10000)
    rmse = float(np.sqrt(np.mean(error[in_band] ** 2)))
    rmse_to_10k = float(np.sqrt(np.mean(error[to_10k] ** 2)))
    realized_peak = float(np.max(fr.fixed_band_eq[in_band]))

    fr.equalized_raw = fr.raw + fr.fixed_band_eq - offset
    return Fit(measurement_path, tier, gains, offset, clipped, rmse, rmse_to_10k, realized_peak, fr)


def _level_offset(fr: FrequencyResponse, normalize: str, headroom: float) -> float:
    """Choose how far to shift the whole correction down.

    AUGraphicEQ can only express a 40 dB span, and only 20 dB of it without boosting. Pinning the
    curve's peak just below unity avoids any boost, but when peak-to-trough exceeds 20 dB it drives
    the deepest cuts through the -20 dB floor, which distorts the curve rather than just quietening
    it. In 'auto' the offset is then raised just enough to keep the trough off the floor, trading a
    few dB of boost (recoverable by lowering Swinsian's volume) for the correct shape.
    """
    if normalize == 'none':
        return 0.0
    if normalize not in ('peak', 'auto'):
        raise ValueError(f'unknown normalize mode {normalize!r}')

    at_bands = _interpolate_at_bands(fr.frequency, fr.equalization)
    highest, lowest = max(at_bands), min(at_bands)
    no_boost = -(highest + headroom)
    if normalize == 'peak':
        return no_boost
    # The epsilon keeps the deepest band just clear of the floor rather than exactly on it, so it
    # is not reported as clipped on optimizer noise alone.
    off_the_floor = GAIN_MIN + CLIP_EPSILON - lowest
    # Never shift further up than the ceiling allows, even if the floor still clips.
    return min(max(no_boost, off_the_floor), GAIN_MAX - highest)


def _optimize(fr: FrequencyResponse, offset: float, fs: int, ideal_at_bands: Sequence[float],
              gain_range: Optional[float]) -> List[float]:
    config = load_band_config()
    if gain_range is not None:
        # Not FrequencyResponse.optimize_fixed_band_eq's own gain_range argument: that centres the
        # bounds on the unshifted equalization curve, which would be wrong by `offset` here.
        for band, ideal in zip(config['filters'], ideal_at_bands):
            # Clamp into the rails in this order so an ideal beyond a rail collapses the bound
            # onto that rail rather than inverting it.
            band['min_gain'] = min(GAIN_MAX, max(GAIN_MIN, ideal - gain_range))
            band['max_gain'] = max(GAIN_MIN, min(GAIN_MAX, ideal + gain_range))
    peqs = fr.optimize_fixed_band_eq(config, fs, preamp=offset)
    peq = peqs[0]
    # Filter order is the AUGraphicEQ parameter order; a mismatch would silently scramble the EQ.
    fcs = [filt.fc for filt in peq.filters]
    if fcs != list(ISO_CENTERS_31):
        raise AssertionError(f'optimizer returned bands in unexpected order: {fcs}')
    return [float(np.clip(filt.gain, GAIN_MIN, GAIN_MAX)) for filt in peq.filters]


def _interpolate_at_bands(frequency: np.ndarray, values: np.ndarray) -> List[float]:
    return [float(np.interp(fc, frequency, values)) for fc in ISO_CENTERS_31]


def preset_name(measurement_path: Path, tier: Tier, index: int, template: str,
                short_name: Optional[str] = None) -> str:
    short = short_name or short_model_name(measurement_path.stem)
    return template.format(short=short, n=index, label=tier.label, key=tier.key,
                           bass=f'{tier.bass_gain:g}')


def short_model_name(stem: str) -> str:
    """Trim a measurement filename down to something that fits a preset menu."""
    name = re.sub(r'^(Apple|Audeze|Beyerdynamic|Sennheiser|Audio-Technica|AKG|Sony|Bose|Focal)\s+', '',
                  stem)
    name = re.sub(r'\s*\(([^)]*)earpads\)', lambda m: f' {m.group(1).strip()}', name)
    return re.sub(r'\s+', ' ', name).strip()
