#!/usr/bin/env python3
"""PreToolUse hook for the Bash tool (CLAUDE.md, rules 3 and 8).

Blocks commands that would write git history, publish to GitHub, discard work,
or read the .env secrets file. Git and GitHub commands are checked against an
allowlist, so aliases and unknown subcommands are blocked too.

This is a guard rail, not a security boundary: a script written to disk can
still run git. The real boundary is Ilia's review and GitHub branch protection.

Exit 0 lets the command through. Exit 2 blocks it, and stderr goes to Claude.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

SEPARATORS = {";", "&&", "||", "|", "|&", "&", "(", ")", "{", "}", "\n"}
WRAPPERS = {"env", "command", "sudo", "exec", "nohup", "time", "xargs", "nice", "builtin", "watch"}
WRAPPERS_WITH_VALUE = {"timeout": 1, "nice": 0}
SHELLS = {"bash", "sh", "zsh", "dash", "fish", "pwsh", "powershell"}
GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
SECRET_FILES = re.compile(r"(^|/)\.env(\.local|\.production|\.development)?$")

# git subcommands Claude may run. Anything else, including aliases, is blocked.
GIT_SAFE = {
    "status", "diff", "log", "show", "add", "blame", "grep", "ls-files", "rev-parse",
    "describe", "shortlog", "help", "version", "fetch", "mv", "remote", "reflog",
    "branch", "switch", "checkout", "restore", "stash", "config", "rm", "reset",
    "worktree", "check-ignore", "cat-file", "ls-tree", "check-attr",
}


class Blocked(Exception):
    pass


def tokens_of(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|(){}")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    out: list[str] = []
    for tok in lexer:
        if "\n" in tok:
            parts = tok.split("\n")
            for i, part in enumerate(parts):
                if part:
                    out.append(part)
                if i < len(parts) - 1:
                    out.append("\n")
        else:
            out.append(tok)
    return out


def segments(tokens: list[str]) -> list[list[str]]:
    segs, cur = [], []
    for tok in tokens:
        if tok in SEPARATORS or set(tok) <= set(";&|(){}"):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


def program_index(seg: list[str]) -> int | None:
    """Index of the program a segment runs, skipping env assignments and wrappers."""
    i = 0
    while i < len(seg):
        tok = seg[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        name = os.path.basename(tok).lower().removesuffix(".exe")
        if name in WRAPPERS or name in WRAPPERS_WITH_VALUE:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                i += 1
            i += WRAPPERS_WITH_VALUE.get(name, 0)
            continue
        return i
    return None


def check_git(args: list[str]) -> None:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i]
        if opt in ("-c", "--config-env") and i + 1 < len(args):
            setting = args[i + 1].lower()
            if setting.startswith(("alias.", "core.hookspath", "core.sshcommand", "core.fsmonitor")):
                raise Blocked(f"git -c {args[i + 1]} is not allowed")
        if opt in GIT_GLOBAL_WITH_VALUE:
            i += 2
        else:
            i += 1
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1:]
    if "--no-verify" in rest:
        raise Blocked("--no-verify is not allowed")
    if sub not in GIT_SAFE:
        raise Blocked(f"git {sub} is not allowed. Stage with git add and ask Ilia to review")

    def has(*flags: str) -> bool:
        return any(a in flags or any(a.startswith(f + "=") for f in flags if f.startswith("--")) for a in rest)

    if sub == "restore" and (not has("--staged", "-S") or has("--worktree", "-W")):
        raise Blocked("git restore may only unstage (git restore --staged <file>)")
    if sub == "reset" and [a for a in rest if a not in ("-q", "--quiet")]:
        raise Blocked("git reset with arguments is not allowed. Use git restore --staged <file>")
    if sub == "checkout" and not (rest and rest[0] == "-b" and len(rest) == 2):
        raise Blocked("git checkout is only allowed as git checkout -b <new-branch>. Use git switch")
    if sub == "switch" and has("--discard-changes", "-f", "--force", "-C", "--force-create"):
        raise Blocked("git switch may not discard changes or reset a branch")
    if sub == "branch" and has("-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force", "-c", "-C"):
        raise Blocked("git branch may only list or create branches")
    if sub == "stash" and rest and rest[0] in ("drop", "clear", "pop"):
        raise Blocked(f"git stash {rest[0]} is not allowed")
    if sub == "config" and not has("--get", "--get-all", "--get-regexp", "--list", "-l"):
        raise Blocked("git config may only read settings")
    if sub == "rm" and not has("--cached"):
        raise Blocked("git rm is only allowed with --cached")
    if sub == "worktree" and rest[:1] != ["list"]:
        raise Blocked("git worktree may only list")
    if sub == "remote" and rest and rest[0] not in ("-v", "show", "get-url"):
        raise Blocked("git remote may only be read")
    if sub == "fetch" and has("--prune", "-p", "--force", "-f"):
        raise Blocked("git fetch may not prune or force-update")


GH_SAFE = {
    "pr": {"view", "list", "diff", "status", "checks"},
    "issue": {"view", "list", "status"},
    "run": {"view", "list", "watch"},
    "repo": {"view"},
    "release": {"view", "list"},
    "workflow": {"view", "list"},
    "auth": {"status"},
}


def check_gh(args: list[str]) -> None:
    if not args:
        return
    group, rest = args[0], args[1:]
    if group == "api":
        writes = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
        for a in rest:
            if a in writes or a.startswith(("--method=", "--field=", "--raw-field=", "--input=")) or re.match(r"^-[XfF].", a):
                raise Blocked("gh api may only read (GET without fields)")
        return
    if group in ("--version", "help", "version"):
        return
    allowed = GH_SAFE.get(group)
    if allowed is None or not rest or rest[0] not in allowed:
        raise Blocked(f"gh {' '.join(args[:2])} is not allowed. Only Ilia publishes to GitHub")


def check_command(command: str, depth: int = 0) -> None:
    if depth > 5:
        raise Blocked("command is nested too deeply to check")
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):
        check_command(inner[0] or inner[1], depth + 1)
    try:
        toks = tokens_of(command)
    except ValueError as error:
        raise Blocked(f"command could not be parsed safely ({error})") from error
    for tok in toks:
        if SECRET_FILES.search(tok):
            raise Blocked("the .env secrets file may not be read or changed")
    for seg in segments(toks):
        idx = program_index(seg)
        if idx is None:
            continue
        name = os.path.basename(seg[idx]).lower().removesuffix(".exe")
        args = seg[idx + 1:]
        if name == "git":
            check_git(args)
        elif name == "gh":
            check_gh(args)
        elif name in SHELLS or name == "eval":
            for j, a in enumerate(args):
                if name == "eval" or (j > 0 and args[j - 1] in ("-c", "-Command", "-command")):
                    check_command(a, depth + 1)


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("guard_bash: could not read the hook input, so the command was blocked", file=sys.stderr)
        return 2
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    try:
        check_command(command)
    except Blocked as reason:
        print(f"Blocked by .claude/hooks/guard_bash.py: {reason}. See CLAUDE.md, rule 3.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
