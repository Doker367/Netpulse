"""Schemas Pydantic del contrato de colectores remotos."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

Metric = Literal["reachable", "latency_ms", "packet_loss_pct"]
ProbeKind = Literal["icmp", "tcp"]


def _require_tz(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp sin zona horaria")
    return value


class RemoteSampleIn(BaseModel):
    """Una fila de medición; la clave única es (device, ts, metric)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(
        ..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    ts: datetime
    metric: Metric
    value_num: float | None = None
    value_bool: StrictBool | None = None
    ok: StrictBool
    probe_kind: ProbeKind
    error: str | None = Field(default=None, max_length=200)

    @field_validator("ts")
    @classmethod
    def _ts_tz(cls, value: datetime) -> datetime:
        return _require_tz(value)  # type: ignore[return-value]

    @field_validator("error")
    @classmethod
    def _error_clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _semantics(self) -> "RemoteSampleIn":
        if self.value_num is not None and not math.isfinite(self.value_num):
            raise ValueError("value_num no es finito")
        if not self.ok:
            if self.metric != "reachable":
                raise ValueError("ok=false solo se admite en metric='reachable'")
            if self.value_bool is not None or self.value_num is not None:
                raise ValueError("ok=false exige value_bool=null y value_num=null")
            if not self.error:
                raise ValueError("ok=false exige error no vacío")
            return self

        if self.metric == "reachable":
            if self.value_bool is None:
                raise ValueError("reachable exige value_bool")
            if self.value_num is not None:
                raise ValueError("reachable no admite value_num")
            if self.value_bool and self.error is not None:
                raise ValueError("reachable=true no admite error")
            if not self.value_bool and not self.error:
                raise ValueError("reachable=false exige error")
        elif self.metric == "latency_ms":
            if self.value_bool is not None:
                raise ValueError("latency_ms no admite value_bool")
            if self.value_num is None or self.value_num < 0:
                raise ValueError("latency_ms exige value_num >= 0")
        elif self.metric == "packet_loss_pct":
            if self.value_bool is not None:
                raise ValueError("packet_loss_pct no admite value_bool")
            if self.value_num is None or not (0 <= self.value_num <= 100):
                raise ValueError("packet_loss_pct exige value_num entre 0 y 100")
            if self.probe_kind != "icmp":
                raise ValueError("packet_loss_pct solo se admite con probe_kind='icmp'")
        return self


class BufferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buffer_epoch: str | None = Field(default=None, max_length=64)
    pending: int = Field(default=0, ge=0)
    oldest_pending_ts: datetime | None = None
    dropped_total: int | None = Field(default=None, ge=0)
    gap_since: datetime | None = None
    paused_reason: str | None = Field(default=None, max_length=64)

    @field_validator("oldest_pending_ts", "gap_since")
    @classmethod
    def _ts_tz(cls, value: datetime | None) -> datetime | None:
        return _require_tz(value)


class SyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_error: str | None = Field(default=None, max_length=300)
    last_error_at: datetime | None = None
    consecutive_failures: int | None = Field(default=None, ge=0)
    auth_state: Literal["ok", "error", "unknown"] | None = None

    @field_validator("last_error_at")
    @classmethod
    def _ts_tz(cls, value: datetime | None) -> datetime | None:
        return _require_tz(value)


class RemoteIngestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    collector_version: str | None = Field(default=None, max_length=64)
    probe_interval_s: int | None = Field(default=None, ge=1, le=86400)
    sync_batch_id: str | None = Field(default=None, max_length=64)
    sent_at: datetime
    buffer: BufferIn
    sync: SyncIn | None = None
    # Se validan una a una en ingest_svc para poder rechazar solo las inválidas
    # y devolver su triplete al colector (contrato de rechazo parcial).
    samples: list[dict[str, Any]] = Field(default_factory=list, max_length=500)

    @field_validator("sent_at")
    @classmethod
    def _ts_tz(cls, value: datetime) -> datetime:
        return _require_tz(value)  # type: ignore[return-value]
