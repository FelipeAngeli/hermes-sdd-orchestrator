"""Durable, bounded executor for LoopDefinitions (opt-in; nothing in the controller calls it).

``LoopEngine.run`` holds the execution's exclusive runner lock and repeats:
recover interrupted work → honour cancellation → check budgets → pick the first
PENDING node whose dependencies are COMPLETED → check approvals and inputs →
checkpoint the attempt (RUNNING, idempotency key) → call the handler with a
timeout → checkpoint the result → retry, correct or halt. Every transition is
committed by ``LoopStore.commit`` before the next one starts.

Handlers are callables registered by the host program: ``handler(ctx) -> dict``
returning ``{"status": "SUCCEEDED"|"FAILED", "output": ..., "failure": ...}`` or
raising ``loop_policy.TransientFailure``/``FunctionalFailure``. Reconcilers
(``reconciler(ctx) -> {"applied": True|False|None, "output": ...}``) prove whether
an interrupted node applied its effect, so recovery never duplicates it.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import loop_correction
import loop_model
import loop_policy
import loop_state
from loop_store import LoopStore

Handler = Callable[["NodeContext"], Any]
BUDGET_HALTS = tuple(loop_state.BUDGET_REASONS)


class NodeContext:
    """What a handler sees for one attempt. ``checkpoint`` persists partial progress immediately."""

    def __init__(self, engine: "LoopEngine", execution: dict[str, Any], node: dict[str, Any], inputs: dict[str, Any]) -> None:
        record = execution["nodes"][node["id"]]
        attempt = record["attempts"][-1]
        self.execution_id = execution["execution_id"]
        self.node = node
        self.inputs = copy.deepcopy(inputs)
        self.partial = copy.deepcopy(record["partial"])
        self.correction = copy.deepcopy(record["correction"])
        self.human_input = record["human_input"]
        self.attempt = attempt["attempt"]
        self.generation = record["generation"]
        self.idempotency_key = attempt["idempotency_key"]
        self.attempt_id = attempt.get("attempt_id")
        self.deadline_seconds = node["timeout_seconds"]
        self._engine, self._execution, self._closed = engine, execution, False

    def checkpoint(self, partial: Any) -> None:
        with self._engine._write_lock:
            if self._closed:
                return
            # TypeError/ValueError/UnicodeEncodeError reaches the handler; nothing is saved.
            json.dumps(partial, sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
            self._execution["nodes"][self.node["id"]]["partial"] = copy.deepcopy(partial)
            self._engine._commit(self._execution, "NODE_PROGRESS", {"node": self.node["id"]})

    def cancel_requested(self) -> bool:
        return self._engine.store.read_cancel_request(self.execution_id) is not None

    def abandoned(self) -> bool:
        """True once the engine stopped waiting for this attempt (timeout); a cooperative handler stops then."""
        return self._closed


class LoopEngine:
    def __init__(self, store: Path | str, *, handlers: dict[str, Handler], reconcilers: dict[str, Handler] | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = LoopStore(store)
        self.handlers = dict(handlers)
        self.reconcilers = dict(reconcilers or {})
        self.clock, self.sleep = clock, sleep
        self._write_lock = threading.RLock()
        self._stale: dict[str, threading.Thread] = {}

    # -- lifecycle -----------------------------------------------------------------
    def start(self, definition: dict[str, Any], execution_id: str | None = None) -> dict[str, Any]:
        spec = loop_model.validate_definition(definition)
        sha = loop_model.digest(spec)
        identifier = execution_id or f"{spec['loop_id']}-{sha[:12]}"
        if self.store.exists(identifier):
            existing = self.store.load(identifier)
            if existing["definition_sha256"] != sha:
                raise loop_model.LoopError("EXECUTION_EXISTS", f"{identifier} exists with another definition.",
                                           "Pass a new --execution id; an execution never adopts a changed definition.")
            return existing
        execution = loop_state.new_execution(identifier, spec, sha, self.clock())
        try:
            self.store.create(identifier, spec, execution)
        except FileExistsError as error:
            raise loop_model.LoopError("EXECUTION_EXISTS", f"{identifier} was created concurrently.", "Run `status` on it.") from error
        return execution

    def load(self, execution_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        execution = self.store.load(execution_id)
        spec = self.store.load_definition(execution_id, execution["definition_sha256"])
        return execution, spec

    def run(self, execution_id: str, *, max_steps: int | None = None) -> dict[str, Any]:
        with self.store.runner_lock(execution_id):
            execution = self.store.load(execution_id, repair=True)
            spec = self.store.load_definition(execution_id, execution["definition_sha256"])
            loop_state.verify_integrity(execution, spec, self.store.events(execution_id))
            nodes = loop_model.node_map(spec)
            steps = 0
            while not loop_state.is_terminal(execution):
                if all(record["status"] == "COMPLETED" for record in execution["nodes"].values()):
                    self._commit(execution, *loop_state.finalize(execution, "COMPLETED", "COMPLETED", self.clock()))
                    self.store.clear_cancel_request(execution_id)
                    break
                if self._cancel_if_requested(execution):
                    break
                if execution["halt"] is not None:
                    break
                if self._recover_interrupted(execution, spec, nodes):
                    continue
                if max_steps is not None and steps >= max_steps:
                    execution["status"] = "PENDING"
                    self._commit(execution, "RUNNER_YIELDED", {"steps": steps})
                    break
                self._step(execution, spec, nodes)
                steps += 1
            return execution

    # -- one scheduling step ---------------------------------------------------------
    def _runnable(self, execution: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> str | None:
        for name in loop_model.topological_order(nodes):
            record = execution["nodes"][name]
            if record["status"] in ("PENDING", "RETRYING") and all(
                    execution["nodes"][dependency]["status"] == "COMPLETED" for dependency in nodes[name]["depends_on"]):
                return name
        return None

    def _budget_halt(self, execution: dict[str, Any]) -> str | None:
        limits, usage = execution["limits"], execution["usage"]
        if usage["node_runs"] >= limits["max_node_runs"]:
            return "RUN_BUDGET_EXHAUSTED"
        if limits["max_tokens"] is not None and usage["tokens"] >= limits["max_tokens"]:
            return "TOKEN_BUDGET_EXHAUSTED"
        if limits["max_wall_clock_seconds"] is not None and usage["active_seconds"] >= limits["max_wall_clock_seconds"]:
            return "TIME_BUDGET_EXHAUSTED"
        return None

    def _halt(self, execution: dict[str, Any], spec: dict[str, Any], reason: str, **kwargs: Any) -> None:
        if reason in BUDGET_HALTS and reason != "CORRECTION_BUDGET_EXHAUSTED":
            kwargs.setdefault("kind", "BUDGET")
        self._commit(execution, *loop_state.halt(execution, spec, reason, self.clock(), **kwargs))

    def _step(self, execution: dict[str, Any], spec: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> None:
        name = self._runnable(execution, nodes)
        if name is None:
            self._halt(execution, spec, "NO_RUNNABLE_NODE")
            return
        node, record = nodes[name], execution["nodes"][name]
        budget = self._budget_halt(execution)
        if budget:
            self._halt(execution, spec, budget)
            return
        approval = node["approval"] or ({"category": "EXTERNAL_MUTATION", "reason": "the node changes an external system"}
                                        if node["effect"] == "EXTERNAL" else None)
        if approval and (record["approval"] or {}).get("decision") != "approve":
            reason = "EXTERNAL_APPROVAL_REQUIRED" if approval["category"] == "EXTERNAL_MUTATION" else "APPROVAL_REQUIRED"
            self._halt(execution, spec, reason, node=name, kind="APPROVAL", category=approval["category"], prompt=approval["reason"])
            return
        if node["kind"] == "wait":
            self._halt(execution, spec, "INPUT_REQUIRED", node=name, kind="INPUT", prompt=node["params"].get("prompt"))
            return
        handler = self.handlers.get(node.get("handler", name))
        if handler is None:
            self._halt(execution, spec, "HANDLER_MISSING", node=name)
            return
        stale = self._stale.get(name)
        if stale is not None and stale.is_alive() and not node["idempotent"]:
            self._halt(execution, spec, "NODE_STILL_RUNNING", node=name)
            return
        self._attempt(execution, spec, nodes, node, handler)

    def _attempt(self, execution: dict[str, Any], spec: dict[str, Any], nodes: dict[str, dict[str, Any]],
                 node: dict[str, Any], handler: Handler) -> None:
        name, record = node["id"], execution["nodes"][node["id"]]
        attempt = len(record["attempts"]) + 1
        record["attempts"].append({"attempt": attempt, "generation": record["generation"], "result": None, "failure": None,
                                   "idempotency_key": loop_policy.idempotency_key(execution["execution_id"], name, record["generation"]),
                                   "attempt_id": loop_policy.attempt_id(execution["execution_id"], name, record["generation"], attempt),
                                   "started_at": self.clock(), "human_authorized": record["retry_authorized"]})
        record["status"] = "RUNNING"
        record["retry_authorized"] = False
        execution["status"] = "RUNNING"
        execution["usage"]["node_runs"] += 1
        self._commit(execution, "NODE_STARTED", {"node": name, "attempt": attempt})
        inputs = {dependency: execution["nodes"][dependency]["output"] for dependency in node["depends_on"]}
        context = NodeContext(self, execution, node, inputs)
        started = self.clock()
        value, failure = self._invoke(handler, context, node["timeout_seconds"])
        execution["usage"]["active_seconds"] += max(0.0, self.clock() - started)
        success = None
        if failure is None:
            success, failure = loop_policy.normalize_result(value)
        self._settle(execution, spec, nodes, node, success, failure)

    def _invoke(self, handler: Handler, context: NodeContext, timeout: float) -> tuple[Any, dict[str, Any] | None]:
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["value"] = handler(context)
            except BaseException as error:  # noqa: BLE001 - re-raised or classified on the runner thread
                box["error"] = error

        worker = threading.Thread(target=target, name=f"loop-node-{context.node['id']}", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            with self._write_lock:
                context._closed = True
            self._stale[context.node["id"]] = worker
            return None, loop_policy.timeout_failure(timeout)
        context._closed = True
        error = box.get("error")
        if error is not None:
            if not isinstance(error, Exception):
                raise error
            return None, loop_policy.failure_from_exception(error)
        return box.get("value"), None

    def _settle(self, execution: dict[str, Any], spec: dict[str, Any], nodes: dict[str, dict[str, Any]], node: dict[str, Any],
                success: dict[str, Any] | None, failure: dict[str, Any] | None) -> None:
        name, record = node["id"], execution["nodes"][node["id"]]
        attempt = record["attempts"][-1]
        attempt["finished_at"] = self.clock()
        if success is not None:
            loop_state.merge_tokens(execution["usage"], success["tokens"], success["tokens_method"])
            if success["needs_input"] and record["human_input"] is None:
                attempt["result"] = "NEEDS_INPUT"
                prompt = success["needs_input"].get("prompt") if isinstance(success["needs_input"], dict) else str(success["needs_input"])
                self._halt(execution, spec, "INPUT_REQUIRED", node=name, kind="INPUT", prompt=prompt)
                return
            attempt["result"] = "SUCCEEDED"
            record.update({"status": "COMPLETED", "output": success["output"], "partial": None, "last_failure": None})
            if node["on_failure"]:
                loop_correction.on_verifier_pass(execution, name)
            self._commit(execution, "NODE_COMPLETED", {"node": name})
            return
        failure = failure or {"class": "UNKNOWN", "code": "RESULT_INVALID", "message": "no result", "effect_applied": None}
        attempt["result"], attempt["failure"] = "FAILED", failure
        record["last_failure"] = failure
        self._after_failure(execution, spec, nodes, node, failure)

    def _after_failure(self, execution: dict[str, Any], spec: dict[str, Any], nodes: dict[str, dict[str, Any]],
                       node: dict[str, Any], failure: dict[str, Any]) -> None:
        name, record = node["id"], execution["nodes"][node["id"]]
        decision = loop_policy.classify(node, record, failure, execution["limits"])
        if decision["decision"] == loop_policy.RETRY:
            record[decision["counter"]] += 1
            record["status"] = "RETRYING"
            execution["status"] = "RETRYING"
            self._commit(execution, "NODE_RETRYING", {"node": name, "delay": decision["delay"], "failure": failure["class"]})
            self.sleep(decision["delay"])
            return
        if decision["decision"] == loop_policy.CORRECT:
            verdict = loop_correction.on_verifier_failure(execution, nodes, name, failure)
            if verdict["decision"] == "CORRECT":
                self._commit(execution, *loop_correction.apply(execution, nodes, name, verdict["hypothesis"], "verifier"))
                return
            self._halt(execution, spec, verdict["reason"], node=name)
            return
        self._halt(execution, spec, decision["reason"], node=name)

    # -- recovery and cancellation ---------------------------------------------------
    def _recover_interrupted(self, execution: dict[str, Any], spec: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> bool:
        """Settle every attempt a dead runner left open; return True when something changed."""
        for name, record in execution["nodes"].items():
            if record["status"] != "RUNNING" or not record["attempts"] or record["attempts"][-1]["result"] is not None:
                continue
            node, attempt = nodes[name], record["attempts"][-1]
            attempt["result"] = "INTERRUPTED"
            reconciler = self.reconcilers.get(node.get("handler", name))
            proof = None
            if reconciler is not None:
                try:
                    proof = reconciler(NodeContext(self, execution, node, {}))
                except Exception:  # noqa: BLE001 - an inconclusive reconciler proves nothing
                    proof = None
            proof = proof if isinstance(proof, dict) else {}
            applied = proof.get("applied")
            if applied is True:
                attempt["result"] = "RECONCILED"
                record.update({"status": "COMPLETED", "output": proof.get("output", {}), "partial": None})
                self._commit(execution, "NODE_RECONCILED", {"node": name, "applied": True})
                return True
            failure = {"class": "TRANSIENT", "code": "PROCESS_INTERRUPTED", "message": "the runner stopped during this attempt",
                       "effect_applied": False if applied is False else None}
            attempt["failure"] = failure
            record["last_failure"] = failure
            self._commit(execution, "NODE_INTERRUPTED", {"node": name, "applied": applied})
            self._after_failure(execution, spec, nodes, node, failure)
            return True
        return False

    def _cancel_if_requested(self, execution: dict[str, Any]) -> bool:
        request = self.store.read_cancel_request(execution["execution_id"])
        if request is None:
            return False
        self._commit(execution, *loop_state.cancel_all(execution, self.clock(), request.get("by", "unknown"), request.get("reason", "")))
        self.store.clear_cancel_request(execution["execution_id"])
        return True

    def _commit(self, execution: dict[str, Any], event: str, data: dict[str, Any]) -> None:
        with self._write_lock:
            execution["updated_at"] = self.clock()
            self.store.commit(execution, event, data)
