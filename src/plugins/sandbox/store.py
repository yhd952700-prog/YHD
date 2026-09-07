"""
Plugin Sandbox Store for LiuHao AI OS

Provides:
- Sandbox execution context persistence
- Result storage
- Status tracking
- Integrity verification
"""

from pathlib import Path
import json
import hashlib
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

from .models import SandboxExecutionContext, SandboxResult


class PluginSandboxStore:
    """
    Plugin sandbox execution store with integrity verification.

    Features:
    - JSON-based persistent storage
    - Hash chain for tamper evidence
    - Execution context and result tracking
    - Status management
    """

    def __init__(self, storage_path: str = "data/plugins/sandbox/executions.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._contexts: Dict[str, SandboxExecutionContext] = {}
        self._results: Dict[str, SandboxResult] = {}
        self._hash_chain: Optional[List[str]] = None
        self._load()

    def _load(self) -> None:
        """Load existing executions from storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._contexts = {
                    k: SandboxExecutionContext.from_dict(v) for k, v in data.get("contexts", {}).items()
                }
                self._results = {
                    k: SandboxResult.from_dict(v) for k, v in data.get("results", {}).items()
                }
                self._hash_chain = data.get("hash_chain")
                if not self._hash_chain or len(self._hash_chain) != len(self._contexts):
                    self._hash_chain = None
                    self._build_hash_chain()
            except Exception as e:
                print(f"Warning: Failed to load sandbox store: {e}")
                self._contexts = {}
                self._results = {}
                self._hash_chain = None
        else:
            self._contexts = {}
            self._results = {}
            self._hash_chain = None

    def _build_hash_chain(self) -> None:
        """Build or rebuild the hash chain from current contexts."""
        chain: List[str] = []
        sorted_ids = sorted(self._contexts.keys())
        prev_hash = "genesis"
        for eid in sorted_ids:
            context = self._contexts[eid]
            context_dict = context.to_dict()
            context_dict["prev_hash"] = prev_hash
            context_data = json.dumps(context_dict, sort_keys=True, separators=(",", ":"))
            context_hash = hashlib.sha256(context_data.encode()).hexdigest()
            chain.append(context_hash)
            prev_hash = context_hash

        self._hash_chain = chain
        self._save()

    def _save(self) -> None:
        """Persist contexts and results to storage."""
        if self._hash_chain is None:
            self._build_hash_chain()

        data = {
            "version": 1,
            "saved_at": time.time(),
            "hash_chain": self._hash_chain,
            "contexts": {k: v.to_dict() for k, v in self._contexts.items()},
            "results": {k: v.to_dict() for k, v in self._results.items()},
        }
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    # ==================== Context Operations ====================

    def create_context(self, context: SandboxExecutionContext) -> str:
        """
        Create (store) a sandbox execution context.

        Args:
            context: The context to store

        Returns:
            The execution ID
        """
        eid = context.execution_id
        self._contexts[eid] = context
        self._save()
        return eid

    def get_context(self, execution_id: str) -> Optional[SandboxExecutionContext]:
        """Get a context by execution ID."""
        return self._contexts.get(execution_id)

    def list_contexts(self, filters: Optional[Dict[str, Any]] = None) -> List[SandboxExecutionContext]:
        """List contexts with optional filters."""
        contexts = list(self._contexts.values())

        if not filters:
            return contexts

        result = []
        for ctx in contexts:
            match = True
            for key, value in filters.items():
                if hasattr(ctx, key):
                    ctx_val = getattr(ctx, key, None)
                    if ctx_val != value:
                        match = False
                        break
                else:
                    match = False
                    break
            if match:
                result.append(ctx)
        return result

    def update_context(self, context: SandboxExecutionContext) -> str:
        """Update an existing context."""
        eid = context.execution_id
        self._contexts[eid] = context
        self._save()
        return eid

    # ==================== Result Operations ====================

    def store_result(self, result: SandboxResult) -> str:
        """
        Store a sandbox execution result.

        Args:
            result: The result to store

        Returns:
            The execution ID
        """
        eid = result.execution_id
        self._results[eid] = result
        # Also update the associated context's status
        if eid in self._contexts:
            self._contexts[eid].status = result.success.__class__.__name__.lower() if hasattr(result.success, '__class__') else str(result.success)
            self._contexts[eid].exit_code = result.exit_code if hasattr(result, 'exit_code') else None
            self._contexts[eid].error_message = result.error
            self._contexts[eid].output = result.output
            self._contexts[eid].end_time = datetime.now()
        self._save()
        return eid

    def get_result(self, execution_id: str) -> Optional[SandboxResult]:
        """Get a result by execution ID."""
        return self._results.get(execution_id)

    # ==================== Integrity ====================

    def verify_integrity(self) -> bool:
        """Verify the hash chain integrity."""
        if not self._contexts or not self._hash_chain:
            return True

        chain = self._hash_chain
        if len(chain) != len(self._contexts):
            return False

        sorted_ids = sorted(self._contexts.keys())
        prev_hash = "genesis"
        for i, eid in enumerate(sorted_ids):
            context = self._contexts[eid]
            context_dict = context.to_dict()
            context_dict["prev_hash"] = prev_hash
            context_data = json.dumps(context_dict, sort_keys=True, separators=(",", ":"))
            expected_hash = hashlib.sha256(context_data.encode()).hexdigest()

            if expected_hash != chain[i]:
                return False

            prev_hash = expected_hash

        return True

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get sandbox statistics."""
        contexts = list(self._contexts.values())
        results = list(self._results.values())

        status_counts: Dict[str, int] = {}
        for ctx in contexts:
            status_counts[ctx.status] = status_counts.get(ctx.status, 0) + 1

        success_count = sum(1 for r in results if r.success)
        failure_count = sum(1 for r in results if not r.success)

        return {
            "total_executions": len(contexts),
            "by_status": status_counts,
            "total_results": len(results),
            "successful": success_count,
            "failed": failure_count,
        }


# Module-level convenience functions
_default_store: Optional[PluginSandboxStore] = None


def get_sandbox_store() -> PluginSandboxStore:
    """Get the default sandbox store instance."""
    global _default_store
    if _default_store is None:
        _default_store = PluginSandboxStore()
    return _default_store


def create_context(context: SandboxExecutionContext) -> str:
    """Create a sandbox execution context using the default store."""
    return get_sandbox_store().create_context(context)


def store_result(result: SandboxResult) -> str:
    """Store a sandbox result using the default store."""
    return get_sandbox_store().store_result(result)
