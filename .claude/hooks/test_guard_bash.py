"""Tests for guard_bash.py. Run with: python3 -m pytest .claude/hooks/test_guard_bash.py"""
import json
import pathlib
import subprocess
import sys

import pytest

HOOK = pathlib.Path(__file__).with_name("guard_bash.py")


def run(command: str, tool: str = "Bash") -> subprocess.CompletedProcess:
    event = {"tool_name": tool, "tool_input": {"command": command}}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                          capture_output=True, text=True, check=False)


ALLOWED = [
    "git status",
    "git diff --staged",
    "git log --oneline -20",
    "git add api/src/bowerstone/ledger/service.py features.md",
    "git restore --staged features.md",
    "git switch -c feature/trial-balance",
    "git checkout -b feature/trial-balance",
    "git branch",
    "git stash list",
    "git config --get user.name",
    "git rm --cached build.log",
    "git reset",
    "git -C api status",
    "cd api && uv run pytest && git status",
    "FOO=1 git diff",
    "gh pr view 12",
    "gh run list",
    "gh api repos/acme/bowerstone/pulls",
    "grep -rn 'git commit' docs/",
    "echo done",
    "cat .env.example",
    "uv run pytest -q",
]

BLOCKED = [
    "git commit -m 'x'",
    "git commit --amend",
    "git push origin main",
    "git push --force",
    "git -c user.name=x commit -m y",
    "git -C . push origin main",
    "git -c alias.ci=commit ci -m x",
    "git ci -m x",
    "cd sub && git commit -m x",
    "uv run pytest; git push",
    "FOO=bar git commit -m x",
    "env git commit -m x",
    "timeout 30 git push",
    "/usr/bin/git commit -m x",
    "git.exe push",
    "bash -c 'git commit -m x'",
    "sh -c \"git push\"",
    "eval 'git push'",
    "echo $(git commit -m x)",
    "echo `git push`",
    "git merge main",
    "git rebase main",
    "git tag v1.0.0",
    "git cherry-pick abc123",
    "git pull",
    "git reset --hard",
    "git reset HEAD~1",
    "git clean -fd",
    "git stash drop",
    "git stash clear",
    "git restore features.md",
    "git restore --staged --worktree features.md",
    "git checkout -- features.md",
    "git checkout .",
    "git switch --discard-changes main",
    "git branch -D feature/x",
    "git config alias.ci commit",
    "git config core.hooksPath /dev/null",
    "git rm features.md",
    "git add . && git commit --no-verify -m x",
    "gh pr create --fill",
    "gh pr merge 12",
    "gh release create v1.0.0",
    "gh repo delete acme/bowerstone",
    "gh api -X POST repos/acme/bowerstone/issues",
    "gh api repos/acme/bowerstone/issues -f title=x",
    "cat .env",
    "cp .env /tmp/x",
    "less api/.env",
    "git commit -m 'unbalanced",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_allowed(command):
    result = run(command)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("command", BLOCKED)
def test_blocked(command):
    result = run(command)
    assert result.returncode == 2
    assert "Blocked" in result.stderr


def test_other_tools_pass_through():
    assert run("anything", tool="Read").returncode == 0


def test_bad_input_is_blocked():
    result = subprocess.run([sys.executable, str(HOOK)], input="not json",
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
