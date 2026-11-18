"""Server-side Gemini chart commentary bounded to advisory output.

Gemini receives a compact, completed-candle chart brief and may describe observations
or uncertainty.  It cannot call a venue, alter engine evidence, set risk limits, emit a
trade signal, or approve a live order.  The deterministic engines and central/risk
boundaries remain authoritative.

The optional ``google-genai`` SDK is imported only when a configured server-side call is
made.  No API key is loaded from the dashboard or serialized in an advisory result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Protocol, Sequence

from ..data import validate_closed_candles
from ..integrations import GeminiIntegrationPolicy, GeminiRole
from ..models import Decision, EngineReport, MarketSnapshot
from ..runtime.secrets import SecretResolver


@dataclass(frozen=True, slots=True)
class GeminiChartAnalysisConfig:
    policy: GeminiIntegrationPolicy
    model_id: str
    maximum_candles: int = 120

    def __post_init__(self) -> None:
        if self.policy.role != GeminiRole.ADVISORY_ONLY:
            raise ValueError("Gemini chart analysis requires explicit ADVISORY_ONLY policy; disabled/explanation roles cannot analyze charts.")
        if self.policy.credential_ref is None:
            raise ValueError("Gemini chart analysis requires a server-side API-key reference.")
        if not self.model_id or self.model_id.strip() != self.model_id:
            raise ValueError("Gemini model_id must be a non-empty trimmed identifier.")
        if not 10 <= self.maximum_candles <= 500:
            raise ValueError("Gemini maximum_candles must be between 10 and 500.")


@dataclass(frozen=True, slots=True)
class GeminiAdvisory:
    """Non-authoritative explanation with prompt lineage, never a trading decision."""

    provider: str
    model_id: str
    generated_at: datetime
    input_sha256: str
    text: str
    disclaimer: str = (
        "Gemini output is advisory chart commentary only. It is not a market-data source, probability, "
        "trade recommendation, risk approval, or execution instruction."
    )


class GeminiTextTransport(Protocol):
    def generate_text(self, *, model_id: str, api_key: str, contents: str) -> str:
        raise NotImplementedError


class GoogleGenAITransport:
    """Official Google Gen AI SDK transport, loaded lazily in the trusted backend process."""

    def generate_text(self, *, model_id: str, api_key: str, contents: str) -> str:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - depends on optional production extra
            raise RuntimeError("Gemini support requires: pip install 'trading-intelligence[gemini]'.") from exc
        client = genai.Client(api_key=api_key)
        try:
            response = client.models.generate_content(model=model_id, contents=contents)
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Gemini returned no usable text response.")
            return text.strip()
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()


class GeminiChartAnalyst:
    """Create a bounded, auditable Gemini chart brief from accepted closed candles only."""

    version = "0.1.0"
    _instruction = (
        "You are an advisory chart-analysis assistant inside an audited trading research system. "
        "Describe only what is supported by the provided completed OHLCV bars and independent-engine summaries. "
        "Do not claim live data, account access, fills, certainty, profit, or a guaranteed outcome. "
        "Do not issue BUY, SELL, close, size, leverage, or execution instructions. "
        "Return concise Arabic commentary with: observations, uncertainty, missing inputs, and invalidation questions. "
        "The deterministic central decision and risk gate are authoritative and cannot be changed by your response."
    )

    def __init__(self, config: GeminiChartAnalysisConfig, secret_resolver: SecretResolver, transport: GeminiTextTransport | None = None) -> None:
        self.config = config
        self.secret_resolver = secret_resolver
        self.transport = transport or GoogleGenAITransport()

    def _contents(
        self,
        snapshot: MarketSnapshot,
        timeframe: str,
        decision: Decision,
        reports: Sequence[EngineReport],
    ) -> str:
        candles = snapshot.candles(timeframe)
        quality = validate_closed_candles(candles, snapshot.as_of)
        if not quality.passed:
            raise ValueError(f"Gemini chart brief refuses invalid/unclosed candles: {'; '.join(quality.errors)}")
        selected = candles[-self.config.maximum_candles :]
        payload = {
            "boundary": "completed_ohlcv_only; advisory_only; no_execution_authority",
            "instrument": snapshot.feed.instrument,
            "instrument_kind": snapshot.feed.instrument_kind,
            "vendor_symbol": snapshot.feed.vendor_symbol,
            "timeframe": timeframe,
            "as_of_utc": snapshot.as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "feed": {
                "provider": snapshot.feed.provider,
                "mode": snapshot.feed.mode.value,
                "quality_status": snapshot.feed.quality_status,
                "live_feed_verified": snapshot.feed.live_feed_verified,
            },
            "central_decision": {
                "action": decision.action.value,
                "confidence_meaning": "de-correlated evidence agreement, not probability or expected return",
                "invalidation_conditions": list(decision.invalidation_conditions),
                "opposing_reasons": list(decision.opposing_reasons),
            },
            "engine_reports": [
                {
                    "engine": report.engine,
                    "version": report.version,
                    "timeframe": report.timeframe,
                    "direction": report.proposed_direction.value,
                    "confidence": report.confidence,
                    "regime": report.regime.value,
                    "invalidation_conditions": list(report.invalidation_conditions),
                }
                for report in reports
                if report.timeframe == timeframe
            ],
            "candles": [
                {
                    "open_time_utc": bar.open_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "close_time_utc": bar.close_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume,
                }
                for bar in selected
            ],
        }
        return f"{self._instruction}\n\nCHART_BRIEF_JSON:\n{json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"

    def analyze(
        self,
        snapshot: MarketSnapshot,
        timeframe: str,
        decision: Decision,
        reports: Sequence[EngineReport],
    ) -> GeminiAdvisory:
        contents = self._contents(snapshot, timeframe, decision, reports)
        credential_ref = self.config.policy.credential_ref
        if credential_ref is None:  # Defensive guard; GeminiChartAnalysisConfig already requires it.
            raise RuntimeError("Gemini advisory has no server-side credential reference.")
        api_key = self.secret_resolver.resolve(credential_ref)
        text = self.transport.generate_text(model_id=self.config.model_id, api_key=api_key, contents=contents)
        return GeminiAdvisory(
            provider="google_genai",
            model_id=self.config.model_id,
            generated_at=datetime.now(timezone.utc),
            input_sha256=hashlib.sha256(contents.encode("utf-8")).hexdigest(),
            text=text,
        )
