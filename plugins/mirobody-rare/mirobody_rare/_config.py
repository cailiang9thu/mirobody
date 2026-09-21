"""Configuration: `config.yaml` beside the package, env overrides, logging level."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def load() -> dict:
    p = Path(os.environ.get("MIROBODY_RARE_CONFIG", _HERE / "config.yaml"))
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if os.environ.get("MIROBODY_RARE_ONTOLOGY_DIR"):
        cfg["ontology_dir"] = os.environ["MIROBODY_RARE_ONTOLOGY_DIR"]
    if os.environ.get("MIROBODY_RARE_CACHE_DIR"):
        cfg["cache_dir"] = os.environ["MIROBODY_RARE_CACHE_DIR"]
    if os.environ.get("MIROBODY_RARE_REFERENCE_BACKEND"):
        cfg.setdefault("reference", {})["backend"] = os.environ["MIROBODY_RARE_REFERENCE_BACKEND"]
    cfg["ontology_dir"] = str(Path(cfg["ontology_dir"]).expanduser())
    cfg["cache_dir"] = str(Path(cfg.get("cache_dir", "~/.cache/mirobody_rare")).expanduser())
    return cfg


def ontology_dir() -> Path:
    return Path(load()["ontology_dir"])


def cache_dir() -> Path:
    d = Path(load()["cache_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(verbose: int | None = None) -> None:
    v = load().get("verbose", 1) if verbose is None else verbose
    level = logging.WARNING if v <= 0 else logging.INFO if v == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
