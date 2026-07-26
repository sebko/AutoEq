"""Generate Swinsian equalizer presets from AutoEq measurements."""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Sequence

from . import augraphiceq as au
from . import prefs
from .fit import (DEFAULT_DB, DEFAULT_GAIN_RANGE, DEFAULT_MEASUREMENTS_ROOT, DEFAULT_TARGET,
                  AmbiguousMeasurement, Fit, MeasurementNotFound, find_measurement,
                  fit_measurement, list_measurements, preset_name)
from .tiers import TIERS, Tier, ad_hoc_tier, resolve_tiers

DEFAULT_OUT_DIR = Path(__file__).parent.parent / 'swinsian_out'
DEFAULT_NAME_TEMPLATE = '{short} {n} {label}'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m swinsian',
        description='Turn AutoEq measurements into Swinsian AUGraphicEQ presets.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='By default nothing is written to Swinsian; pass --install for that.')

    parser.add_argument('measurements', nargs='*', metavar='MEASUREMENT',
                        help='headphone name or path to a measurement CSV')

    source = parser.add_argument_group('measurement source')
    source.add_argument('--db', default=DEFAULT_DB)
    source.add_argument('--measurements-root', type=Path, default=DEFAULT_MEASUREMENTS_ROOT)
    source.add_argument('--target', type=Path, default=DEFAULT_TARGET)
    source.add_argument('--list', action='store_true', help='list matching measurements and exit')

    tone = parser.add_argument_group('tone')
    tone.add_argument('--tiers', default='all',
                      help='comma separated tier keys, or "all" (default). '
                           f'Available: {", ".join(t.key for t in TIERS)}')
    tone.add_argument('--bass-boost', action='append', metavar='GAIN',
                      help='ad hoc bass shelf gain in dB; repeatable, overrides --tiers')
    tone.add_argument('--max-gain', type=float, help='override the per tier max gain')
    tone.add_argument('--tilt', type=float, default=0.0)
    tone.add_argument('--treble-boost', type=float, default=0.0, dest='treble_boost_gain')
    tone.add_argument('--fs', type=int, default=44100)

    level = parser.add_argument_group('level')
    level.add_argument('--normalize', choices=['auto', 'peak', 'none'], default='auto',
                       help='auto (default) avoids boost unless doing so would drive the deepest '
                            'cuts through the -20 dB floor; peak always avoids boost')
    level.add_argument('--headroom', type=float, default=0.2,
                       help='dB below unity for the fitted peak (default 0.2)')
    level.add_argument('--refit', action='store_true',
                       help='run a corrective pass if the fitted peak overshoots')
    level.add_argument('--gain-range', type=float, default=DEFAULT_GAIN_RANGE,
                       help='dB a band may stray from the ideal curve at its own centre '
                            f'(default {DEFAULT_GAIN_RANGE:g}); use -1 to leave bands unbounded')
    level.add_argument('--allow-clipping', action='store_true',
                       help='exit 0 even when bands hit the audio unit rails')
    level.add_argument('--i-know-this-clips', action='store_true',
                       help='required with --normalize=none')

    out = parser.add_argument_group('output')
    out.add_argument('--name-template', default=DEFAULT_NAME_TEMPLATE)
    out.add_argument('--short-name', action='append',
                     help='override the derived short name, once per measurement in order')
    out.add_argument('--out-dir', type=Path, default=DEFAULT_OUT_DIR)

    install = parser.add_argument_group('swinsian')
    install.add_argument('--install', action='store_true', help='write presets into Swinsian')
    install.add_argument('--replace-all', action='store_true',
                         help='replace every existing preset instead of merging')
    install.add_argument('--quit-swinsian', action='store_true')
    install.add_argument('--relaunch', action='store_true')
    install.add_argument('--verify', action='store_true', help='read presets back and compare')
    install.add_argument('--backup-dir', type=Path)
    install.add_argument('--restore', type=Path, metavar='BACKUP_XML')
    install.add_argument('--probe-dump', action='store_true',
                         help='decode and print Swinsian\'s stored equalizer state, then exit')
    return parser


def main(argv: Sequence[str] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.restore:
        prefs.restore(args.restore)
        print(f'Restored {prefs.DOMAIN} from {args.restore}')
        return 0

    if args.probe_dump:
        return probe_dump()

    if args.list:
        for path in list_measurements(args.db, args.measurements_root,
                                      args.measurements[0] if args.measurements else None):
            print(path.stem)
        return 0

    if not args.measurements:
        build_parser().error('at least one MEASUREMENT is required (or use --list)')

    if args.normalize == 'none' and not args.i_know_this_clips:
        build_parser().error(
            '--normalize=none leaves positive band gains that will clip digitally; '
            'pass --i-know-this-clips to accept that')

    try:
        paths = [find_measurement(name, args.db, args.measurements_root)
                 for name in args.measurements]
    except (MeasurementNotFound, AmbiguousMeasurement) as err:
        print(f'error: {err}', file=sys.stderr)
        return 2

    tiers = ([ad_hoc_tier(spec) for spec in args.bass_boost] if args.bass_boost
             else list(resolve_tiers(args.tiers.split(','))))

    short_names = args.short_name or []
    fits: List[Fit] = []
    names: List[str] = []
    for path_index, path in enumerate(paths):
        short = short_names[path_index] if path_index < len(short_names) else None
        for tier_index, tier in enumerate(tiers, start=1):
            print(f'Fitting {path.stem} @ {tier.label} ({tier.bass_gain:g} dB bass)...', flush=True)
            fit = fit_measurement(
                path, tier,
                target_path=args.target,
                headroom=args.headroom,
                normalize=args.normalize,
                max_gain=args.max_gain,
                fs=args.fs,
                refit=args.refit,
                gain_range=None if args.gain_range < 0 else args.gain_range,
                tilt=args.tilt,
                treble_boost_gain=args.treble_boost_gain)
            fits.append(fit)
            names.append(preset_name(path, tier, tier_index, args.name_template, short))

    write_artifacts(fits, names, args.out_dir)
    print_summary(fits, names)

    if args.install:
        install_presets(fits, names, args)

    clipped_total = sum(len(fit.clipped) for fit in fits)
    if clipped_total and not args.allow_clipping:
        print(f'\n{clipped_total} band(s) hit the audio unit rails. '
              'Pass --allow-clipping to accept this.', file=sys.stderr)
        return 1
    return 0


def preset_payloads(fits: Sequence[Fit], names: Sequence[str]) -> Dict[str, bytes]:
    return {
        name: au.serialize_classinfo(au.build_classinfo(fit.gains, name=name))
        for name, fit in zip(names, fits)}


def write_artifacts(fits: Sequence[Fit], names: Sequence[str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for fit, name in zip(fits, names):
        target_dir = out_dir / fit.measurement.stem / fit.tier.key
        target_dir.mkdir(parents=True, exist_ok=True)

        (target_dir / f'{name}.classinfo.plist').write_bytes(
            au.serialize_classinfo(au.build_classinfo(fit.gains, name=name)))

        with open(target_dir / f'{name} gains.csv', 'w') as fh:
            fh.write('frequency,gain_db\n')
            for centre, gain in zip(au.ISO_CENTERS_31, fit.gains):
                fh.write(f'{centre:g},{gain:.2f}\n')

        (target_dir / f'{name}.md').write_text(preset_report(fit, name))

        plot_fr = fit.fr.copy()
        # The fit was made against a target shifted down by `offset`; undo that for the plot so
        # it shows tonal balance rather than the level drop.
        plot_fr.fixed_band_eq = plot_fr.fixed_band_eq - fit.offset
        plot_fr.name = f'{fit.measurement.stem} - {fit.tier.label} ({fit.offset:+.1f} dB level)'
        plot_fr.plot(show_fig=False, close_fig=True, file_path=str(target_dir / f'{name}.png'),
                     parametric_eq=False)

    (out_dir / 'SUMMARY.md').write_text(summary_report(fits, names))


def preset_report(fit: Fit, name: str) -> str:
    lines = [
        f'# {name}',
        '',
        f'- Measurement: `{fit.measurement}`',
        f'- Bass shelf: {fit.tier.bass_gain:g} dB @ 105 Hz Q 0.7 ({fit.tier.note})',
        f'- Level offset baked into the bands: **{fit.offset:+.1f} dB**',
        f'- Fitted peak: {fit.realized_peak:+.2f} dB',
        f'- Fit RMSE vs the ideal curve: {fit.rmse:.2f} dB '
        f'({fit.rmse_to_10k:.2f} dB below 10 kHz)',
        '',
        f'AUGraphicEQ has no preamp, so this preset plays about {abs(fit.offset):.0f} dB quieter '
        'than bypass. Raise Swinsian\'s volume to compare fairly.',
        '',
    ]
    if fit.realized_peak > 0:
        lines += [
            f'> The correction spans more than the audio unit\'s 20 dB of headroom, so it boosts '
            f'by up to {fit.realized_peak:.1f} dB rather than distorting the curve shape. '
            f'Keep Swinsian\'s volume at or below {-fit.realized_peak:.0f} dB to avoid clipping.',
            '']
    if fit.clipped:
        lines += ['## Clipped bands', '',
                  'These bands wanted more cut than the audio unit\'s -20 dB floor allows:', '',
                  '| Hz | Set (dB) | Wanted (dB) | Short by (dB) |',
                  '| --- | --- | --- | --- |']
        lines += [f'| {b.frequency:g} | {b.gain:+.2f} | {b.ideal:+.2f} | {b.shortfall:.2f} |'
                  for b in fit.clipped]
        lines.append('')
    lines += ['## Bands', '', '| Hz | Gain (dB) |', '| --- | --- |']
    lines += [f'| {centre:g} | {gain:+.2f} |'
              for centre, gain in zip(au.ISO_CENTERS_31, fit.gains)]
    return '\n'.join(lines) + '\n'


def summary_report(fits: Sequence[Fit], names: Sequence[str]) -> str:
    lines = ['# Swinsian preset summary', '',
             '| Preset | Bass (dB) | Level offset (dB) | Clipped bands | RMSE (dB) | RMSE <10k (dB) |',
             '| --- | --- | --- | --- | --- | --- |']
    for fit, name in zip(fits, names):
        lines.append(f'| {name} | {fit.tier.bass_gain:g} | {fit.offset:+.1f} | '
                     f'{len(fit.clipped)} | {fit.rmse:.2f} | {fit.rmse_to_10k:.2f} |')
    return '\n'.join(lines) + '\n'


def print_summary(fits: Sequence[Fit], names: Sequence[str]) -> None:
    print()
    width = max(len(n) for n in names)
    print(f'{"Preset".ljust(width)}  {"Bass":>5}  {"Level":>7}  {"Clip":>4}  {"RMSE":>5}  {"<10k":>5}')
    for fit, name in zip(fits, names):
        print(f'{name.ljust(width)}  {fit.tier.bass_gain:>4g}  {fit.offset:>+6.1f}  '
              f'{len(fit.clipped):>4}  {fit.rmse:>5.2f}  {fit.rmse_to_10k:>5.2f}')


def install_presets(fits: Sequence[Fit], names: Sequence[str], args) -> None:
    if prefs.is_running():
        if not args.quit_swinsian:
            raise prefs.SwinsianRunning(
                'Swinsian is running and would discard presets written now. '
                'Quit it, or pass --quit-swinsian.')
        print('\nQuitting Swinsian...')
        prefs.quit_app()

    backup_dir = args.backup_dir or (args.out_dir / 'backups')
    xml_path, _ = prefs.backup(backup_dir)
    print(f'Backed up preferences to {xml_path}')

    payloads = preset_payloads(fits, names)
    prefs.write_presets(payloads, replace_all=args.replace_all)
    print(f'Installed {len(payloads)} preset(s).')

    if args.verify:
        verify_presets(fits, names)

    if args.relaunch:
        prefs.launch_app()
        print('Relaunched Swinsian.')


def verify_presets(fits: Sequence[Fit], names: Sequence[str]) -> None:
    stored = prefs.read_presets()
    for fit, name in zip(fits, names):
        if name not in stored:
            raise AssertionError(f'preset {name!r} is missing after install')
        decoded, num_bands = au.gains_from_preset_data(stored[name])
        if num_bands != 31:
            raise AssertionError(f'preset {name!r} came back with {num_bands} bands')
        expected = [au.f32(g) for g in fit.gains]
        if decoded != expected:
            raise AssertionError(f'preset {name!r} read back with different gains')
    print(f'Verified {len(fits)} preset(s) by reading them back.')


def probe_dump() -> int:
    manual = prefs.read_manual_settings()
    print('== EqualizerManualSettings ==')
    if manual is None:
        print('  (absent)')
    else:
        classinfo = au.parse_classinfo(manual)
        params = au.decode_param_blob(classinfo['data'])
        print(f'  name: {classinfo["name"]!r}')
        print(f'  Num EQ Bands: {classinfo["Num EQ Bands"]}')
        print(f'  band count parameter: {params[au.NUM_BANDS_PARAM_ID]}')
        print(f'  blob: {len(classinfo["data"])} bytes, {len(params)} parameters')
        print(au.describe_gains([params[i] for i in range(31)]))

    presets = prefs.read_presets()
    print(f'\n== EqualizerPresets ({len(presets)}) ==')
    for name, data in presets.items():
        print(f'\n-- {name} --')
        try:
            gains, num_bands = au.gains_from_preset_data(data)
        except ValueError as err:
            print(f'  could not decode: {err}')
            continue
        print(f'  Num EQ Bands: {num_bands}')
        nonzero = [(au.ISO_CENTERS_31[i], g) for i, g in enumerate(gains) if abs(g) > 0.005]
        print(f'  non-zero bands: '
              + (', '.join(f'{fc:g} Hz {g:+.2f} dB' for fc, g in nonzero) or '(none)'))
    return 0
