"""Source protocol and the registry that loads adapters from config."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..fetch import Fetcher
from ..models import Event

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "sources.yaml"


@dataclass(frozen=True)
class SourceConfig:
    id: str
    adapter: str
    name: str
    url: str
    address: str = ""
    enabled: bool = True

    # Set for sources where an empty result is a normal state rather than a
    # broken parser -- e.g. BNHS lists nationally and often has nothing in
    # Delhi. `doctor` skips its zero-events check for these.
    allow_empty: bool = False

    # Free-form per-source settings. Keeps adapter-specific knobs out of the
    # shared schema.
    options: dict = field(default_factory=dict)


class BaseSource:
    """Adapters subclass this and implement ``fetch``.

    An adapter's only job is to return well-formed ``Event`` objects. It must not
    touch the database, and should raise on genuine failure rather than returning
    an empty list -- an empty list is how `doctor` detects a silently broken
    parser, so it needs to mean "the venue has nothing on", not "I crashed".
    """

    def __init__(self, config: SourceConfig):
        self.config = config

    @property
    def id(self) -> str:
        return self.config.id

    def fetch(self, fetcher: Fetcher) -> list[Event]:
        raise NotImplementedError

    def base_fields(self) -> dict[str, str]:
        """Venue identity every event from this source inherits."""
        return {
            "source_id": self.config.id,
            "venue": self.config.name,
            "address": self.config.address,
        }


def load_configs(path: Path | str = CONFIG_PATH) -> list[SourceConfig]:
    data = yaml.safe_load(Path(path).read_text())
    return [SourceConfig(**entry) for entry in data["sources"]]


def load_source(config: SourceConfig) -> BaseSource:
    module = importlib.import_module(f"delhi_events.sources.{config.adapter}")
    return module.Source(config)


def load_all(path: Path | str = CONFIG_PATH, only: list[str] | None = None) -> list[BaseSource]:
    configs = [c for c in load_configs(path) if c.enabled]
    if only:
        configs = [c for c in configs if c.id in only]
    return [load_source(c) for c in configs]
