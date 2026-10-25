"""Security policy engine: regression tests for the bypasses found in review rounds 4 and 5 of PR #64.

Shares the fixture of ``test_policy_bypasses``; attack strings stay assembled at run time
for the same reason (Hermes skills-guard reads the literals as threats).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_policy_bypasses import BypassFixture, policy_approval, policy_git  # noqa: E402,F401


class RoundFourTests(BypassFixture):
    PROTECTED = ".hermes/orchestration/runtime/x.py"

    def test_combined_stdin_flags_and_glued_preloads_have_no_script_argument(self):
        for prefix in ("bash -xs", "sh -es", "bash -s-", "ruby -rw", "ruby -r./w", "perl -Mw", "perl -I. -Mw"):
            with self.subTest(prefix=prefix):
                self.assertEqual("DENY", self.run_tool(f"{prefix} {self.PROTECTED}")["decision"])

    def test_git_config_reads_only_when_the_action_comes_before_the_key(self):
        for command in ("git config core.hooksPath unset", "git config include.path list",
                        "git config alias.w '!x' --show-origin", "git config core.fsmonitor -l"):
            with self.subTest(command=command):
                self.assertEqual("REQUIRE_APPROVAL", self.run_tool(command)["decision"])
        for command in ("git config --get alias.co", "git config --list", "git config get core.hooksPath"):
            with self.subTest(command=command):
                self.assertEqual("ALLOW", self.run_tool(command)["decision"])

    def test_only_labelling_git_variables_pass(self):
        for command in (f"GIT_INDEX_FILE={self.PROTECTED} git add .", "GIT_WORK_TREE=.hermes git checkout .",
                        f"GIT_TRACE={self.PROTECTED} git status", "GIT_OBJECT_DIRECTORY=x git log", "GIT_DIR=evil git log"):
            with self.subTest(command=command):
                self.assertEqual("REQUIRE_APPROVAL", self.run_tool(command)["decision"])
        for command in ("GIT_DIR=.git git status", "GIT_COMMITTER_NAME=Ana git commit -m x", "GIT_PAGER=cat git log"):
            with self.subTest(command=command):
                self.assertEqual("ALLOW", self.run_tool(command)["decision"])

    def test_a_pager_or_editor_value_passes_only_with_display_flags(self):
        for value in ("less +!touch pwn", f"less -o {self.PROTECTED}", "less --lesskey-src=k", "true x"):
            with self.subTest(value=value):
                self.assertTrue(policy_git.git_runs_code(["git", "-c", f"core.pager={value}", "log"]))
        self.assertEqual("REQUIRE_APPROVAL", self.run_tool("GIT_PAGER='less +!x' git log")["decision"])
        for value in ("less -R", "less -FRX", "more", "true"):
            with self.subTest(value=value):
                self.assertFalse(policy_git.git_runs_code(["git", "-c", f"core.editor={value}", "commit"]))

    def test_grouping_and_keywords_do_not_hide_the_program(self):
        for command in ("{ RM -rf src; }", "if true; then RM -rf src; fi", "! CHMOD -R 777 src", "(rm -rf src)"):
            with self.subTest(command=command):
                self.assertEqual("DENY", self.run_tool(command)["decision"])
        for command in ("{ . ./x; }", "(. ./x)", "! . ./x", "if true; then . ./x; fi", "while true; do . ./x; done"):
            with self.subTest(command=command):
                self.assertEqual("REQUIRE_APPROVAL", self.run_tool(command)["decision"])
        self.assertEqual("ALLOW", self.run_tool("if true; then ls; fi")["decision"])

    def test_tool_bots_and_group_compounds_cannot_approve(self):
        action = {"kind": "tool_execution", "tool": "execute_code", "command": "print(1)"}
        for name in ("slackbot", "chatbot", "botnet", "teamcity", "groupchat", "Teamster"):
            with self.subTest(name=name), self.assertRaises(policy_approval.ApprovalError):
                self.dispatcher.approvals.grant(action, by=name, quote="ok")


class RoundFiveTests(BypassFixture):
    PROTECTED = ".hermes/orchestration/runtime/x.py"

    def test_only_known_safe_options_let_the_first_argument_be_the_script(self):
        for prefix in ("perl -eunlink+shift", "perl -Ex", "node --eval=x", "node --print=x", "node -p1", "ruby -e1",
                       "ruby -we1", "bash -c1", "bash -xc1", "lua -e1", "perl -mw", "lua -lw", "node --experimental-loader=./w"):
            with self.subTest(prefix=prefix):
                self.assertEqual("DENY", self.run_tool(f"{prefix} {self.PROTECTED}")["decision"])
        for prefix in ("bash -x", "bash -n", "ruby -w", "node --inspect", "python3 -B", "python3 -W ignore"):
            with self.subTest(prefix=prefix):
                self.assertEqual("ALLOW", self.run_tool(f"{prefix} {self.PROTECTED}")["decision"])

    def test_an_environment_assignment_before_an_interpreter_may_preload_code(self):
        for env in ("NODE_OPTIONS='--require ./w.js' node", "RUBYOPT=-rw ruby", "PERL5OPT=-Mw perl", "BASH_ENV=./w bash",
                    "PYTHONSTARTUP=w python3"):
            with self.subTest(env=env):
                self.assertEqual("DENY", self.run_tool(f"{env} {self.PROTECTED}")["decision"])
        self.assertEqual("ALLOW", self.run_tool("PYTHONPATH=src python3 script.py out")["decision"])

    def test_a_variable_exported_or_assigned_in_an_earlier_segment_may_preload_code(self):
        for setup in ("export NODE_OPTIONS='--require ./w.js'; node", "export RUBYOPT=-rw && ruby", "declare -x RUBYOPT=-rw; ruby",
                      "typeset -x PERL5OPT=-Mw; perl", "set -a; BASH_ENV=./w; bash", "RUBYOPT=-rw; export RUBYOPT; ruby",
                      "export BASH_ENV=./w\nbash"):
            with self.subTest(setup=setup):
                self.assertEqual("DENY", self.run_tool(f"{setup} {self.PROTECTED}")["decision"])
        self.assertEqual("ALLOW", self.run_tool("ls; python3 script.py out")["decision"])

    def test_a_subshell_opened_with_a_separate_parenthesis_is_read_like_any_other_command(self):
        for command in ("( . ./x )", "! ( . ./x )", "( git -c core.pager=./p log )"):
            with self.subTest(command=command):
                self.assertEqual("REQUIRE_APPROVAL", self.run_tool(command)["decision"])
        for command in (f"( export RUBYOPT=-rw; ruby {self.PROTECTED} )", f"( export RUBYOPT=-rw ) ; ruby {self.PROTECTED}",
                        "( rm -rf src )"):
            with self.subTest(command=command):
                self.assertEqual("DENY", self.run_tool(command)["decision"])
        self.assertEqual("ALLOW", self.run_tool("( cd src && ls )")["decision"])

    def test_a_module_name_after_dash_m_is_not_a_path_but_later_arguments_are(self):
        # Read as a module, the word after -m is an input even when it spells a protected path.
        self.assertEqual("ALLOW", self.run_tool(f"python3 -m {self.PROTECTED}")["decision"])
        self.assertEqual("DENY", self.run_tool(f"python3 -m json.tool src/a.json {self.PROTECTED}")["decision"])

    def test_a_pager_or_editor_given_as_a_path_needs_an_approval(self):
        for command in ("git -c core.pager=./less log", "git -c core.pager=x/less log", "git -c core.editor=./true commit",
                        "GIT_PAGER=./cat git log", "git config core.pager ./less", "GIT_AUTHOR_NAMEX=1 git log"):
            with self.subTest(command=command):
                self.assertEqual("REQUIRE_APPROVAL", self.run_tool(command)["decision"])



if __name__ == "__main__":
    unittest.main()
