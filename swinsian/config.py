"""Loading of the AUGraphicEQ band configuration for AutoEq's fixed band optimizer."""

from pathlib import Path
from typing import Any, Dict

import yaml

BAND_CONFIG_PATH = Path(__file__).parent / 'configs' / 'augraphiceq_31.yaml'


def load_band_config(path: Path = BAND_CONFIG_PATH) -> Dict[str, Any]:
    """Load the band config, with centre frequencies coerced to float.

    autoeq.peq.PEQ.from_dict fills defaults into each band dict in place, so callers must never
    share one config across fits. Re-reading the YAML per call is what provides that isolation:
    do not cache the parsed result here.
    """
    with open(path) as fh:
        config = yaml.safe_load(fh)
    for band in config['filters']:
        band['fc'] = float(band['fc'])
    return config
