"""Configuration loading for the CIL.

Config-driven from day one (CCS-001 principle): values come from a YAML file and
are overridable by environment variables. Precedence, highest first:

    init kwargs  >  env vars (CIL_*)  >  .env file  >  YAML config file  >  secrets

No scoring/policy thresholds are hardcoded — those live in their own config
artifacts loaded at runtime by the relevant engines. This module holds only the
process/runtime settings the app needs to boot.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_CONFIG_FILE = "config/default.yaml"


class Settings(BaseSettings):
    """Runtime settings for the CIL service."""

    model_config = SettingsConfigDict(
        env_prefix="CIL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "cil"
    env: Literal["local", "ci", "staging", "production"] = "local"
    log_level: str = "INFO"

    host: str = "0.0.0.0"  # container binds all interfaces by design
    port: int = 8000

    # Telemetry ingest loop (WAN monitoring). When enabled, the app samples the
    # simulator and persists to the telemetry store on startup.
    telemetry_enabled: bool = True
    telemetry_interval_s: float = 1.0
    telemetry_db_path: str = "data/telemetry.db"

    # Application monitoring loop (clinical endpoint liveness). Shares the
    # operational DB (telemetry_db_path).
    app_monitoring_enabled: bool = True
    app_monitoring_interval_s: float = 5.0

    # Data platform (EPIC-03). The training repository lives in its OWN DB file
    # the retention sweeper is never pointed at (indefinite UC2 dataset).
    data_platform_enabled: bool = True
    training_db_path: str = "data/training.db"
    # Telemetry window around every continuity event: ±15 min target, ±5 min floor.
    window_before_s: float = 900.0
    window_after_s: float = 900.0
    window_min_radius_s: float = 300.0
    # Operational retention (24 months); training is indefinite (no setting — by design).
    retention_enabled: bool = True
    operational_retention_days: int = 730
    retention_sweep_interval_s: float = 3600.0
    # Negative-class sampling for the training set.
    no_action_sample_interval_s: float = 60.0
    # Labeling config artifacts (CIL-303). SLA thresholds live in CCS-001.
    ccs_tiers_path: str = "config/ccs_tiers.yaml"
    labeling_config_path: str = "config/labeling.yaml"

    # Scoring (EPIC-04: CQS + CCS). Weights live in their config artifacts;
    # CCS tiers + SLA threshold come from CCS-001 (ccs_tiers_path).
    scoring_enabled: bool = True
    scoring_interval_s: float = 1.0
    cqs_config_path: str = "config/cqs.yaml"
    ccs_config_path: str = "config/ccs.yaml"
    scoring_site_id: str = "site"
    scoring_primary_path: str = "modem-a"

    # Clinical endpoints monitored for continuity (CCS-APP-001). Config-driven so the
    # fleet isn't hardcoded; falls back to a built-in default if the file is absent.
    clinical_endpoints_path: str = "config/clinical_endpoints.yaml"

    # Path to the YAML config file. Read from the environment so the file source
    # can be pointed elsewhere per environment without code changes.
    config_file: str = DEFAULT_CONFIG_FILE

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = os.getenv("CIL_CONFIG_FILE", DEFAULT_CONFIG_FILE)
        yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached)."""
    return Settings()
