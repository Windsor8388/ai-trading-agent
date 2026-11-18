"""Offline maintenance diagnostics for the research platform.

The doctor is deliberately a local, non-invasive inspection tool. It does not resolve
secrets, contact Supabase/Vercel/a market venue, alter a database, start a worker, or
assert that any feed is live. Its job is to detect repository/configuration drift before
an operator starts a separately approved backend process.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import tomllib
from typing import Callable

from . import __version__
from .knowledge import KnowledgeBase
from .research.ledger import ResearchLedger


class DoctorState(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    check_id: str
    state: DoctorState
    summary: str
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    version: str
    generated_at: str
    checks: tuple[DoctorCheck, ...]

    @property
    def has_failures(self) -> bool:
        return any(check.state == DoctorState.FAIL for check in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(check.state == DoctorState.WARN for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "healthy": not self.has_failures,
            "warnings_present": self.has_warnings,
            "checks": [asdict(check) | {"state": check.state.value} for check in self.checks],
            "safety": (
                "Offline local diagnostics only: no secrets were resolved and no external "
                "service, market-data provider, broker, or order-routing endpoint was contacted."
            ),
        }


class PlatformDoctor:
    """Run deterministic maintenance checks against one repository checkout.

    A warning is intentionally distinct from a failure: the absence of an optional
    local research ledger is not a broken platform, while a malformed knowledge
    registry, version drift, missing audit migration, or raw secret assignment in the
    committed template is a failure.
    """

    version = "0.1.0"
    _registry_files = ("concepts.json", "sources.json", "strategies.json")
    _required_migration_markers = (
        "create table if not exists public.ati_order_intents",
        "create table if not exists public.ati_order_events",
        "create table if not exists public.ati_order_states",
        "create or replace function public.ati_record_order_event",
        "enable row level security",
        "grant execute on function",
        "to service_role",
    )
    _raw_secret_names = frozenset({
        "BINGX_API_KEY",
        "BINGX_SECRET_KEY",
        "GEMINI_API_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "OANDA_ACCESS_TOKEN",
    })

    def __init__(self, repository_root: Path | str | None = None) -> None:
        if repository_root is not None:
            self.repository_root = Path(repository_root)
            return
        # An installed wheel does not carry dashboard/docs/migrations. Prefer a real
        # checkout in the caller's current directory, then fall back to the source
        # tree when the package is executed directly from that checkout.
        current_directory = Path.cwd()
        source_tree = Path(__file__).resolve().parents[2]
        self.repository_root = current_directory if (current_directory / "pyproject.toml").is_file() and (current_directory / "knowledge").is_dir() else source_tree

    def _check(self, check_id: str, summary: str, action: Callable[[], str]) -> DoctorCheck:
        try:
            detail = action()
        except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            return DoctorCheck(check_id, DoctorState.FAIL, summary, f"{type(exc).__name__}: {exc}")
        return DoctorCheck(check_id, DoctorState.PASS, summary, detail)

    def _knowledge_registry(self) -> str:
        registry = KnowledgeBase.from_directory(self.repository_root / "knowledge")
        return f"Loaded knowledge schema {registry.version} updated {registry.updated_at}; source/relation integrity passed."

    def _knowledge_bundle(self) -> str:
        canonical = self.repository_root / "knowledge"
        bundled = self.repository_root / "src" / "trading_intelligence" / "knowledge_data"
        mismatches = []
        for name in self._registry_files:
            left = canonical / name
            right = bundled / name
            if not left.is_file() or not right.is_file():
                raise ValueError(f"Missing canonical or bundled registry file: {name}")
            if hashlib.sha256(left.read_bytes()).digest() != hashlib.sha256(right.read_bytes()).digest():
                mismatches.append(name)
        if mismatches:
            raise ValueError(f"Bundled knowledge registry differs from canonical files: {', '.join(mismatches)}")
        return "Canonical and packaged knowledge registry files have matching SHA-256 content."

    def _version_alignment(self) -> str:
        pyproject = self.repository_root / "pyproject.toml"
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        declared = payload.get("project", {}).get("version")
        if declared != __version__:
            raise ValueError(f"pyproject version {declared!r} does not match package version {__version__!r}.")
        return f"Package and pyproject versions agree at {__version__}."

    def _runtime_template(self) -> str:
        template = self.repository_root / "config" / "runtime.env.example"
        found_raw = []
        for raw_line in template.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _value = line.split("=", 1)
            if name in self._raw_secret_names:
                found_raw.append(name)
        if found_raw:
            raise ValueError(f"Committed runtime template assigns raw credential variable(s): {', '.join(found_raw)}")
        text = template.read_text(encoding="utf-8")
        expected_locators = ("BINGX_API_KEY_REF=sm://env/", "BINGX_API_SECRET_REF=sm://env/")
        if any(marker not in text for marker in expected_locators):
            raise ValueError("Runtime template is missing mandatory server-side BingX secret-reference examples.")
        return "Runtime template has no raw secret assignments and preserves server-side secret-reference examples."

    def _supabase_migration(self) -> str:
        migrations = sorted((self.repository_root / "supabase" / "migrations").glob("*_trading_intelligence_durable_order_audit.sql"))
        if len(migrations) != 1:
            raise ValueError("Expected exactly one durable-order-audit Supabase migration.")
        document = migrations[0].read_text(encoding="utf-8").casefold()
        missing = [marker for marker in self._required_migration_markers if marker not in document]
        if missing:
            raise ValueError(f"Supabase durable-audit migration is missing safeguards: {', '.join(missing)}")
        digest = hashlib.sha256(migrations[0].read_bytes()).hexdigest()
        return f"Validated {migrations[0].name}; SHA-256={digest}."

    def _dashboard_boundary(self) -> str:
        dashboard = self.repository_root / "dashboard"
        configuration = json.loads((dashboard / "vercel.json").read_text(encoding="utf-8"))
        headers = {item["key"]: item["value"] for item in configuration["headers"][0]["headers"]}
        required_headers = {"X-Content-Type-Options", "Referrer-Policy", "Content-Security-Policy", "Permissions-Policy"}
        missing_headers = sorted(required_headers - set(headers))
        if missing_headers:
            raise ValueError(f"Dashboard deployment security headers missing: {', '.join(missing_headers)}")
        app = (dashboard / "app.js").read_text(encoding="utf-8")
        forbidden = [name for name in self._raw_secret_names if name in app]
        if forbidden:
            raise ValueError(f"Browser dashboard contains forbidden secret variable name(s): {', '.join(sorted(forbidden))}")
        return "Dashboard headers and browser-secret boundary passed local inspection."

    def _ledger(self, ledger_path: Path | None) -> DoctorCheck:
        if ledger_path is None:
            return DoctorCheck(
                "research_ledger",
                DoctorState.SKIPPED,
                "Research ledger integrity",
                "No --ledger path supplied; no local research ledger was inspected.",
            )
        try:
            entries = ResearchLedger(ledger_path).verify()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return DoctorCheck("research_ledger", DoctorState.FAIL, "Research ledger integrity", f"{type(exc).__name__}: {exc}")
        if not entries:
            return DoctorCheck("research_ledger", DoctorState.WARN, "Research ledger integrity", "Ledger exists or was addressable but contains no entries.")
        return DoctorCheck("research_ledger", DoctorState.PASS, "Research ledger integrity", f"Verified {len(entries)} hash-chained ledger entries.")

    def inspect(self, *, ledger_path: Path | str | None = None, now: datetime | None = None) -> DoctorReport:
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("Doctor timestamp must be timezone-aware.")
        ledger = Path(ledger_path) if ledger_path is not None else None
        checks = (
            self._check("knowledge_registry", "Knowledge registry integrity", self._knowledge_registry),
            self._check("knowledge_bundle", "Bundled knowledge parity", self._knowledge_bundle),
            self._check("version_alignment", "Package version alignment", self._version_alignment),
            self._check("runtime_template", "Committed secret-template boundary", self._runtime_template),
            self._check("supabase_migration", "Supabase durable-audit migration safeguards", self._supabase_migration),
            self._check("dashboard_boundary", "Dashboard security boundary", self._dashboard_boundary),
            self._ledger(ledger),
        )
        return DoctorReport(
            version=self.version,
            generated_at=observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            checks=checks,
        )
