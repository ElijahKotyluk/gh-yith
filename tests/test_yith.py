"""Tests for the pure logic and the GitHub boundary of the yith CLI.

The same file runs in both repos: against gh-yith at the root of the gh-yith
extension repo, and against scripts/yith in the yith skill repo.
Every test monkeypatches yith.gh or yith.graphql; nothing here reaches GitHub.
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [os.path.join(HERE, "..", "gh-yith"), os.path.join(HERE, "..", "scripts", "yith")]
SCRIPT = next((c for c in CANDIDATES if os.path.isfile(c)), None)
if SCRIPT is None:
    raise RuntimeError("yith CLI not found; looked for: %s" % ", ".join(CANDIDATES))

loader = importlib.machinery.SourceFileLoader("yith", SCRIPT)
spec = importlib.util.spec_from_loader("yith", loader)
yith = importlib.util.module_from_spec(spec)
loader.exec_module(yith)


def no_network(*args, **kwargs):
    raise AssertionError("GitHub was contacted: %r" % (args,))


def comment(login, body, typename="Bot", cid="C1"):
    return {
        "id": cid,
        "databaseId": 1,
        "body": body,
        "createdAt": "2026-01-01T00:00:00Z",
        "url": "https://github.com/o/r/pull/1#discussion_r1",
        "author": {"login": login, "__typename": typename},
    }


def node(comments, subject="LINE", line=10, start=None, resolved=False, tid="PRRT_1", can_resolve=None):
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": False,
        "path": "a.py",
        "line": line,
        "startLine": start,
        "originalLine": line,
        "diffSide": "RIGHT",
        "subjectType": subject,
        "viewerCanReply": True,
        "viewerCanResolve": (not resolved) if can_resolve is None else can_resolve,
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": comments},
    }


@contextlib.contextmanager
def patched(**attrs):
    saved = {k: getattr(yith, k) for k in attrs}
    for k, v in attrs.items():
        setattr(yith, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(yith, k, v)


@contextlib.contextmanager
def captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class AuthorFilter(unittest.TestCase):
    def test_default_keeps_bots_and_drops_lookalike_humans(self):
        bot = yith.shape_thread(node([comment("copilot-pull-request-reviewer", "x")]))
        human = yith.shape_thread(node([comment("copilot-helper", "x", typename="User")]))
        self.assertTrue(yith.thread_matches(bot, None, False))
        self.assertFalse(yith.thread_matches(human, None, False))

    def test_regex_replaces_bot_filter(self):
        import re
        human = yith.shape_thread(node([comment("alice", "x", typename="User")]))
        bot = yith.shape_thread(node([comment("copilot-pull-request-reviewer", "x")]))
        self.assertTrue(yith.thread_matches(human, re.compile("alice"), False))
        self.assertFalse(yith.thread_matches(bot, re.compile("alice"), False))


class ThreadShape(unittest.TestCase):
    def test_suggestions_come_from_the_thread_author_only(self):
        bot_sugg = "fix\n```suggestion\nnew line\n```"
        human_sugg = "or\n```suggestion\nhuman idea\n```"
        bot_followup = "```suggestion\nsecond\n```"
        t = yith.shape_thread(
            node(
                [
                    comment("bot", bot_sugg),
                    comment("alice", human_sugg, typename="User", cid="C2"),
                    comment("bot", bot_followup, cid="C3"),
                ]
            )
        )
        self.assertEqual(t["suggestions"], ["new line\n", "second\n"])
        self.assertEqual(len(t["replies"]), 2)

    def test_file_level_comment_and_deleted_author_do_not_crash(self):
        c = comment("ghost", "whole file")
        c["author"] = None
        t = yith.shape_thread(node([c], subject="FILE", line=None))
        self.assertEqual(t["subject_type"], "FILE")
        self.assertIsNone(t["line"])
        self.assertEqual(t["author"], "ghost")
        with captured() as (out, _):
            yith.print_table({"pr": fake_pr(), "threads": [t]})
            yith.print_markdown({"pr": fake_pr(), "threads": [t]})
        self.assertIn("a.py:?", out.getvalue())
        self.assertNotIn("None", out.getvalue())


def fake_pr():
    return {
        "owner": "o", "repo": "r", "number": 1, "url": "https://github.com/o/r/pull/1",
        "title": "t", "state": "OPEN", "head": "h", "head_sha": "0123456789abcdef",
        "base": "main", "from_fork": False, "draft": False, "viewer": "me",
    }


class PrResolution(unittest.TestCase):
    def test_url_needs_no_network(self):
        with patched(gh=no_network):
            self.assertEqual(
                yith.resolve_pr("https://github.com/boll-branch/poseidon/pull/1670/files", None),
                ("boll-branch", "poseidon", 1670),
            )

    def test_bare_number_uses_repo_flag_without_network(self):
        with patched(gh=no_network):
            self.assertEqual(yith.resolve_pr("#42", "o/r"), ("o", "r", 42))

    def test_garbage_is_rejected(self):
        with patched(gh=no_network), self.assertRaises(yith.YithError):
            yith.resolve_pr("nonsense", None)


class Reasons(unittest.TestCase):
    def test_aliases_map_to_github_enum(self):
        self.assertEqual(yith.reason_type("wont-fix"), "WONT_FIX")
        self.assertEqual(yith.reason_type("Invalid"), "INVALID")

    def test_empty_reason_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(yith.YithError):
            yith.reason_type("")


class WriteSafety(unittest.TestCase):
    def test_reply_without_body_fails_instead_of_reading_stdin(self):
        with patched(gh=no_network), captured() as (_, err):
            code = yith.main(["reply", "PRRT_1", "--resolve", "wontfix"])
        self.assertEqual(code, 1)
        self.assertIn("--body-file", err.getvalue())

    def test_dry_run_sends_nothing(self):
        with patched(gh=no_network), captured() as (out, _):
            codes = [
                yith.main(["reply", "PRRT_1", "-n", "-m", "Fixed.", "--resolve"]),
                yith.main(["resolve", "PRRT_1", "PRRT_2", "-n", "--reason", "invalid"]),
                yith.main(["unresolve", "PRRT_1", "-n"]),
                yith.main(["rerequest", "https://github.com/o/r/pull/1", "-n"]),
            ]
        self.assertEqual(codes, [0, 0, 0, 0])
        self.assertEqual(out.getvalue().count("DRY RUN"), 5)

    def test_reply_posted_but_resolve_failed_is_reported_loudly(self):
        calls = []

        def fake_graphql(query, variables):
            calls.append(query)
            if "addPullRequestReviewThreadReply" in query:
                return {"addPullRequestReviewThreadReply": {"comment": {"url": "https://x/r1"}}}
            raise yith.YithError("Resource not accessible by integration")

        with patched(graphql=fake_graphql), captured() as (out, err):
            code = yith.main(["reply", "PRRT_1", "-m", "Fixed.", "--resolve"])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("replied: https://x/r1", out.getvalue())
        self.assertIn("WAS posted", err.getvalue())
        self.assertIn("do not re-post", err.getvalue())


class ListExitCodes(unittest.TestCase):
    def run_list(self, threads, *extra):
        def fake_fetch(owner, repo, number):
            return fake_pr(), threads

        with patched(fetch_threads=fake_fetch, gh=no_network), captured() as (out, _):
            code = yith.main(["list", "https://github.com/o/r/pull/1", "-f", "table"] + list(extra))
        return code, out.getvalue()

    def test_fail_if_open_distinguishes_open_from_resolved(self):
        open_bot = node([comment("bot", "x")])
        resolved_bot = node([comment("bot", "x")], resolved=True)
        self.assertEqual(self.run_list([open_bot], "--fail-if-open")[0], 2)
        self.assertEqual(self.run_list([resolved_bot], "--fail-if-open")[0], 0)
        self.assertEqual(self.run_list([open_bot])[0], 0)

    def test_bad_regex_fails_before_any_fetch(self):
        with patched(fetch_threads=no_network, gh=no_network), captured() as (_, err):
            code = yith.main(["list", "https://github.com/o/r/pull/1", "--author", "("])
        self.assertEqual(code, 1)
        self.assertIn("bad --author regex", err.getvalue())


def plan_with(*entries):
    return {
        "yith_plan": 1,
        "pr": {"owner": "o", "repo": "r", "number": 1, "head_sha": "0123456789abcdef"},
        "threads": list(entries),
    }


def entry(tid, cls, reply="", **extra):
    e = {"id": tid, "path": "a.py", "line": 10, "class": cls, "reply": reply}
    e.update(extra)
    return e


class PlanValidation(unittest.TestCase):
    def test_classes_map_to_replies_and_reasons(self):
        actions = yith.plan_actions(
            plan_with(
                entry("T1", "fix", "Fixed: x."),
                entry("T2", "wont-fix", "Intentional."),
                entry("T3", "INVALID"),
                entry("T4", "HUMAN", "Should this retry?"),
                entry("T5", "HUMAN"),
                entry("T6", "WONTFIX", "Out of scope.", resolve=False),
            )
        )
        got = [(e["id"], cls, reply, reason) for e, cls, reply, reason in actions]
        self.assertEqual(
            got,
            [
                ("T1", "FIX", "Fixed: x.", "ADDRESSED"),
                ("T2", "WONT_FIX", "Intentional.", "WONT_FIX"),
                ("T3", "INVALID", None, "INVALID"),
                ("T4", "HUMAN", "Should this retry?", None),
                ("T5", "HUMAN", None, None),
                ("T6", "WONT_FIX", "Out of scope.", None),
            ],
        )

    def test_every_problem_is_reported_at_once(self):
        with self.assertRaises(yith.YithError) as ctx:
            yith.plan_actions(
                plan_with(entry("T1", "FIX"), entry("T2", ""), entry("T1", "HUMAN"), {"class": "FIX"})
            )
        msg = str(ctx.exception)
        self.assertIn("T1: FIX needs a reply", msg)
        self.assertIn("T2: class ''", msg)
        self.assertIn("T1 appears more than once", msg)
        self.assertIn("entry 4 has no id", msg)

    def test_a_list_output_file_is_not_mistaken_for_a_plan(self):
        with self.assertRaises(yith.YithError):
            yith.plan_actions({"schema": 1, "pr": {}, "threads": []})


class Post(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "plan.json")
        self.sent = []

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, plan):
        with open(self.path, "w") as fh:
            json.dump(plan, fh)

    def read(self):
        with open(self.path) as fh:
            return json.load(fh)

    def run_post(self, nodes, *flags, fail_on=None):
        def fake_fetch(owner, repo, number):
            return fake_pr(), nodes

        def fake_graphql(query, variables):
            kind = "reply" if "addPullRequestReviewThreadReply" in query else "resolve"
            if fail_on == (kind, variables["id"]):
                raise yith.YithError("Resource not accessible by integration")
            self.sent.append((kind, variables["id"]))
            if kind == "reply":
                return {"addPullRequestReviewThreadReply": {"comment": {"url": "https://x/" + variables["id"]}}}
            return {"resolveReviewThread": {"thread": {"id": variables["id"], "isResolved": True}}}

        with patched(fetch_threads=fake_fetch, graphql=fake_graphql, gh=no_network), captured() as (out, err):
            code = yith.main(["post", self.path] + list(flags))
        return code, out.getvalue(), err.getvalue()

    def test_without_yes_nothing_is_sent(self):
        self.write(plan_with(entry("T1", "FIX", "Fixed.")))
        code, out, _ = self.run_post([node([comment("bot", "x")], tid="T1")])
        self.assertEqual((code, self.sent), (0, []))
        self.assertIn("DRY RUN", out)

    def test_sends_exactly_the_plan_in_order(self):
        self.write(plan_with(entry("T1", "FIX", "Fixed."), entry("T2", "INVALID"), entry("T3", "HUMAN")))
        nodes = [node([comment("bot", "x")], tid=t) for t in ("T1", "T2", "T3")]
        code, _, _ = self.run_post(nodes, "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.sent, [("reply", "T1"), ("resolve", "T1"), ("resolve", "T2")])
        results = [e["result"] for e in self.read()["threads"]]
        self.assertTrue(results[0].startswith("replied https://x/T1"))
        self.assertEqual(results[2], "no action")

    def test_one_bad_entry_blocks_the_whole_plan(self):
        self.write(plan_with(entry("T1", "FIX", "Fixed."), entry("T9", "FIX", "Fixed.")))
        code, _, err = self.run_post([node([comment("bot", "x")], tid="T1")], "--yes")
        self.assertEqual((code, self.sent), (1, []))
        self.assertIn("T9 is not a review thread on this PR", err)

    def test_unresolvable_thread_blocks_unless_resolve_false(self):
        nodes = [node([comment("bot", "x")], tid="T1", can_resolve=False)]
        self.write(plan_with(entry("T1", "FIX", "Fixed.")))
        code, _, err = self.run_post(nodes, "--yes")
        self.assertEqual((code, self.sent), (1, []))
        self.assertIn('"resolve": false', err)
        self.write(plan_with(entry("T1", "FIX", "Fixed.", resolve=False)))
        code, _, _ = self.run_post(nodes, "--yes")
        self.assertEqual((code, self.sent), (0, [("reply", "T1")]))

    def test_rerun_skips_what_is_already_on_github(self):
        self.write(plan_with(entry("T1", "FIX", "Fixed.\r\n"), entry("T2", "WONT_FIX", "No.")))
        nodes = [
            node([comment("bot", "x"), comment("me", "Fixed.", typename="User", cid="C2")], tid="T1"),
            node([comment("bot", "x")], tid="T2", resolved=True),
        ]
        code, _, _ = self.run_post(nodes, "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.sent, [("resolve", "T1"), ("reply", "T2")])

    def test_failure_stops_and_records_what_happened(self):
        self.write(plan_with(entry("T1", "FIX", "Fixed."), entry("T2", "FIX", "Fixed too.")))
        nodes = [node([comment("bot", "x")], tid=t) for t in ("T1", "T2")]
        code, _, err = self.run_post(nodes, "--yes", fail_on=("resolve", "T1"))
        self.assertEqual(code, 1)
        self.assertEqual(self.sent, [("reply", "T1")])
        self.assertIn("Re-running post is safe", err)
        t1, t2 = self.read()["threads"]
        self.assertIn("replied", t1["result"])
        self.assertIn("FAILED", t1["result"])
        self.assertEqual(t2["result"], "not attempted")


class PlanCommand(unittest.TestCase):
    def test_writes_open_bot_threads_and_refuses_to_overwrite(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "sub", "plan.json")
            nodes = [
                node([comment("bot", "x")], tid="T1"),
                node([comment("bot", "x")], tid="T2", resolved=True),
                node([comment("alice", "x", typename="User")], tid="T3"),
            ]
            fake = lambda o, r, n: (fake_pr(), nodes)
            with patched(fetch_threads=fake, gh=no_network), captured():
                first = yith.main(["plan", "https://github.com/o/r/pull/1", "-o", path])
                second = yith.main(["plan", "https://github.com/o/r/pull/1", "-o", path])
            with open(path) as fh:
                plan = json.load(fh)
            self.assertEqual((first, second), (0, 1))
            self.assertEqual([t["id"] for t in plan["threads"]], ["T1"])
            self.assertEqual(plan["pr"]["head_sha"], "0123456789abcdef")
        finally:
            shutil.rmtree(d)

    def test_default_path_lives_inside_the_git_dir(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            subprocess.run(["git", "init", "-q", d], check=True)
            os.chdir(d)
            path = yith.default_plan_path(7)
            self.assertEqual(path, os.path.join(os.path.realpath(d), ".git", "yith", "plan-7.json"))
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
