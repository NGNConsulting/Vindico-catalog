#!/usr/bin/env python3
"""
Offline tests for fill_drafts.py -- no network, no API key.

The live web_search/web_fetch call can only run in CI (the repo secret), so
these pin the parts that decide what gets written to settlements.json: JSON
extraction, the official-host guard, deadline checks, the "not claimable"
flag, and the pause_turn resume loop, against response shapes taken from the
Messages API docs.

    python scripts/test_fill_drafts.py
"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fill_drafts as fd  # noqa: E402

TODAY = date(2026, 10, 13)


def shell(**over):
    e = {
        "id": "x-2026", "name": "X settlement",
        "eligibility": "REVIEW: verify on official administrator site and describe who qualifies.",
        "payout": "REVIEW", "deadline": "REVIEW",
        "url": "https://topclassactions.com/x", "keywords": [], "noProofRequired": False,
    }
    e.update(over)
    return e


def good(**over):
    r = {
        "claimable": True, "official_url": "https://www.xsettlement.com/",
        "eligibility": "Bought X between 2020 and 2025.", "payout": "$10 cash",
        "deadline": "2026-12-01", "confidence": "high", "evidence": "FAQ page",
    }
    r.update(over)
    return r


HOSTS = {"xsettlement.com", "topclassactions.com"}


class ExtractJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(fd.extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_with_preamble(self):
        self.assertEqual(fd.extract_json('Here you go:\n```json\n{"a": 2}\n```\nDone.'), {"a": 2})

    def test_garbage(self):
        self.assertIsNone(fd.extract_json("no json here"))
        self.assertIsNone(fd.extract_json('{"a": '))

    def test_non_object(self):
        self.assertIsNone(fd.extract_json("[1, 2]"))


class CollectHosts(unittest.TestCase):
    def test_search_and_fetch_results(self):
        content = [
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "q"}},
            {"type": "web_search_tool_result", "tool_use_id": "1", "content": [
                {"type": "web_search_result", "url": "https://www.XSettlement.com/faq", "title": "t"},
                {"type": "web_search_result", "url": "https://claimdepot.com/y", "title": "t"},
            ]},
            {"type": "web_fetch_tool_result", "tool_use_id": "2", "content": {
                "type": "web_fetch_result", "url": "https://xsettlement.com/dates"}},
            # error shapes must not raise
            {"type": "web_search_tool_result", "tool_use_id": "3", "content": {
                "type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
            {"type": "web_fetch_tool_result", "tool_use_id": "4", "content": {
                "type": "web_fetch_tool_error", "error_code": "url_not_accessible"}},
            {"type": "text", "text": "ignored"},
        ]
        self.assertEqual(fd.collect_hosts(content), {"xsettlement.com", "claimdepot.com"})


class ApplyDraft(unittest.TestCase):
    def test_fills_everything_and_stamps(self):
        e = shell()
        filled, note = fd.apply_draft(e, good(), HOSTS, TODAY)
        self.assertEqual(set(filled), {"eligibility", "payout", "deadline", "url"})
        self.assertIsNone(note)
        self.assertEqual(e["deadline"], "2026-12-01")
        self.assertEqual(e["url"], "https://www.xsettlement.com/")
        self.assertTrue(e["aiDrafted"])

    def test_low_confidence_is_noop(self):
        e = shell()
        self.assertEqual(fd.apply_draft(e, good(confidence="low"), HOSTS, TODAY), ([], None))
        self.assertEqual(e, shell())

    def test_official_host_must_have_been_returned_by_a_tool(self):
        e = shell()
        filled, note = fd.apply_draft(e, good(official_url="https://invented-site.com"), HOSTS, TODAY)
        self.assertEqual(filled, [])
        self.assertIn("not returned", note)
        self.assertEqual(e, shell())

    def test_aggregator_is_never_official(self):
        e = shell()
        filled, note = fd.apply_draft(
            e, good(official_url="https://www.topclassactions.com/x"), HOSTS, TODAY)
        self.assertEqual(filled, [])
        self.assertIn("aggregator", note)

    def test_unsafe_or_missing_url_rejected(self):
        for bad in ("javascript:alert(1)", "intent://x#Intent;end", "", None, "has space.com"):
            e = shell()
            filled, _ = fd.apply_draft(e, good(official_url=bad), HOSTS, TODAY)
            self.assertEqual(filled, [], bad)
            self.assertEqual(e, shell(), bad)

    def test_malformed_deadline_left_as_review_other_fields_still_land(self):
        for bad in ("12/01/2026", "2026-13-40", None):
            e = shell()
            filled, _ = fd.apply_draft(e, good(deadline=bad), HOSTS, TODAY)
            self.assertEqual(e["deadline"], "REVIEW", bad)
            self.assertIn("payout", filled)

    # 2026-10-19 run: Globe Life / MCNA / NYC Central Booking / State Farm Louisiana
    # all had a claim deadline of the run day itself and were filled anyway.
    def test_deadline_same_day_as_run_is_flagged_not_filled(self):
        run_day = date(2026, 10, 19)
        e = shell()
        filled, note = fd.apply_draft(e, good(deadline="2026-10-19"), HOSTS, run_day)
        self.assertEqual(filled, [])
        self.assertIn("closes in 0 day(s)", note)
        self.assertIn("flagged for deletion", note)
        self.assertEqual(e["deadline"], "REVIEW")
        self.assertEqual(e["payout"], "REVIEW")
        self.assertTrue(e["eligibility"].startswith("REVIEW: closes in 0 day(s)"))
        self.assertNotIn("aiDrafted", e)
        self.assertEqual(e["url"], shell()["url"])                      # aggregator link untouched

    def test_already_closed_deadline_is_flagged(self):
        e = shell()
        filled, note = fd.apply_draft(e, good(deadline="2025-11-14"), HOSTS, TODAY)   # HSHS
        self.assertEqual(filled, [])
        self.assertIn("already closed", note)
        self.assertTrue(e["eligibility"].startswith("REVIEW: already closed"))

    def test_runway_boundary(self):
        for days, expect_filled in ((fd.MIN_RUNWAY_DAYS - 1, False), (fd.MIN_RUNWAY_DAYS, True)):
            d = date.fromordinal(TODAY.toordinal() + days).isoformat()
            e = shell()
            filled, _ = fd.apply_draft(e, good(deadline=d), HOSTS, TODAY)
            self.assertEqual("deadline" in filled, expect_filled, d)
            self.assertEqual(e["deadline"], d if expect_filled else "REVIEW", d)

    def test_short_runway_flag_needs_a_trusted_answer(self):
        e = shell()
        self.assertEqual(fd.apply_draft(e, good(deadline="2026-10-13", confidence="low"), HOSTS, TODAY), ([], None))
        self.assertEqual(e, shell())
        e = shell()
        filled, note = fd.apply_draft(
            e, good(deadline="2026-10-13", official_url="https://invented-site.com"), HOSTS, TODAY)
        self.assertEqual(filled, [])
        self.assertIn("not returned", note)          # the host guard fires first
        self.assertEqual(e, shell())

    def test_short_runway_does_not_clobber_a_human_written_eligibility(self):
        e = shell(eligibility="Human wrote this.")
        filled, note = fd.apply_draft(e, good(deadline="2026-10-13"), HOSTS, TODAY)
        self.assertEqual(filled, [])
        self.assertEqual(e["eligibility"], "Human wrote this.")
        self.assertIn("flagged for deletion", note)

    def test_days_left(self):
        self.assertEqual(fd.days_left("2026-10-13", TODAY), 0)
        self.assertEqual(fd.days_left("2026-10-20", TODAY), 7)
        self.assertEqual(fd.days_left("2026-10-12", TODAY), -1)
        for bad in (None, "", "12/01/2026", "2026-13-40", 20261013):
            self.assertIsNone(fd.days_left(bad, TODAY), bad)

    def test_never_clobbers_resolved_fields(self):
        e = shell(eligibility="Human wrote this.", payout="$5", deadline="2026-11-01")
        filled, _ = fd.apply_draft(e, good(), HOSTS, TODAY)
        self.assertEqual((e["eligibility"], e["payout"], e["deadline"]), ("Human wrote this.", "$5", "2026-11-01"))
        self.assertEqual(filled, ["url"])

    def test_not_claimable_is_flagged_in_place_not_deleted(self):
        e = shell()
        filled, note = fd.apply_draft(
            e, good(claimable=False, evidence="injunctive relief only, no claim form"), HOSTS, TODAY)
        self.assertEqual(filled, [])
        self.assertIn("not claimable", note)
        self.assertTrue(e["eligibility"].startswith("REVIEW: likely NOT claimable"))
        self.assertEqual(e["payout"], "REVIEW")           # still a REVIEW shell -> CI stays red
        self.assertNotIn("aiDrafted", e)

    # The next three use the model's real output from the first live run (2026-10-13).
    AMWELL_ELIG = ("People who used the Appointment Booking Tool on the LiveHealth Online website, "
                   "iOS app or Android app between October 2024 and August 2025. The October "
                   "2024-August 2025 class period comes from an aggregator listing. The official "
                   "home page I read did not state it.")
    FMUSA_PAYOUT = ("$75 alternative cash payment, or documented out-of-pocket losses (up to $4,500 "
                    "per the aggregator listing) plus up to $100 for lost time, with identity "
                    "protection services")
    TELEFLORA_ELIG = ("People in the U.S. who did not give Teleflora LLC their phone number, and whose "
                      "residential or cellular number had been on the National Do Not Call Registry for "
                      "at least 30 days before Teleflora sent them at least two promotional texts within "
                      "a 12-month period, between May 9, 2021 and the date of preliminary approval.")

    def test_commentary_about_sources_is_rejected_not_written(self):
        e = shell()
        filled, note = fd.apply_draft(e, good(eligibility=self.AMWELL_ELIG), HOSTS, TODAY)
        self.assertTrue(e["eligibility"].startswith("REVIEW:"))          # left for a human
        self.assertNotIn("eligibility", filled)
        self.assertIn("eligibility contains commentary", note)
        self.assertIn("payout", filled)                                   # other fields still land

    def test_aggregator_reference_in_payout_is_rejected(self):
        e = shell()
        filled, note = fd.apply_draft(e, good(payout=self.FMUSA_PAYOUT), HOSTS, TODAY)
        self.assertEqual(e["payout"], "REVIEW")
        self.assertIn("payout contains commentary", note)
        self.assertIn("eligibility", filled)

    def test_over_long_fields_are_rejected_never_truncated(self):
        self.assertGreater(len(self.TELEFLORA_ELIG), fd.MAX_ELIGIBILITY)
        e = shell()
        filled, note = fd.apply_draft(e, good(eligibility=self.TELEFLORA_ELIG), HOSTS, TODAY)
        self.assertTrue(e["eligibility"].startswith("REVIEW:"))
        self.assertIn("too long", note)
        e2 = shell()
        fd.apply_draft(e2, good(payout="x" * (fd.MAX_PAYOUT + 1)), HOSTS, TODAY)
        self.assertEqual(e2["payout"], "REVIEW")

    def test_clean_field_accepts_normal_text_and_collapses_whitespace(self):
        self.assertEqual(fd.clean_field("  $50 cash\n or up to $2,500 documented losses ", 120),
                         ("$50 cash or up to $2,500 documented losses", None))
        self.assertEqual(fd.clean_field("Bought X between 2020 and 2025.", 280),
                         ("Bought X between 2020 and 2025.", None))
        self.assertEqual(fd.clean_field(None, 120), (None, None))

    def test_not_claimable_needs_high_confidence(self):
        e = shell()
        self.assertEqual(fd.apply_draft(e, good(claimable=False, confidence="low"), HOSTS, TODAY), ([], None))
        self.assertEqual(e, shell())


class ResearchLoop(unittest.TestCase):
    def payload(self, stop, content, **usage):
        return {"stop_reason": stop, "content": content,
                "usage": {"input_tokens": 100, "output_tokens": 10, **usage}}

    def test_pause_turn_resumes_without_a_fake_user_message(self):
        paused_content = [
            {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}},
            {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
                {"type": "web_search_result", "url": "https://xsettlement.com/", "title": "t"}]},
        ]
        final_content = [{"type": "text", "text": '{"confidence": "high", "claimable": true}'}]
        responses = [self.payload("pause_turn", paused_content),
                     self.payload("end_turn", final_content,
                                  server_tool_use={"web_search_requests": 2})]
        sent = []

        def fake_post(api_key, body):
            sent.append(body)
            return responses[len(sent) - 1]

        with mock.patch.object(fd, "post_messages", side_effect=fake_post):
            result, hosts, usage = fd.research("k", "X", "https://agg/x", TODAY)

        self.assertEqual(result, {"confidence": "high", "claimable": True})
        self.assertEqual(hosts, {"xsettlement.com"})
        self.assertEqual(len(sent), 2)
        roles = [m["role"] for m in sent[1]["messages"]]
        self.assertEqual(roles, ["user", "assistant"])           # no extra "Continue." turn
        self.assertEqual(sent[1]["messages"][1]["content"], paused_content)
        self.assertEqual(usage, {"input": 200, "output": 20, "searches": 2})

    def test_request_shape(self):
        sent = []

        def fake_post(api_key, body):
            sent.append(body)
            return self.payload("end_turn", [{"type": "text", "text": "{}"}])

        with mock.patch.object(fd, "post_messages", side_effect=fake_post):
            fd.research("k", "X", "https://agg/x", TODAY)
        body = sent[0]
        self.assertEqual(body["model"], fd.MODEL)
        self.assertEqual([t["type"] for t in body["tools"]], ["web_search_20260209", "web_fetch_20260209"])
        self.assertNotIn("code_execution", [t["name"] for t in body["tools"]])
        self.assertNotIn("thinking", body)                       # omit; never send "disabled"
        self.assertNotIn("tool_choice", body)                    # forced tool use 400s on Sonnet 5.5
        self.assertIn("2026-10-13", body["messages"][0]["content"])

    def test_refusal_and_max_tokens_return_none(self):
        for stop in ("refusal", "max_tokens"):
            with mock.patch.object(fd, "post_messages",
                                   return_value=self.payload(stop, [{"type": "text", "text": "{}"}])):
                result, _, _ = fd.research("k", "X", "u", TODAY)
            self.assertIsNone(result, stop)

    def test_gives_up_after_continuation_limit(self):
        paused = self.payload("pause_turn", [{"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}}])
        with mock.patch.object(fd, "post_messages", return_value=paused) as m:
            result, _, _ = fd.research("k", "X", "u", TODAY)
        self.assertIsNone(result)
        self.assertEqual(m.call_count, fd.MAX_CONTINUATIONS + 1)

    def test_api_failure_returns_none(self):
        with mock.patch.object(fd, "post_messages", return_value=None):
            result, hosts, _ = fd.research("k", "X", "u", TODAY)
        self.assertIsNone(result)
        self.assertEqual(hosts, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
