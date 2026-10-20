"""Where tasklog keeps its files and its small user configuration.

Public interface: store_path(), config_path(), load_config(), DEFAULTS.
Invariants: TASKLOG_STORE / TASKLOG_CONFIG override the default locations under ~/.tasklog; a missing config file means DEFAULTS;
unknown keys in the file are ignored.
"""
import json
import os

DEFAULTS = {"default_tags": [], "round_to": 1}


def _home():
    return os.path.join(os.path.expanduser("~"), ".tasklog")


def store_path():
    return os.environ.get("TASKLOG_STORE") or os.path.join(_home(), "store.json")


def config_path():
    return os.environ.get("TASKLOG_CONFIG") or os.path.join(_home(), "config.json")


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(config_path()) as f:
            data = json.load(f)
    except FileNotFoundError:
        return cfg
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    return cfg
