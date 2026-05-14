from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from roadmatch.errors import ConfigError


def load_config(path: str) -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError(
            "YAML config requires PyYAML. Install the project with `pip install -e .`."
        ) from exc
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ConfigError("Config root must be a mapping")
    return payload


def get_config(config: Dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def required_config(config: Dict[str, Any], keys: Iterable[str]) -> Any:
    missing = ".".join(keys)
    value = get_config(config, keys, default=None)
    if value is None:
        raise ConfigError(f"Missing required config value: {missing}")
    return value


def project_path(config: Dict[str, Any], section_key: str, default_name: Optional[str] = None) -> Path:
    data_dir = Path(str(required_config(config, ["paths", "data_dir"])))
    value = get_config(config, ["paths", section_key], default_name)
    if value is None:
        raise ConfigError(f"Missing path config: paths.{section_key}")
    return data_dir / str(value)


def output_path(config: Dict[str, Any], filename: str) -> Path:
    output_dir = Path(str(required_config(config, ["paths", "output_dir"])))
    return output_dir / filename


def ensure_project_dirs(config: Dict[str, Any]) -> None:
    Path(str(required_config(config, ["paths", "data_dir"]))).mkdir(parents=True, exist_ok=True)
    Path(str(required_config(config, ["paths", "output_dir"]))).mkdir(parents=True, exist_ok=True)
