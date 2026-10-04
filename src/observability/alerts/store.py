"""
Advanced Alert Store for LiuHao AI OS

Provides:
- Alert persistence with JSON storage
- Alert rule management
- Alert state tracking and history
- Integrity verification
"""

from pathlib import Path
import json
import logging
import os
import time
from typing import Dict, List, Optional, Any
from src.common.hash_chain import compute_hash, verify_declared_hash, DEFAULT_HASH_ALG
from .models import Alert, AlertRule, AlertThreshold, AlertSeverity, AlertState, AlertType
from .sinks import dispatch_alert

logger = logging.getLogger(__name__)

# Tests redirect the alert store to a temp dir via this env var (REDIR). The
# production default is unchanged; this only makes the path overridable.
ALERTS_STORE_PATH_ENV = "LIUHAO_ALERTS_STORE_PATH"
DEFAULT_ALERTS_STORE_PATH = "data/observability/alerts.json"


class AlertStore:
    """
    Alert storage with integrity verification.

    Features:
    - JSON-based persistent storage
    - Hash chain for tamper evidence
    - Alert rule management
    - Alert state tracking
    - History and resolution tracking
    """

    def __init__(self, storage_path: str = None):
        if storage_path is None:
            storage_path = os.environ.get(
                ALERTS_STORE_PATH_ENV, DEFAULT_ALERTS_STORE_PATH
            )
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._alerts: Dict[str, Alert] = {}
        self._rules: Dict[str, AlertRule] = {}
        self._hash_chain: Optional[List[str]] = None
        self._hash_alg: str = DEFAULT_HASH_ALG
        self._load()

    def _load(self) -> None:
        """Load existing alerts and rules from storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._alerts = {
                    k: Alert.from_dict(v) for k, v in data.get("alerts", {}).items()
                }
                self._rules = {
                    k: AlertRule.from_dict(v) for k, v in data.get("rules", {}).items()
                }
                self._hash_chain = data.get("hash_chain")
                # P0-8c: NO default fallback. An envelope that declares
                # no algorithm is UNVERIFIED, never silently read as sha256.
                self._hash_alg = data.get("hash_alg")
                # Rebuild hash chain if missing or inconsistent
                if self._hash_alg and (
                    not self._hash_chain
                    or len(self._hash_chain) != len(self._alerts) + len(self._rules)
                ):
                    self._hash_chain = None
                    self._build_hash_chain()

            except Exception as e:
                print(f"Warning: Failed to load alert store: {e}")
                self._alerts = {}
                self._rules = {}
                self._hash_chain = None
        else:
            self._alerts = {}
            self._rules = {}
            self._hash_chain = None

    def _build_hash_chain(self) -> None:
        """Build or rebuild the hash chain from current alerts and rules."""
        # P0-8c: refuse to (re)build a chain without a declared algorithm.
        # Defaulting to sha256 is exactly what the containment forbids;
        # the operator must stamp legacy data via the migration script.
        if not self._hash_alg:
            raise ValueError(
                "cannot build a hash chain without a declared hash_alg; "
                "run scripts/migrate_p08_hash_alg.py to stamp legacy data"
            )
        chain: List[str] = []
        # Combine and sort by ID for consistent ordering
        all_items: List[tuple[str, Any]] = []

        for eid, alert in self._alerts.items():
            all_items.append((eid, alert))
        for rid, rule in self._rules.items():
            all_items.append((rid, rule))

        all_items.sort(key=lambda x: x[0])

        prev_hash = "genesis"
        for eid, item in all_items:
            if isinstance(item, Alert):
                item_dict = item.to_dict()
                item_dict["prev_hash"] = prev_hash
                item_data = json.dumps(item_dict, sort_keys=True, separators=(",", ":"))
                item_hash = compute_hash(self._hash_alg, item_data.encode())
                chain.append(item_hash)
            elif isinstance(item, AlertRule):
                item_dict = item.to_dict()
                item_dict["prev_hash"] = prev_hash
                item_data = json.dumps(item_dict, sort_keys=True, separators=(",", ":"))
                item_hash = compute_hash(self._hash_alg, item_data.encode())
                chain.append(item_hash)
            prev_hash = chain[-1] if chain else "genesis"

        self._hash_chain = chain
        self._save()

    def _save(self) -> None:
        """Persist alerts and rules to storage with hash chain."""
        if self._hash_alg and self._hash_chain is None:
            self._build_hash_chain()

        data = {
            "version": 1,
            "saved_at": time.time(),
            "hash_alg": self._hash_alg,
            "hash_chain": self._hash_chain,
            "alerts": {k: v.to_dict() for k, v in self._alerts.items()},
            "rules": {k: v.to_dict() for k, v in self._rules.items()},
        }
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    # ==================== Alert Operations ====================

    def emit_alert(self, alert: Alert, dispatch: bool = True) -> str:
        """
        Emit (store + deliver) an alert.

        The alert is first persisted to the JSON store (the fail-closed
        guarantee: the record always survives). It is then dispatched to the
        configured notification sink(s) — console log, and a webhook when
        ``LIUHAO_ALERT_WEBHOOK`` is set. A sink failure is logged and swallowed;
        it can never lose the persisted record or break the caller.

        Args:
            alert: The alert to store.
            dispatch: When ``True`` (default) the alert is delivered to the
                configured sink(s). When ``False`` the alert is persisted but
                NOT re-dispatched — used to refresh an alert that is already
                firing so a sustained breach cannot storm the sink.

        Returns:
            The alert ID
        """
        eid = alert.id
        self._alerts[eid] = alert
        self._save()
        # Real delivery: dispatch to the configured sink(s). File persistence
        # above is the fail-closed guarantee — even if dispatch fails, the alert
        # record is already on disk.
        if dispatch:
            try:
                dispatch_alert(alert)
            except Exception as exc:  # noqa: BLE001 - emit_alert must never raise
                logger.warning("alert dispatch failed (alert already persisted): %s", exc)
        return eid

    def get_alert(self, alert_id: str) -> Optional[Alert]:
        """Get a single alert by ID."""
        return self._alerts.get(alert_id)

    def list_alerts(self, filters: Optional[Dict[str, Any]] = None) -> List[Alert]:
        """
        List alerts with optional filters.

        Supported filter keys:
        - severity
        - state
        - source
        - alert_type
        - metric_name
        """
        alerts = list(self._alerts.values())

        if not filters:
            return alerts

        result = []
        for alert in alerts:
            match = True
            for key, value in filters.items():
                alert_value = getattr(alert, key, None)
                if alert_value != value:
                    match = False
                    break
            if match:
                result.append(alert)
        return result

    def list_by_severity(self, severity: AlertSeverity) -> List[Alert]:
        """List alerts by severity."""
        return [a for a in self._alerts.values() if a.severity == severity]

    def list_by_state(self, state: AlertState) -> List[Alert]:
        """List alerts by state."""
        return [a for a in self._alerts.values() if a.state == state]

    def list_firing(self) -> List[Alert]:
        """List currently firing alerts."""
        return [a for a in self._alerts.values() if a.state == AlertState.FIRING]

    def list_resolved(self) -> List[Alert]:
        """List resolved alerts."""
        return [a for a in self._alerts.values() if a.state == AlertState.RESOLVED]

    def resolve_alert(self, alert_id: str) -> bool:
        """Mark an alert as resolved."""
        if alert_id in self._alerts:
            self._alerts[alert_id].state = AlertState.RESOLVED
            self._alerts[alert_id].resolved_at = time.time()
            self._save()
            return True
        return False

    # ==================== Rule Operations ====================

    def add_rule(self, rule: AlertRule) -> str:
        """Add an alert rule."""
        eid = rule.id
        self._rules[eid] = rule
        self._save()
        return eid

    def get_rule(self, rule_id: str) -> Optional[AlertRule]:
        """Get a rule by ID."""
        return self._rules.get(rule_id)

    def list_rules(self) -> List[AlertRule]:
        """List all alert rules."""
        return list(self._rules.values())

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule."""
        if rule_id in self._rules:
            del self._rules[rule_id]
            self._save()
            return True
        return False

    # ==================== Integrity ====================

    def verify_integrity(self) -> bool:
        """Verify the hash chain integrity using the declared hash algorithm.

        Fail-closed: an unknown declared algorithm (or any hash mismatch)
        returns ``False`` — never raises, never silently falls back.
        """
        if not self._alerts and not self._rules:
            return True
        # P0-8c: data that declares no algorithm (or carries no chain) is
        # UNVERIFIED -- fail closed, never a silent pass and never a
        # silent fallback to sha256.
        if not self._hash_alg:
            return False

        chain = self._hash_chain
        if chain is None:
            return False

        all_items: List[tuple[str, Any]] = []
        for eid, alert in self._alerts.items():
            all_items.append((eid, alert))
        for rid, rule in self._rules.items():
            all_items.append((rid, rule))

        if len(chain) != len(all_items):
            return False

        all_items.sort(key=lambda x: x[0])

        prev_hash = "genesis"
        for i, (eid, item) in enumerate(all_items):
            if isinstance(item, Alert):
                item_dict = item.to_dict()
            elif isinstance(item, AlertRule):
                item_dict = item.to_dict()
            else:
                return False
            item_dict["prev_hash"] = prev_hash
            item_data = json.dumps(item_dict, sort_keys=True, separators=(",", ":"))
            ok, _reason = verify_declared_hash(self._hash_alg, item_data.encode(), chain[i])
            if not ok:
                return False
            prev_hash = chain[i]

        return True

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get alert statistics."""
        total = len(self._alerts)
        firing = len(self.list_firing())
        resolved = len(self.list_resolved())

        severity_counts: Dict[str, int] = {}
        for alert in self._alerts.values():
            severity_counts[alert.severity.name] = severity_counts.get(alert.severity.name, 0) + 1

        return {
            "total_alerts": total,
            "firing_alerts": firing,
            "resolved_alerts": resolved,
            "by_severity": severity_counts,
        }


# Module-level convenience functions
_default_store: Optional[AlertStore] = None


def get_alert_store() -> AlertStore:
    """Get the default alert store instance."""
    global _default_store
    if _default_store is None:
        _default_store = AlertStore()
    return _default_store


def emit_alert(alert: Alert, dispatch: bool = True) -> str:
    """Emit (store) an alert using the default store."""
    return get_alert_store().emit_alert(alert, dispatch=dispatch)


def get_alert(alert_id: str) -> Optional[Alert]:
    """Get a alert by ID using the default store."""
    return get_alert_store().get_alert(alert_id)


def list_alerts(filters: Optional[Dict[str, Any]] = None) -> List[Alert]:
    """List alerts using the default store."""
    return get_alert_store().list_alerts(filters)


def list_by_severity(severity: AlertSeverity) -> List[Alert]:
    """List alerts by severity using the default store."""
    return get_alert_store().list_by_severity(severity)


def list_firing() -> List[Alert]:
    """List firing alerts using the default store."""
    return get_alert_store().list_firing()


def resolve_alert(alert_id: str) -> bool:
    """Resolve an alert using the default store."""
    return get_alert_store().resolve_alert(alert_id)


__all__ = [
    "Alert",
    "AlertRule",
    "AlertThreshold",
    "AlertSeverity",
    "AlertState",
    "AlertType",
    "AlertStore",
    "get_alert_store",
    "emit_alert",
    "get_alert",
    "list_alerts",
    "list_by_severity",
    "list_firing",
    "resolve_alert",
]
