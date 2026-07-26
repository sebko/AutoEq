"""Reading and writing Swinsian's equalizer presets.

Swinsian stores presets in its preferences domain as {preset name: NSData}. Two hazards shape
this module:

1. -[SWNEqualizerController savePresets] rewrites the whole EqualizerPresets dictionary from
   Swinsian's in-memory list, so anything written while Swinsian runs is discarded the next time
   the user touches the equalizer. Quitting first is required, not advisory.
2. cfprefsd caches the domain and flushes its own copy over the file, so editing the plist
   directly (PlistBuddy, plutil, plistlib) is silently lost. Every write goes through
   `defaults import`, which is also a single atomic domain write.
"""

import plistlib
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DOMAIN = 'com.swinsian.Swinsian'
PLIST_PATH = Path.home() / 'Library' / 'Preferences' / f'{DOMAIN}.plist'
PRESETS_KEY = 'EqualizerPresets'
MANUAL_SETTINGS_KEY = 'EqualizerManualSettings'
SELECTED_PRESET_KEY = 'SelectedEqualizerPreset'


class SwinsianRunning(RuntimeError):
    pass


def is_running() -> bool:
    return subprocess.run(['/usr/bin/pgrep', '-x', 'Swinsian'], capture_output=True).returncode == 0


def quit_app(timeout: float = 30.0) -> None:
    if not is_running():
        return
    subprocess.run(
        ['/usr/bin/osascript', '-e', 'tell application "Swinsian" to quit'],
        capture_output=True, check=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running():
            return
        time.sleep(0.5)
    raise SwinsianRunning(
        f'Swinsian was still running {timeout:g}s after being asked to quit. '
        'Quit it manually and try again.')


def launch_app() -> None:
    subprocess.run(['/usr/bin/open', '-a', 'Swinsian'], check=True)


def _defaults(*command: str, stdin: bytes = None) -> bytes:
    """Run `defaults`, surfacing its stderr instead of hiding it in a CalledProcessError."""
    result = subprocess.run(
        ['/usr/bin/defaults', *command], input=stdin, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode(errors='replace').strip() or 'no error output'
        raise RuntimeError(f'`defaults {" ".join(command)}` failed: {detail}')
    return result.stdout


def export_domain() -> Dict[str, Any]:
    return plistlib.loads(_defaults('export', DOMAIN, '-'))


def import_domain(domain: Dict[str, Any]) -> None:
    _defaults('import', DOMAIN, '-', stdin=plistlib.dumps(domain, fmt=plistlib.FMT_XML))


def backup(dest_dir: Path) -> Tuple[Path, Path]:
    """Snapshot the domain both as cfprefsd sees it and as it exists on disk."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')

    xml_path = dest_dir / f'{stamp}.xml'
    xml_path.write_bytes(_defaults('export', DOMAIN, '-'))

    plist_path = dest_dir / f'{stamp}.plist'
    if PLIST_PATH.exists():
        shutil.copy2(PLIST_PATH, plist_path)

    return xml_path, plist_path


def restore(backup_xml: Path, backup_dir: Path = None) -> None:
    if is_running():
        raise SwinsianRunning('Quit Swinsian before restoring its preferences.')
    if backup_dir is not None:
        # Restoring the wrong file would otherwise discard the current state irrecoverably.
        backup(backup_dir)
    _defaults('import', DOMAIN, str(backup_xml))


def read_presets() -> Dict[str, bytes]:
    return dict(export_domain().get(PRESETS_KEY, {}))


def read_manual_settings() -> Optional[bytes]:
    return export_domain().get(MANUAL_SETTINGS_KEY)


def write_presets(presets: Dict[str, bytes], replace_all: bool = False) -> Dict[str, bytes]:
    """Merge (or replace) presets into the domain. Returns the resulting preset dictionary."""
    if is_running():
        raise SwinsianRunning(
            'Swinsian is running. It rewrites EqualizerPresets wholesale from memory, so it '
            'would discard anything written now. Quit it first (--quit-swinsian).')
    domain = export_domain()
    merged = {} if replace_all else dict(domain.get(PRESETS_KEY, {}))
    merged.update(presets)
    domain[PRESETS_KEY] = merged
    import_domain(domain)
    return merged
