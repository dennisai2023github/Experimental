"""Tests for substack_morning.py. Run:
python3.13 -m unittest discover -s skills/substack-morning/tests
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "substack_morning.py"
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(SCRIPT.parent))
import substack_morning as sm  # noqa: E402

SENTINEL = "SENTINEL_COOKIE_123"
NOW = "2026-09-30T08:00:00Z"


def fake_response(body=b'{"ok": true}'):
    resp = mock.MagicMock()
    resp.__enter__.return_value.read.return_value = body
    return resp


class HomeTestCase(unittest.TestCase):
    """Gives each test an isolated SUBSTACK_MORNING_HOME and clean env."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        env = {k: v for k, v in os.environ.items()
               if k not in ("SUBSTACK_SID", "SUBSTACK_HANDLE", "SUBSTACK_MORNING_CHILD")}
        env["SUBSTACK_MORNING_HOME"] = str(self.home)
        env["SUBSTACK_MORNING_NO_SPAWN"] = "1"  # never launch claude from tests
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sm.main(list(argv))
        self.stdout.write(out.getvalue())
        self.stderr.write(err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def write_env(self, sid=SENTINEL):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / ".env").write_text(
            f'SUBSTACK_SID="{sid}"\nSUBSTACK_HANDLE=dennisahking\n', encoding="utf-8")

    def write_state(self, **state):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def collect(self, **cfg_overrides):
        self.run_cli("setup")
        if cfg_overrides:
            cfg = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
            cfg.update(cfg_overrides)
            (self.home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        out = Path(self._tmp.name) / "candidates.json"
        code, _, _ = self.run_cli("collect", "--out", str(out), "--mock", str(FIXTURES),
                                  "--now", NOW)
        self.assertEqual(code, 0)
        return json.loads(out.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- network safety

class HttpGetTests(unittest.TestCase):
    def setUp(self):
        sm._last_request_at[0] = None
        self.open = mock.patch.object(sm._OPENER, "open", return_value=fake_response()).start()
        self.sleep = mock.patch.object(sm.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def sent_request(self):
        return self.open.call_args[0][0]

    def test_cookie_sent_to_substack_hosts_only(self):
        for url in ("https://substack.com/api/v1/subscriptions",
                    "https://example-pub.substack.com/api/v1/archive"):
            sm.http_get(url, sid=SENTINEL)
            req = self.sent_request()
            self.assertEqual(req.get_header("Cookie"), f"substack.sid={SENTINEL}")
            self.assertEqual(req.get_method(), "GET")
            self.assertEqual(req.get_header("User-agent"), sm.USER_AGENT)
            self.assertIsNone(req.data)
            self.assertEqual(self.open.call_args[1]["timeout"], 20)

    def test_custom_domain_allowed_but_no_cookie(self):
        sm.http_get("https://paid.example.com/api/v1/archive", sid=SENTINEL,
                    allowed_custom={"paid.example.com"})
        self.assertIsNone(self.sent_request().get_header("Cookie"))

    def test_disallowed_hosts_raise_before_network(self):
        bad = ["https://evil.example.com/x", "http://substack.com/api/v1/x",
               "https://substack.com.evil.com/x", "https://evilsubstack.com/x",
               "https://user:pw@substack.com/x", "https://substack.com:8443/x",
               "https://paid.example.com/x"]  # custom domain not in allowlist
        for url in bad:
            with self.assertRaises(sm.HostNotAllowed, msg=url):
                sm.http_get(url, sid=SENTINEL)
        self.open.assert_not_called()

    def test_http_error_is_sanitized(self):
        self.open.side_effect = urllib.error.HTTPError(
            "https://substack.com/api/v1/x", 403, "Forbidden",
            {"Set-Cookie": f"substack.sid={SENTINEL}"}, None)
        with self.assertRaises(sm.HttpError) as ctx:
            sm.http_get("https://substack.com/api/v1/x", sid=SENTINEL)
        self.assertEqual(str(ctx.exception), "HTTP 403 from host substack.com")
        self.assertEqual(ctx.exception.status, 403)
        self.assertTrue(ctx.exception.__suppress_context__)
        self.assertIsNone(ctx.exception.__cause__)

    def test_url_error_is_sanitized(self):
        self.open.side_effect = urllib.error.URLError(OSError(f"boom {SENTINEL}"))
        with self.assertRaises(sm.HttpError) as ctx:
            sm.http_get("https://substack.com/api/v1/x", sid=SENTINEL)
        self.assertNotIn(SENTINEL, str(ctx.exception))

    def test_throttle_between_requests(self):
        sm.http_get("https://substack.com/api/v1/a")
        self.sleep.assert_not_called()
        sm.http_get("https://substack.com/api/v1/b")
        self.sleep.assert_called_once()
        self.assertGreater(self.sleep.call_args[0][0], 0.5)
        self.assertLessEqual(self.sleep.call_args[0][0], 0.6)

    def test_redirect_rechecks_allowlist_and_drops_cookie(self):
        handler = sm._AllowlistRedirect()
        req = urllib.request.Request("https://paid-pub.substack.com/api/v1/archive",
                                     headers={"Cookie": f"substack.sid={SENTINEL}"}, method="GET")
        req.allowed_custom = frozenset({"paid.example.com"})
        new = handler.redirect_request(req, None, 302, "Found", {},
                                       "https://paid.example.com/api/v1/archive")
        self.assertIsNone(new.get_header("Cookie"))
        self.assertEqual(new.get_method(), "GET")
        same = handler.redirect_request(req, None, 302, "Found", {},
                                        "https://www.substack.com/api/v1/archive")
        self.assertIsNotNone(same.get_header("Cookie"))
        with self.assertRaises(sm.HostNotAllowed):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/")


class SourceAuditTests(unittest.TestCase):
    """Static checks that the script cannot write to Substack."""

    def setUp(self):
        self.src = SCRIPT.read_text(encoding="utf-8")

    def test_single_network_call_site(self):
        self.assertEqual(self.src.count("_OPENER.open("), 1)
        self.assertEqual(self.src.count("urlopen("), 0)
        self.assertEqual(self.src.count("urllib.request.Request("), 1)
        for banned in ("http.client", "import socket", "import requests", "ftplib", "smtplib"):
            self.assertNotIn(banned, self.src)

    def test_no_write_methods_or_request_bodies(self):
        for verb in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertIsNone(re.search(rf"""['"]{verb}['"]""", self.src), verb)
        self.assertNotIn("data=", self.src)
        self.assertEqual(set(re.findall(r"method\s*=\s*['\"](\w+)", self.src)), {"GET"})


# --------------------------------------------------------------------------- setup / status

class SetupStatusTests(HomeTestCase):
    def test_setup_creates_files_and_never_overwrites_env(self):
        code, out, _ = self.run_cli("setup")
        self.assertEqual(code, 0)
        env_text = (self.home / ".env").read_text(encoding="utf-8")
        self.assertIn("SUBSTACK_SID=\n", env_text)
        self.assertIn("SUBSTACK_HANDLE=dennisahking", env_text)
        self.assertIn("substack.sid", env_text)
        cfg = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg, sm.DEFAULT_CONFIG)
        self.assertIn(str(self.home / ".env"), json.loads(out)["env"])

        (self.home / ".env").write_text("SUBSTACK_SID=keepme\n", encoding="utf-8")
        (self.home / "config.json").write_text('{"handle": "custom"}', encoding="utf-8")
        self.run_cli("setup")
        self.assertEqual((self.home / ".env").read_text(encoding="utf-8"), "SUBSTACK_SID=keepme\n")
        self.assertEqual((self.home / "config.json").read_text(encoding="utf-8"),
                         '{"handle": "custom"}')

    def test_env_file_parsing_and_env_override(self):
        self.write_env("from_file")
        sid, handle = sm.load_secrets(self.home, sm.DEFAULT_CONFIG)
        self.assertEqual((sid, handle), ("from_file", "dennisahking"))
        with mock.patch.dict(os.environ, {"SUBSTACK_SID": "from_env"}):
            self.assertEqual(sm.load_secrets(self.home, sm.DEFAULT_CONFIG)[0], "from_env")

    def test_check_with_mock_and_missing_cookie(self):
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("cookie: missing", out)
        self.write_env()
        code, out, _ = self.run_cli("check", "--mock", str(FIXTURES))
        self.assertEqual(code, 0, self.stderr.getvalue())
        self.assertIn("cookie: present", out)
        self.assertIn("user id: 999", out)
        self.assertIn("publications: 4", out)

    def test_check_reports_expired_cookie(self):
        self.write_env()
        with mock.patch.object(sm.Fetcher, "get",
                               side_effect=sm.HttpError(401, "substack.com")):
            code, _, err = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("cookie expired, copy a fresh substack.sid", err)


# --------------------------------------------------------------------------- SessionStart hook

class HookTests(HomeTestCase):
    def setUp(self):
        super().setUp()
        self.today = date.today().isoformat()
        self.briefs = self.home / "briefs"
        self.lock = self.home / "run.lock"

    def hook(self):
        code, out, err = self.run_cli("status", "--hook")
        self.assertEqual(code, 0)
        return out, err

    def state(self):
        return json.loads((self.home / "state.json").read_text(encoding="utf-8"))

    def make_brief(self):
        self.briefs.mkdir(parents=True, exist_ok=True)
        path = self.briefs / f"{self.today}.html"
        path.write_text("x", encoding="utf-8")
        return path

    def test_ready_announced_once(self):
        path = self.make_brief()
        out, _ = self.hook()
        self.assertEqual(out, sm.HOOK_READY.format(url=path.resolve().as_uri()) + "\n")
        self.assertIn("file:///", out)
        self.assertEqual(self.state()["announced_date"], self.today)
        self.assertEqual(self.hook()[0], "")
        info = json.loads(self.run_cli("status")[1])
        self.assertTrue(info["brief_exists"])

    def test_no_brief_no_lock_spawns(self):
        out, err = self.hook()
        self.assertEqual(out, sm.HOOK_PREPARING + "\n")
        self.assertTrue(self.lock.is_file())
        self.assertIn("started_at", json.loads(self.lock.read_text(encoding="utf-8")))
        self.assertIn("would spawn:", err)
        cmd = json.loads(err.split("would spawn:", 1)[1].strip())
        self.assertEqual(cmd[:3], ["claude", "-p", sm.CHILD_PROMPT])
        self.assertEqual(cmd[3:], ["--allowedTools", "Bash(python:*)", "Bash(py:*)", "Read",
                                   "Write", "Edit", "Skill"])
        # second session while the lock is fresh: no respawn
        out, err = self.hook()
        self.assertEqual((out, err), (sm.HOOK_STILL + "\n", ""))

    def test_stale_lock_respawns(self):
        self.home.mkdir(parents=True)
        self.lock.write_text(json.dumps({"started_at": "2000-01-01T00:00:00+00:00"}),
                             encoding="utf-8")
        out, err = self.hook()
        self.assertEqual(out, sm.HOOK_PREPARING + "\n")
        self.assertIn("would spawn:", err)

    def test_spawn_failure_removes_lock(self):
        os.environ.pop("SUBSTACK_MORNING_NO_SPAWN")
        self.run_cli("setup")
        cfg = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        cfg["claude_command"] = "definitely-not-a-real-claude-xyz"
        (self.home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        out, _ = self.hook()
        self.assertEqual(out, sm.HOOK_SPAWN_FAILED + "\n")
        self.assertFalse(self.lock.exists())

    def test_spawn_is_detached_with_child_env(self):
        os.environ.pop("SUBSTACK_MORNING_NO_SPAWN")
        with mock.patch.object(sm.shutil, "which", return_value="/usr/bin/claude"), \
                mock.patch.object(sm.subprocess, "Popen") as popen:
            out, _ = self.hook()
        self.assertEqual(out, sm.HOOK_PREPARING + "\n")
        args, kwargs = popen.call_args
        self.assertEqual(args[0][0], "/usr/bin/claude")
        self.assertEqual(kwargs["env"]["SUBSTACK_MORNING_CHILD"], "1")
        self.assertEqual(Path(kwargs["cwd"]), SCRIPT.parent.parent)
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"])
        else:
            self.assertTrue(kwargs["start_new_session"])
        self.assertTrue((self.home / "logs" / f"{self.today}.log").exists())

    def test_child_process_hook_is_silent(self):
        with mock.patch.dict(os.environ, {"SUBSTACK_MORNING_CHILD": "1"}):
            self.assertEqual(self.hook(), ("", ""))
        self.assertFalse(self.lock.exists())

    def test_auto_run_false_asks(self):
        self.home.mkdir(parents=True)
        (self.home / "config.json").write_text('{"auto_run": false}', encoding="utf-8")
        self.assertEqual(self.hook()[0], sm.HOOK_ASK + "\n")
        self.assertFalse(self.lock.exists())

    def test_failure_announced_once_and_no_respawn(self):
        self.write_env()
        self.lock.write_text("{}", encoding="utf-8")
        code, out, _ = self.run_cli("fail", "--reason", f"HTTP 401 from substack.com {SENTINEL}")
        self.assertEqual(code, 0)
        self.assertFalse(self.lock.exists())
        err_file = self.briefs / f"{self.today}-error.txt"
        self.assertNotIn(SENTINEL, err_file.read_text(encoding="utf-8"))
        out, _ = self.hook()
        self.assertEqual(out, sm.HOOK_FAILED.format(
            reason="HTTP 401 from substack.com [redacted]") + "\n")
        self.assertEqual(self.state()["error_announced_date"], self.today)
        self.assertEqual(self.hook(), ("", ""))  # announced already, and no spawn
        self.assertFalse(self.lock.exists())

    def test_hook_swallows_errors(self):
        self.home.mkdir(parents=True)
        (self.home / "config.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.hook(), ("", ""))

    def test_open_brief_and_artifact(self):
        path = self.make_brief()
        self.lock.write_text("{}", encoding="utf-8")
        with mock.patch.object(sm.webbrowser, "open") as wb, \
                mock.patch.object(sm.os, "startfile", create=True) as sf:
            code, out, _ = self.run_cli("open")
        self.assertEqual((code, out.strip()), (0, path.resolve().as_uri()))
        self.assertTrue(wb.called or sf.called)
        self.assertFalse(self.lock.exists())
        (self.home / "config.json").write_text(
            '{"artifact_url": "https://claude.ai/artifact/example"}', encoding="utf-8")
        # A saved artifact_url must not override today's local page (it could be stale).
        with mock.patch.object(sm.webbrowser, "open") as wb, \
                mock.patch.object(sm.os, "startfile", create=True):
            code, out, _ = self.run_cli("open", "--date", self.today)
        self.assertEqual(out.strip(), path.resolve().as_uri())

    def test_open_missing_brief(self):
        with mock.patch.object(sm.webbrowser, "open") as wb:
            code, _, err = self.run_cli("open", "--date", "2001-01-01")
        self.assertEqual(code, 1)
        self.assertIn("no brief found", err)
        wb.assert_not_called()


# --------------------------------------------------------------------------- collect

class CollectTests(HomeTestCase):
    def setUp(self):
        super().setUp()
        self.write_env()
        self.write_state(last_brief_at=None, seen_ids={"post:105": date.today().isoformat()})

    def test_filters_and_dedupe(self):
        data = self.collect()
        ids = [c["id"] for c in data["candidates"]]
        self.assertEqual(set(ids), {"post:101", "post:106", "post:201", "post:601", "post:602",
                                    "post:603", "note:501", "note:503", "note:504"})
        # seen 105, old 102, excluded 103/506, paywalled 301, own 401/502, commented 104/505
        self.assertEqual(data["dropped"], {"seen": 1, "own": 2, "old_or_undated": 1,
                                           "excluded": 2, "paywalled": 1, "already_commented": 2})
        self.assertEqual(len(ids), len(set(ids)))
        by_id = {c["id"]: c for c in data["candidates"]}
        self.assertEqual(by_id["post:101"]["sources"], ["subscriptions", "feed:subscribed"])
        self.assertTrue(by_id["post:201"]["paywalled"] and by_id["post:201"]["readable"])
        self.assertEqual(data["user"], {"handle": "dennisahking", "id": "999"})
        self.assertEqual(data["since"], "2026-09-28T08:00:00+00:00")

    def test_normalized_fields(self):
        by_id = {c["id"]: c for c in self.collect()["candidates"]}
        note = by_id["note:501"]
        self.assertEqual(note["url"], "https://substack.com/@sample-writer/note/c-501")
        self.assertEqual(note["kind"], "note")
        self.assertTrue(note["followed"])
        self.assertEqual(note["audience_size"], 800)
        art = by_id["post:101"]
        self.assertEqual(art["text"], "Boards need clear answers & owners. Second paragraph.")
        self.assertEqual(art["url"],
                         "https://example-pub.substack.com/p/ai-governance-for-boards")
        self.assertEqual(art["writer"], "Sample Writer")
        self.assertEqual(by_id["post:106"]["reaction_count"], 3)
        self.assertEqual(by_id["post:201"]["audience_size"], 12000)
        for c in by_id.values():
            self.assertFalse(any(k.startswith("_") for k in c))
            self.assertFalse(c["excluded"])
            for key in ("title", "subtitle", "writer_handle", "publication", "publication_host",
                        "published_at", "comment_count", "reaction_count", "paywalled",
                        "keyword_hits", "score_parts"):
                self.assertIn(key, c)

    def test_scoring_order(self):
        cands = self.collect()["candidates"]
        scores = [c["score"] for c in cands]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(cands[0]["id"], "post:101")
        by_id = {c["id"]: c for c in cands}
        self.assertEqual(by_id["post:101"]["score_parts"]["keyword"], 25)
        self.assertEqual(by_id["note:503"]["score_parts"]["keyword"], 15)  # extended only
        self.assertEqual(by_id["note:504"]["score_parts"]["keyword"], 0)
        self.assertEqual(by_id["post:106"]["score_parts"]["followed"], 30)
        self.assertGreater(by_id["post:101"]["score"], by_id["post:106"]["score"])

    def test_source_health_records_failures(self):
        health = {h["source"]: h for h in self.collect()["source_health"]}
        self.assertEqual(health["feed:category-technology"]["status"], "failed")
        self.assertIn("HTTP 404", health["feed:category-technology"]["error"])
        self.assertEqual(health["subscriptions"]["status"], "ok")
        self.assertEqual(health["search"]["status"], "ok")

    def test_pool_balance_in_collect(self):
        cands = self.collect(candidate_pool=4)["candidates"]
        kinds = [c["kind"] for c in cands]
        # pool = top 2 articles + top 2 notes; nothing dropped from that pool
        self.assertEqual(sorted(kinds), ["article", "article", "note", "note"])

    def test_select_pool_balance_and_fill(self):
        def item(i, kind, score):
            return {"id": f"{kind}:{i}", "kind": kind, "score": score}
        ranked = [item(i, "article", 90 - i) for i in range(8)] + [item(1, "note", 10)]
        pool = sm.select_pool(sorted(ranked, key=lambda x: -x["score"]), 6)
        self.assertEqual(len(pool), 6)
        self.assertEqual(sum(1 for p in pool if p["kind"] == "note"), 1)  # all notes available
        ranked = [item(i, "article", 90 - i) for i in range(5)] + \
                 [item(i, "note", 50 - i) for i in range(5)]
        pool = sm.select_pool(sorted(ranked, key=lambda x: -x["score"]), 6)
        self.assertEqual(sum(1 for p in pool if p["kind"] == "note"), 3)

    def test_keyword_matching_rules(self):
        self.assertEqual(sm.find_hits("Thoughts on AI and DORA", ["AI", "DORA"]), ["AI", "DORA"])
        self.assertEqual(sm.find_hits("said Dora; paid air", ["AI", "DORA"]), [])
        self.assertEqual(sm.find_hits("eu ai act explained", ["EU AI Act"]), ["EU AI Act"])

    def test_mock_fetcher_enforces_allowlist(self):
        fetcher = sm.Fetcher(SENTINEL, FIXTURES)
        with self.assertRaises(sm.HostNotAllowed):
            fetcher.get("https://evil.example.com/api/v1/subscriptions")


# --------------------------------------------------------------------------- render / commit

def valid_drafts():
    return {
        "date": "2026-09-30",
        "generated_at": "2026-09-30T08:05:00+00:00",
        "source_health": [{"source": "search", "status": "failed", "count": 0, "error": "HTTP 404"}],
        "items": [{
            "id": "post:101", "kind": "article", "title": "AI governance for boards",
            "url": "https://example-pub.substack.com/p/ai-governance-for-boards",
            "writer": "Sample Writer", "publication": "Example Pub",
            "published_at": "2026-09-30T06:00:00+00:00", "comment_count": 2,
            "why": "Core topic, few comments.", "summary": "Boards need answers. </script>",
            "reply_a": "Strong point on ownership.",
            "reply_b": "We saw this at [client type] last quarter.", "flags": ["paywalled"]},
            {"id": "note:501", "kind": "note", "title": "Hot take", "url": "https://substack.com/x",
             "writer": "Sample Writer", "why": "Followed writer.", "summary": "Risk registers.",
             "reply_a": "Agree.", "reply_b": "In [sector] this shows up as [symptom]."}],
    }


class RenderCommitTests(HomeTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(self._tmp.name)
        self.template = self.tmp / "template.html"
        self.template.write_text("<script>const DATA = /*__BRIEF_DATA__*/null;</script>",
                                 encoding="utf-8")

    def write_drafts(self, drafts):
        path = self.tmp / "drafts.json"
        path.write_text(json.dumps(drafts, ensure_ascii=False), encoding="utf-8")
        return path

    def render(self, drafts, *extra):
        return self.run_cli("render", "--drafts", str(self.write_drafts(drafts)),
                            "--template", str(self.template), *extra)

    def test_render_writes_html_md_json(self):
        code, out, err = self.render(valid_drafts())
        self.assertEqual(code, 0, err)
        paths = json.loads(out)
        html_text = Path(paths["html"]).read_text(encoding="utf-8")
        self.assertNotIn("/*__BRIEF_DATA__*/", html_text)
        self.assertIn("<\\/script>", html_text)
        self.assertEqual(html_text.count("</script>"), 1)  # only the template's own tag
        md = Path(paths["md"]).read_text(encoding="utf-8")
        for needle in ("AI governance for boards", "Reply A", "Reply B", "[client type]",
                       "Sample Writer", "https://example-pub.substack.com/p/"):
            self.assertIn(needle, md)
        self.assertEqual(json.loads(Path(paths["json"]).read_text(encoding="utf-8")),
                         valid_drafts())
        self.assertEqual(Path(paths["html"]).name, "2026-09-30.html")

    def test_render_custom_out(self):
        target = self.tmp / "custom" / "brief.html"
        code, out, _ = self.render(valid_drafts(), "--out", str(target))
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())

    def test_dashes_block_rendering(self):
        for field, bad in (("reply_a", "Great point — really"), ("why", "Topic – fit"),
                           ("summary", "A -- B"), ("reply_b", "In [x] -- yes")):
            drafts = valid_drafts()
            drafts["items"][1][field] = bad
            code, out, err = self.render(drafts)
            self.assertEqual(code, 2, field)
            self.assertIn(f"note:501: {field}", err)
            self.assertFalse((self.home / "briefs" / "2026-09-30.html").exists())

    def test_reply_b_requires_slot_and_required_keys(self):
        drafts = valid_drafts()
        drafts["items"][0]["reply_b"] = "No slot here."
        code, _, err = self.render(drafts)
        self.assertEqual(code, 2)
        self.assertIn("post:101: reply_b needs at least one [slot]", err)
        drafts = valid_drafts()
        del drafts["items"][1]["summary"]
        code, _, err = self.render(drafts)
        self.assertEqual(code, 2)
        self.assertIn("missing keys summary", err)

    def test_missing_template_errors_clearly(self):
        code, _, err = self.run_cli("render", "--drafts", str(self.write_drafts(valid_drafts())),
                                    "--template", str(self.tmp / "nope.html"))
        self.assertEqual(code, 1)
        self.assertIn("template not found", err)

    def test_commit_updates_state_atomically(self):
        self.write_state(last_brief_at=None, seen_ids={"post:old": "2000-01-01",
                                                       "post:recent": date.today().isoformat()})
        code, out, _ = self.run_cli("commit", "--drafts", str(self.write_drafts(valid_drafts())))
        self.assertEqual(code, 0)
        state = json.loads((self.home / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["last_brief_at"], "2026-09-30T08:05:00+00:00")
        today = date.today().isoformat()
        self.assertEqual(state["seen_ids"], {"post:recent": today, "post:101": today,
                                             "note:501": today})
        self.assertEqual(list(self.home.glob("*.tmp")), [])

    def test_commit_removes_lock(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "run.lock").write_text("{}", encoding="utf-8")
        self.run_cli("commit", "--drafts", str(self.write_drafts(valid_drafts())))
        self.assertFalse((self.home / "run.lock").exists())


# --------------------------------------------------------------------------- secret leakage

class CookieNeverLeaksTests(HomeTestCase):
    def test_sentinel_absent_from_all_outputs_and_files(self):
        self.run_cli("setup")
        self.write_env(SENTINEL)
        self.write_state(last_brief_at=None, seen_ids={})
        self.run_cli("check", "--mock", str(FIXTURES))
        out = Path(self._tmp.name) / "work" / "candidates.json"
        self.run_cli("collect", "--out", str(out), "--mock", str(FIXTURES), "--now", NOW)
        drafts = valid_drafts()
        drafts_path = Path(self._tmp.name) / "work" / "drafts.json"
        drafts_path.write_text(json.dumps(drafts), encoding="utf-8")
        template = Path(self._tmp.name) / "work" / "t.html"
        template.write_text("/*__BRIEF_DATA__*/null", encoding="utf-8")
        self.run_cli("render", "--drafts", str(drafts_path), "--template", str(template))
        self.run_cli("commit", "--drafts", str(drafts_path))
        self.run_cli("status")
        self.run_cli("status", "--hook")
        # Network failure path with the cookie attached.
        with mock.patch.object(sm._OPENER, "open", side_effect=urllib.error.HTTPError(
                "https://substack.com/x", 401, "nope", {}, None)), \
                mock.patch.object(sm.time, "sleep"):
            self.run_cli("check")

        self.assertNotIn(SENTINEL, self.stdout.getvalue())
        self.assertNotIn(SENTINEL, self.stderr.getvalue())
        self.assertIn("cookie: present", self.stdout.getvalue())
        scanned = 0
        for path in Path(self._tmp.name).rglob("*"):
            if path.is_file() and path.name != ".env":
                scanned += 1
                self.assertNotIn(SENTINEL, path.read_text(encoding="utf-8", errors="replace"),
                                 str(path))
        self.assertGreaterEqual(scanned, 7)

    def test_redact_scrubs_registered_secret(self):
        sm._SECRETS.append(SENTINEL)
        self.assertEqual(sm.redact(f"x {SENTINEL} y"), "x [redacted] y")
        self.assertNotIn(SENTINEL, sm.safe_error(ValueError(f"bad {SENTINEL}")))


if __name__ == "__main__":
    unittest.main()
