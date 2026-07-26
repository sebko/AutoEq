"""Loading of the AUGraphicEQ band configuration for AutoEq's fixed band optimizer."""

import copy
from pathlib import Path
from typing import Any, Dict

import yaml

BAND_CONFIG_PATH = Path(__file__).parent / 'configs' / 'augraphiceq_31.yaml'


def load_band_config(path: Path = BAND_CONFIG_PATH) -> Dict[str, Any]:
    """Load the band config, with centre frequencies coerced to float.

    Always returns a fresh copy: autoeq.peq.PEQ.from_dict and optimize_fixed_band_eq both fill
    defaults into the config in place, so a shared dict would leak state between fits.
    """
    with open(path) as fh:
        config = yaml.safe_load(fh)
    for band in config['filters']:
        band['fc'] = float(band['fc'])
    return copy.deepcopy(config)
