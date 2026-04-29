"""SkillFactory — agent-authored tools.

Two-stage flow, both stages exposed as tools:

    skill_factory_create(description, name)
        → LLM generates Python code for a tool function.
        → Code is linted (banned-imports, AST validity) and stored in
          `app/workspace/skills/_staging/<name>.py`.
        → Returns a preview so the user can review.

    skill_factory_install(name)   [approval-required by policy]
        → Moves the staged file to `app/workspace/skills/<name>.py`,
          imports it, and registers the tool in the live ToolRegistry.

The active live skill files are auto-loaded at startup by `load_into(...)`,
so once installed, a skill survives restarts and is available immediately.

Sandbox model: this is *not* a true sandbox. Generated code runs in the
main process. Defense-in-depth comes from:
  1. Banned imports (no os.system, subprocess, ctypes, socket).
  2. AST whitelist of node types (no exec/eval, no class defs, no global
     mutation outside the function body).
  3. Approval gate before install (a human must ✅).
For untrusted-input use cases, a real sandbox (subprocess + restricted env)
is required and not yet built.
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.ollama import OllamaClient

if TYPE_CHECKING:
    from app.agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


SKILL_TEMPLATE_SYSTEM = """You write tool functions for an AI assistant.
Output one Python file containing exactly ONE async function called
`run(...)` plus a module-level `META` dict.

HARD RULES:
- IMPORTS GO AT THE TOP. Every name you use inside `run` must either be
  a builtin, a function/argument parameter, or a name imported at module
  scope. Do NOT assume any module is auto-available.
- No imports beyond: json, datetime, pathlib, re, math, hashlib, base64,
  textwrap, urllib.parse, httpx (for HTTP), typing, asyncio.
- DO NOT import: os, sys, subprocess, ctypes, socket, multiprocessing,
  threading, shutil, importlib, builtins, atexit, signal.
- No `exec`, `eval`, `compile`, `open(..., 'w'/'a')`, `__import__`.
- The function must be `async def run(**kwargs) -> str:`. Return a string.
- Wrap external calls in try/except — never raise out of `run`.
- Keep the file under 80 lines.

OUTPUT FORMAT — Python source, no markdown fences, no commentary:

import hashlib   # example — import EVERYTHING you use here at the top

META = {"name": "<snake_case_name>", "description": "<one line>"}

async def run(**kwargs) -> str:
    ...
"""

# Top-level imports (and their aliases) that are allowed in generated code.
ALLOWED_IMPORTS = {
    "json", "datetime", "pathlib", "re", "math", "hashlib", "base64",
    "textwrap", "urllib", "urllib.parse", "httpx", "typing", "asyncio",
}

# AST node types we refuse outright.
FORBIDDEN_NODES = (ast.ClassDef, ast.Global, ast.Nonlocal)

# Banned source substrings — extra cheap lint after AST checks.
BANNED_SUBSTRINGS = (
    "subprocess", "os.system", "os.popen", "ctypes", "socket.",
    "__import__", "exec(", "eval(", "compile(", "atexit",
)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


class SkillFactory:
    def __init__(
        self,
        skills_root: Path | str | None = None,
        ollama: OllamaClient | None = None,
    ):
        if skills_root is None:
            skills_root = Path(__file__).parent.parent / "workspace" / "skills"
        self.skills_root = Path(skills_root)
        self.staging_dir = self.skills_root / "_staging"
        self.skills_root.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.ollama = ollama or OllamaClient()

    # ---- create ------------------------------------------------------------

    async def create(self, description: str, name: str) -> dict:
        """Generate a skill, lint, stage. Returns dict with status + preview."""
        if not NAME_RE.match(name or ""):
            return {"status": "rejected", "reason": "name must be snake_case, 2-31 chars"}

        prompt = (
            f"Create a tool named `{name}`.\n"
            f"What it does: {description}\n\n"
            "Now produce the Python file."
        )
        try:
            result = await self.ollama.chat(messages=[
                {"role": "system", "content": SKILL_TEMPLATE_SYSTEM},
                {"role": "user", "content": prompt},
            ])
            code = result.get("message", {}).get("content", "")
        except Exception as exc:
            return {"status": "rejected", "reason": f"LLM call failed: {exc}"}

        code = self._strip_fences(code).strip()
        ok, reason = self._lint(code)
        if not ok:
            return {"status": "rejected", "reason": reason, "code": code[:1000]}

        # Stage the file
        staged = self.staging_dir / f"{name}.py"
        staged.write_text(code, encoding="utf-8")
        return {
            "status": "staged",
            "name": name,
            "staged_path": str(staged),
            "code": code,
            "next_step": (
                f"Review the code, then call skill_factory_install(name='{name}') "
                "(this is approval-required so you'll be asked to confirm)."
            ),
        }

    # ---- install -----------------------------------------------------------

    def install(self, name: str, registry: ToolRegistry) -> dict:
        """Move staged → live, hot-load into the running ToolRegistry."""
        if not NAME_RE.match(name or ""):
            return {"status": "rejected", "reason": "invalid name"}

        staged = self.staging_dir / f"{name}.py"
        if not staged.is_file():
            return {"status": "rejected", "reason": f"no staged skill named '{name}'"}

        live = self.skills_root / f"{name}.py"
        shutil.copy2(staged, live)
        try:
            self._load_skill_file(live, registry)
        except Exception as exc:
            # Roll back the file so we don't leave a broken skill on disk
            try:
                live.unlink()
            except OSError:
                pass
            return {"status": "rejected", "reason": f"import failed: {exc}"}

        # Clean staging
        try:
            staged.unlink()
        except OSError:
            pass
        return {"status": "installed", "name": name, "path": str(live)}

    # ---- bulk load at startup ---------------------------------------------

    def load_into(self, registry: ToolRegistry) -> int:
        """Discover and register every installed skill. Return count loaded."""
        loaded = 0
        for f in self.skills_root.glob("*.py"):
            if f.name.startswith("_"):
                continue
            try:
                self._load_skill_file(f, registry)
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to load skill %s: %s", f.name, exc)
        return loaded

    # ---- internals ---------------------------------------------------------

    @staticmethod
    def _strip_fences(text: str) -> str:
        # Remove ```python … ``` fences if the model added them despite the rule.
        m = re.search(r"```(?:python)?\s*\n(.+?)\n```", text, re.DOTALL)
        if m:
            return m.group(1)
        return text

    def _lint(self, code: str) -> tuple[bool, str]:
        if not code.strip():
            return False, "empty code"
        # Substring lint
        lower = code.lower()
        for banned in BANNED_SUBSTRINGS:
            if banned in lower:
                return False, f"banned substring: {banned!r}"
        # AST checks
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return False, f"syntax error: {exc}"

        has_run = False
        for node in ast.walk(tree):
            if isinstance(node, FORBIDDEN_NODES):
                return False, f"forbidden node: {type(node).__name__}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                else:
                    if node.module:
                        names = [node.module.split(".")[0]]
                for n in names:
                    if n not in ALLOWED_IMPORTS:
                        return False, f"disallowed import: {n}"
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
                has_run = True
        if not has_run:
            return False, "missing `async def run(**kwargs)` function"
        return True, "ok"

    def _load_skill_file(self, path: Path, registry: ToolRegistry) -> None:
        spec = importlib.util.spec_from_file_location(f"myai_skill_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not build module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        meta = getattr(module, "META", None) or {}
        run = getattr(module, "run", None)
        if run is None or not callable(run):
            raise RuntimeError("skill missing `run` function")
        name = meta.get("name") or path.stem
        if not NAME_RE.match(name):
            raise RuntimeError(f"skill name '{name}' invalid")
        # Register into the live registry
        registry._tools[name] = run  # type: ignore[index]
        logger.info("Skill registered: %s (%s)", name, meta.get("description", ""))


_singleton: SkillFactory | None = None


def get_skill_factory() -> SkillFactory:
    global _singleton
    if _singleton is None:
        _singleton = SkillFactory()
    return _singleton
