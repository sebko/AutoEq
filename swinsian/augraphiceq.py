"""Encoding and decoding of Apple AUGraphicEQ state as Swinsian stores it.

Swinsian's equalizer is Apple's AUGraphicEQ audio unit. A saved preset is an NSData holding a
binary plist of the unit's kAudioUnitProperty_ClassInfo dictionary, whose 'data' member is the
raw AUBase::SaveState parameter dump. Band centres, ranges and parameter IDs below come from
`auval -v aufx greq appl`.

This module is deliberately stdlib-only so it can be tested without the AutoEq dependency stack.
"""

import plistlib
import struct
from typing import Any, Dict, List, Sequence, Tuple

AU_TYPE = 0x61756678  # 'aufx'
AU_SUBTYPE = 0x67726571  # 'greq'
AU_MANUFACTURER = 0x6170706C  # 'appl'
AU_VERSION = 0

NUM_BANDS_PARAM_ID = 10000
NUM_BANDS_VALUES = {10: 0.0, 31: 1.0}  # the parameter is indexed, not a count

GAIN_MIN = -20.0
GAIN_MAX = 20.0

CLASSINFO_BLOB_LEN = 268

# Index is the AUGraphicEQ global parameter ID.
ISO_CENTERS_31 = (
    20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0,
    200.0, 250.0, 315.0, 400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0,
    2000.0, 2500.0, 3150.0, 4000.0, 5000.0, 6300.0, 8000.0, 10000.0, 12500.0, 16000.0,
    20000.0,
)

CLASSINFO_KEYS = {'manufacturer', 'subtype', 'type', 'version', 'name', 'Num EQ Bands', 'data'}


def f32(value: float) -> float:
    """Round-trip a float through single precision, as the encoded blob does."""
    return struct.unpack('>f', struct.pack('>f', float(value)))[0]


def encode_param_blob(gains: Sequence[float], num_bands: int = 31) -> bytes:
    """Build the AUBase::SaveState blob for a set of band gains in dB."""
    if num_bands not in NUM_BANDS_VALUES:
        raise ValueError(f'num_bands must be 10 or 31, got {num_bands}')
    if len(gains) != len(ISO_CENTERS_31):
        raise ValueError(f'expected {len(ISO_CENTERS_31)} gains, got {len(gains)}')
    for i, gain in enumerate(gains):
        if not GAIN_MIN <= gain <= GAIN_MAX:
            raise ValueError(
                f'gain {gain:.2f} dB for band {i} ({ISO_CENTERS_31[i]:g} Hz) is outside '
                f'AUGraphicEQ\'s [{GAIN_MIN}, {GAIN_MAX}] dB range')

    params = [(i, float(gain)) for i, gain in enumerate(gains)]
    params.append((NUM_BANDS_PARAM_ID, NUM_BANDS_VALUES[num_bands]))

    blob = struct.pack('>III', 0, 0, len(params))  # scope, element, parameter count
    for param_id, value in params:
        blob += struct.pack('>if', param_id, value)

    if len(blob) != CLASSINFO_BLOB_LEN:
        raise AssertionError(f'encoded blob is {len(blob)} bytes, expected {CLASSINFO_BLOB_LEN}')
    return blob


def decode_param_blob(blob: bytes) -> Dict[int, float]:
    """Decode an AUBase::SaveState blob into {parameter ID: value}.

    Loops over element blocks rather than assuming one, so a unit with more than the global
    scope would still decode.
    """
    params: Dict[int, float] = {}
    offset = 0
    while offset < len(blob):
        if offset + 12 > len(blob):
            raise ValueError(f'truncated element header at offset {offset}')
        _scope, _element, count = struct.unpack_from('>III', blob, offset)
        offset += 12
        if offset + count * 8 > len(blob):
            raise ValueError(f'element declares {count} parameters but the blob is too short')
        for _ in range(count):
            param_id, value = struct.unpack_from('>if', blob, offset)
            params[param_id] = value
            offset += 8
    return params


def build_classinfo(gains: Sequence[float], name: str = '', num_bands: int = 31) -> Dict[str, Any]:
    """Build the AU ClassInfo dictionary for a set of band gains."""
    return {
        'manufacturer': AU_MANUFACTURER,
        'subtype': AU_SUBTYPE,
        'type': AU_TYPE,
        'version': AU_VERSION,
        'name': name,
        'Num EQ Bands': num_bands,
        'data': encode_param_blob(gains, num_bands=num_bands),
    }


def serialize_classinfo(classinfo: Dict[str, Any]) -> bytes:
    """Serialize to the binary plist v1.0 that -[SWNEqualizerPreset setClassInfo:] writes."""
    return plistlib.dumps(classinfo, fmt=plistlib.FMT_BINARY)


def parse_classinfo(data: bytes) -> Dict[str, Any]:
    """Parse and validate a stored preset payload."""
    classinfo = plistlib.loads(data)
    if not isinstance(classinfo, dict):
        raise ValueError(f'expected a dictionary, got {type(classinfo).__name__}')
    missing = CLASSINFO_KEYS - set(classinfo)
    if missing:
        raise ValueError(f'ClassInfo is missing keys: {sorted(missing)}')
    actual = (classinfo['type'], classinfo['subtype'], classinfo['manufacturer'])
    expected = (AU_TYPE, AU_SUBTYPE, AU_MANUFACTURER)
    if actual != expected:
        raise ValueError(f'ClassInfo is not AUGraphicEQ: {actual} != {expected}')
    return classinfo


def gains_from_preset_data(data: bytes) -> Tuple[List[float], int]:
    """Read a stored preset back into (31 band gains, band count)."""
    classinfo = parse_classinfo(data)
    params = decode_param_blob(classinfo['data'])
    gains = [params[i] for i in range(len(ISO_CENTERS_31))]
    return gains, int(classinfo['Num EQ Bands'])


def describe_gains(gains: Sequence[float]) -> str:
    """Human-readable band table, used by reports and --probe-dump."""
    lines = [f'{"Hz":>8}  {"Gain (dB)":>9}']
    for centre, gain in zip(ISO_CENTERS_31, gains):
        lines.append(f'{centre:>8g}  {gain:>+9.2f}')
    return '\n'.join(lines)
