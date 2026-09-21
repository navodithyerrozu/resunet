"""Cross-process queue contract.

This test exists because of a class of bug that produces no error anywhere. Task
names are opaque strings in a Redis payload. If the API enqueues
`"render_resume_pdf"` and the worker registers `"render_resume"`, ARQ accepts the
enqueue, the job sits in the queue under a name nothing consumes, and it expires.
No exception, no log line, no failed health check -- the user simply never gets
their PDF, and the only symptom is a support ticket.

The import-time guards in each worker's `main.py` cannot catch this, and it is
worth being clear about why: a worker process only ever sees its own
registrations. The render worker cannot know whether `submit_application` has a
consumer, and neither worker can know what the API's enum declares. The contract
is a property of the three codebases *together*, so the only place it can be
checked is here.

Everything is read with `ast` rather than imported. That is deliberate: importing
`app.services.queue` pulls in FastAPI, Pydantic settings and a Redis
configuration, and importing the automation worker pulls in Playwright. This test
must run in CI before any of that is installed, and a contract test that gets
skipped because a dependency is missing is a contract test that never runs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

API_QUEUE = REPO_ROOT / "apps" / "api" / "app" / "services" / "queue.py"
WORKER_MAINS = (
    REPO_ROOT / "apps" / "render-worker" / "worker" / "main.py",
    REPO_ROOT / "apps" / "automation-worker" / "worker" / "main.py",
)
TASK_MODULES = (
    REPO_ROOT / "apps" / "render-worker" / "worker" / "tasks",
    REPO_ROOT / "apps" / "automation-worker" / "worker" / "tasks",
)


# --- extraction helpers ------------------------------------------------------


def _api_task_names() -> dict[str, str]:
    """`TaskName` members, as {member: value}."""
    tree = ast.parse(API_QUEUE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TaskName":
            return {
                stmt.targets[0].id: stmt.value.value
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.targets[0], ast.Name)
            }
    raise AssertionError("TaskName enum not found in the API queue module")


def _unimplemented() -> set[str]:
    """The explicit allowlist of names declared ahead of implementation."""
    src = API_QUEUE.read_text(encoding="utf-8")
    match = re.search(
        r"UNIMPLEMENTED_TASKS:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{(.*?)\}\s*\)",
        src,
        re.DOTALL,
    )
    assert match, "UNIMPLEMENTED_TASKS not found -- did it get renamed?"
    members = re.findall(r"TaskName\.(\w+)\.value", match.group(1))
    api = _api_task_names()
    return {api[m] for m in members}


def _registered_task_names() -> set[str]:
    """Names each worker actually registers with ARQ, from `functions = [...]`."""
    names: set[str] = set()
    for main in WORKER_MAINS:
        tree = ast.parse(main.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.List)):
                continue
            if not any(
                isinstance(t, ast.Name) and t.id == "functions" for t in node.targets
            ):
                continue
            for element in node.value.elts:
                if isinstance(element, ast.Name):
                    names.add(element.id)
    return names


def _declared_expectations() -> set[str]:
    """The `EXPECTED_TASK_NAMES` literals from each worker's import-time guard."""
    names: set[str] = set()
    for main in WORKER_MAINS:
        src = main.read_text(encoding="utf-8")
        match = re.search(r"EXPECTED_TASK_NAMES\s*=\s*frozenset\(\{([^}]*)\}\)", src)
        assert match, f"{main} has no EXPECTED_TASK_NAMES guard"
        names |= set(re.findall(r'"([^"]+)"', match.group(1)))
    return names


def _defined_task_functions() -> set[str]:
    """Public async functions in the workers' `tasks/` packages."""
    found: set[str] = set()
    for directory in TASK_MODULES:
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:  # top level only
                if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_"):
                    found.add(node.name)
    return found


# --- the contract ------------------------------------------------------------


class TestTaskNameContract:
    def test_every_registered_task_is_declared_by_the_api(self) -> None:
        """A worker consuming a name the API never enqueues is dead code."""
        orphans = _registered_task_names() - set(_api_task_names().values())
        assert not orphans, (
            f"workers register {sorted(orphans)}, which the API's TaskName enum does "
            "not declare -- nothing will ever enqueue these"
        )

    def test_every_enum_name_is_either_implemented_or_allowlisted(self) -> None:
        """The expensive direction: an enqueued name with no consumer is silent loss."""
        declared = set(_api_task_names().values())
        unconsumed = declared - _registered_task_names() - _unimplemented()
        assert not unconsumed, (
            f"TaskName declares {sorted(unconsumed)} with no worker consuming them. "
            "Either register a task under that name, or add it to "
            "UNIMPLEMENTED_TASKS with a comment explaining why it exists early."
        )

    def test_unimplemented_names_really_are_unimplemented(self) -> None:
        """Stops the allowlist from silently outliving its purpose.

        Once a worker registers one of these, the allowlist entry is stale and
        would start hiding a real regression, so implementing a task is required
        to update this list.
        """
        implemented = _unimplemented() & _registered_task_names()
        assert not implemented, (
            f"{sorted(implemented)} are now implemented -- remove them from "
            "UNIMPLEMENTED_TASKS so the contract test starts guarding them"
        )

    def test_worker_guards_match_what_the_workers_register(self) -> None:
        """The per-worker import-time assertion must itself be accurate."""
        assert _declared_expectations() == _registered_task_names()

    def test_registered_functions_exist_in_the_task_modules(self) -> None:
        missing = _registered_task_names() - _defined_task_functions()
        assert not missing, f"registered but not defined as public async tasks: {sorted(missing)}"

    def test_task_names_are_snake_case_and_unique(self) -> None:
        values = list(_api_task_names().values())
        assert len(values) == len(set(values)), "duplicate task name values in TaskName"
        for value in values:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", value), f"{value!r} is not snake_case"


class TestQueueRouting:
    """Queue names are the second half of the contract.

    A task registered on the right name but the wrong queue fails exactly the same
    way: enqueued successfully, consumed by nobody.
    """

    @staticmethod
    def _string_defaults(path: Path, suffix: str) -> dict[str, str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id.endswith(suffix)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                found[node.target.id] = node.value.value
        return found

    def test_queue_names_agree_across_services(self) -> None:
        api = self._string_defaults(
            REPO_ROOT / "apps" / "api" / "app" / "core" / "config.py", "queue_name"
        )
        render = self._string_defaults(
            REPO_ROOT / "apps" / "render-worker" / "worker" / "settings.py", "queue_name"
        )
        automation = self._string_defaults(
            REPO_ROOT / "apps" / "automation-worker" / "worker" / "settings.py", "queue_name"
        )

        assert api["render_queue_name"] == render["render_queue_name"], (
            "the API enqueues renders to a different queue than the render worker "
            "consumes from"
        )
        assert api["automation_queue_name"] == automation["automation_queue_name"], (
            "the API enqueues submissions to a different queue than the automation "
            "worker consumes from"
        )

    @pytest.mark.parametrize("service", ["render", "automation"])
    def test_queue_names_are_namespaced(self, service: str) -> None:
        """Shared Redis instances are common; an unprefixed queue name collides."""
        api = self._string_defaults(
            REPO_ROOT / "apps" / "api" / "app" / "core" / "config.py", "queue_name"
        )
        assert api[f"{service}_queue_name"].startswith("resunet:")
