# AutoEq → Swinsian

Turns any headphone in AutoEq's measurement database into equalizer presets for
[Swinsian](https://swinsian.com), installed straight into its preset menu.

Swinsian's equalizer is Apple's `AUGraphicEQ` audio unit: 31 ISO third-octave bands from 20 Hz to
20 kHz, each ±20 dB, and **no preamp**. Presets live in Swinsian's preferences as
`{preset name: NSData}`, where the data is a binary plist of the audio unit's ClassInfo dictionary.
This tool fits an AutoEq correction onto those exact 31 bands and writes the presets.

## Setup

AutoEq pins `numpy~=1.24.4` / `scipy~=1.10.1` and requires Python `>=3.8,<3.12`. On macOS that
usually means `/usr/bin/python3`; a Homebrew or mise Python 3.12+ will fail to build the pins.

```bash
/usr/bin/python3 -m venv .venv
.venv/bin/python -m pip install -U pip wheel
.venv/bin/python -m pip install -e .
```

## Usage

```bash
# What's available
.venv/bin/python -m swinsian --list "DT 1990"

# Generate, writing nothing to Swinsian
.venv/bin/python -m swinsian "Apple AirPods Max" --tiers all --out-dir swinsian_out

# Install, with a backup and a read-back check
.venv/bin/python -m swinsian "Apple AirPods Max" --tiers all \
    --install --quit-swinsian --verify --relaunch

# Undo
.venv/bin/python -m swinsian --restore swinsian_out/backups/<timestamp>.xml
```

### Measurements from outside the database

`swinsian/measurements/` holds measurements AutoEq doesn't have yet, as AutoEq CSVs. Pass the path
and the target that suits the rig. In-ear measurements need `--target`, because the default target
is over-ear:

```bash
.venv/bin/python -m swinsian "swinsian/measurements/Beyerdynamic DT 30 IE.csv" \
    --target "targets/AutoEq in-ear.csv" --install --quit-swinsian --verify --relaunch
```

| File | Source |
| --- | --- |
| `Beyerdynamic DT 30 IE.csv` | [Hawaii Bad Boy's squig.link](https://hbb.squig.link), right channel only (no left published), fetched 2026-09-15 |

Nothing touches Swinsian without `--install`. Each run writes a PNG, a band table, a gains CSV and
the exact preset payload per tier, plus a `SUMMARY.md`.

### Bass tiers

Tiers step up from AutoEq's reference shelf for the chosen target, so they mean the same thing on
over-ear and in-ear targets. A target AutoEq has no reference value for needs `--bass-boost`.

| Tier | Shelf @ 105 Hz | Harman over-ear 2018 (default) | AutoEq in-ear |
| --- | --- | --- | --- |
| `harman` | reference | 6 dB | 8 dB |
| `warm` | +3 dB | 9 dB | 11 dB |
| `heavy` | +6 dB | 12 dB | 14 dB |
| `max` | +12 dB | 18 dB | 20 dB |

`--bass-boost GAIN[,FC[,Q]]` (repeatable) replaces the tiers with ad hoc shelves, e.g.
`--bass-boost 9.5,150,0.69`. Centre frequency defaults to 105 Hz and Q to 0.7.

## The two things that will bite you

**Swinsian must be quit before installing.** `-[SWNEqualizerController savePresets]` rewrites the
entire preset dictionary from Swinsian's in-memory list, so presets written while it runs are
discarded the moment you touch the equalizer. `--quit-swinsian` handles it; otherwise the tool
refuses.

**Never edit the plist directly.** `cfprefsd` caches the domain and flushes its own copy over the
file, so `PlistBuddy`, `plutil` and `plistlib` writes are silently lost. Every write here goes
through `defaults import`, which is also atomic for the whole domain. (`killall cfprefsd` is only
ever needed to recover from having ignored this.)

## Level, and why presets play quieter

With no preamp available, the headroom a correction needs has to live in the band gains themselves,
so every preset is shifted down and plays quieter than bypass. The shift is reported per preset;
raise Swinsian's volume when comparing against bypass.

`--normalize auto` (the default) avoids boosting at all when it can. When a correction's
peak-to-trough span exceeds the audio unit's 20 dB of downward range — common with a big bass shelf
on a headphone that also needs a deep treble cut — pinning the peak at unity would drive the deepest
cuts through the −20 dB floor and distort the curve's shape rather than merely quieten it. `auto`
then raises the offset just enough to keep the trough off the floor and accepts a few dB of boost,
which you recover by lowering Swinsian's volume. `--normalize peak` never boosts, at the cost of
clipped bands; `--normalize none` applies no shift at all and needs `--i-know-this-clips`.

Bands that still land on a rail are listed in each preset's report and make the tool exit non-zero
unless `--allow-clipping`. The check runs before installing, so a refused run writes nothing.

## Why bands are bounded to the curve

`--gain-range` (default 3 dB) keeps each band within that distance of the ideal curve at its own
centre frequency. Without it the optimizer exploits how third-octave biquads misbehave near Nyquist
at 44.1 kHz and produces enormous cancelling gains in the top octave — e.g. −20 dB at 12.5 kHz where
the curve only asks for −6.8 dB. Those fit the model well but would not survive a different filter
implementation. Loosening the bound measurably *worsens* the fit, so the default is deliberate.

## Verifying

`--verify` reads every installed preset back out of the preferences and compares the decoded band
gains against what was computed, at float32 precision.

`--probe-dump` decodes and prints Swinsian's stored equalizer state read-only — useful for
confirming what the app actually saved.

In the app: open the equalizer window, check the band popup reads **31 Bands**, and compare the
slider silhouette against the tier's PNG. The `MAX` presets are expected to show extreme treble
sliders — that is the correction's span, not a bug.

## Known limits

`AUGraphicEQ`'s internal filter topology is closed-source. The 31 bands are modelled as independent
peaking biquads at Q 4.318473; each slider is set to the gain AutoEq wants at that centre frequency,
but the interpolation *between* centres is a model, so the PNG is a prediction rather than a
measurement. Trust your ears over the graph.

Swinsian's AppleScript dictionary has no equalizer commands, so presets cannot be switched
automatically when you change headphones — pick them in the equalizer window.
