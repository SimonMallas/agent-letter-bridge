"""Initiated first-message send. Reply path is not used for routing."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alb.grant import store as grants  # noqa: E402
from alb.initiate import budget, destinations, ids, reservation, send as initiate  # noqa: E402
from alb.letter import store as letters  # noqa: E402
from alb.outbound import store as outbound  # noqa: E402
from alb.send import reply  # noqa: E402


CHAT = "fixture-chat"
LABEL = "owner"
SEAT = "fixture-agent"
INTENT = "hello-1"
REASON = "dogfood the first-message path"
TEXT = "hello from the agent"


class FakeSender:
    def __init__(self, outcome="sent"):
        self.outcome = outcome
        self.calls = []

    def send(self, chat_id, text):
        self.calls.append((chat_id, text))
        if self.outcome == "ambiguous":
            raise reply.AmbiguousOutcome("connection reset after POST")
        if self.outcome == "refused":
            raise reply.DefiniteRefusal("refused with HTTP 400")
        if self.outcome == "throttled":
            raise reply.Throttled("throttled with HTTP 429", retry_after=1)
        return "42"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.outbox = self.root / "outbox"
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": [CHAT]}), encoding="utf-8")
        rec = grants.create(self.state, "telegram", CHAT)
        destinations.bind(self.state, LABEL, rec["grant_id"])
        self.grant = rec

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, sender=None, **kw):
        sender = sender or FakeSender()
        params = dict(label=LABEL, intent_id=INTENT, text=TEXT, reason=REASON,
                      seat=SEAT)
        params.update(kw)
        oid = initiate.send_initiated(
            sender, self.state, self.outbox, self.allow, **params)
        return oid, sender


class Identity(Base):
    def test_outbound_id_is_length_prefixed(self):
        a = ids.outbound_id(self.grant["grant_id"], SEAT, "a")
        self.assertTrue(a.startswith("v1-"))
        self.assertEqual(len(a), 3 + 64)
        letters._check_id(a)
        self.assertNotEqual(
            ids.length_prefixed("a", "bc"),
            ids.length_prefixed("ab", "c"))

    def test_raw_chat_label_is_refused_before_any_write(self):
        before = list(self.state.rglob("*"))
        with self.assertRaises(ids.UsageError):
            initiate.send_initiated(
                FakeSender(), self.state, self.outbox, self.allow,
                label="999001002", intent_id=INTENT, text=TEXT,
                reason=REASON, seat=SEAT)
        self.assertEqual(list(self.state.rglob("*")), before)


class HappyPath(Base):
    def test_send_reaches_the_granted_chat_and_hides_it_from_mail(self):
        oid, sender = self.send()
        self.assertEqual(sender.calls, [(CHAT, TEXT)])
        letter = letters.resolve(self.outbox, oid)
        blob = (self.outbox / f"{oid}.md").read_text(encoding="utf-8")
        self.assertNotIn(CHAT, blob)
        self.assertNotIn(self.grant["grant_id"], blob)
        self.assertEqual(letter.meta.get("type"), "initiated")
        self.assertEqual(letter.meta.get("reason"), REASON)
        self.assertEqual(letter.body, TEXT)

    def test_same_intent_does_not_send_twice(self):
        oid1, sender = self.send()
        with self.assertRaises(initiate.TerminalExists):
            self.send(sender)
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(oid1, ids.outbound_id(self.grant["grant_id"], SEAT, INTENT))

    def test_unknown_label_creates_nothing(self):
        with self.assertRaises(destinations.UnknownLabel):
            initiate.send_initiated(
                FakeSender(), self.state, self.outbox, self.allow,
                label="nobody", intent_id=INTENT, text=TEXT,
                reason=REASON, seat=SEAT)
        self.assertFalse(self.outbox.exists() or list(self.outbox.glob("*")))

    def test_missing_reason_is_refused(self):
        with self.assertRaises(ids.UsageError):
            self.send(reason="   ")

    def test_route_ref_is_not_a_chat_fingerprint(self):
        oid, _ = self.send()
        letter = letters.resolve(self.outbox, oid)
        ref = letter.meta["route_ref"]
        self.assertNotEqual(ref, self.grant["binding_key"])
        self.assertEqual(
            ref, ids.route_ref(self.grant["grant_id"], self.grant["binding_key"]))
        for n in range(1_000_000):
            if grants.binding_key("telegram", str(n)) == ref:
                self.fail("route_ref matched a telegram chat fingerprint")

    def test_refused_receipt_repairs_stranded_reservation(self):
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, "strand-1")
        outbound.record_event(self.state, oid, "refused", detail="probe")
        rec = reservation.create(self.state, {
            "outbound_id": oid,
            "binding_key": self.grant["binding_key"],
            "grant_id": self.grant["grant_id"],
            "intent_id": "strand-1",
            "seat": SEAT,
            "payload_digest": "0" * 64,
            "day_key": "2099-01-01",
            "hour_key": "2099-01-01T00",
            "status": "reserved",
        })
        with self.assertRaises(initiate.TerminalExists):
            initiate._cross(
                FakeSender(), self.state, self.allow, self.grant, rec,
                text=TEXT, reason=REASON, seat=SEAT, intent_id="strand-1")
        self.assertEqual(reservation.load(self.state, oid)["status"], "refunded")
        used_day, _hour, active_q = budget.counts(
            self.state, self.grant["binding_key"],
            day_key="2099-01-01", hour_key="2099-01-01T00")
        self.assertEqual((used_day, active_q), (0, 0))

    def test_ambiguous_receipt_repairs_stranded_reservation(self):
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, "strand-2")
        outbound.record_event(self.state, oid, "ambiguous", detail="probe")
        rec = reservation.create(self.state, {
            "outbound_id": oid,
            "binding_key": self.grant["binding_key"],
            "grant_id": self.grant["grant_id"],
            "intent_id": "strand-2",
            "seat": SEAT,
            "payload_digest": "0" * 64,
            "day_key": "2099-01-01",
            "hour_key": "2099-01-01T00",
            "status": "held",
        })
        with self.assertRaises(initiate.TerminalExists):
            initiate._cross(
                FakeSender(), self.state, self.allow, self.grant, rec,
                text=TEXT, reason=REASON, seat=SEAT, intent_id="strand-2")
        self.assertEqual(
            reservation.load(self.state, oid)["status"], "settled-ambiguous")


class Budget(Base):
    def test_third_message_in_an_hour_is_refused(self):
        sender = FakeSender()
        t0 = 1_700_000_000
        for i in range(2):
            initiate.send_initiated(
                sender, self.state, self.outbox, self.allow,
                label=LABEL, intent_id=f"n{i}", text=TEXT, reason=REASON,
                seat=SEAT, now=t0)
        with self.assertRaises(budget.CapacityRefused):
            initiate.send_initiated(
                sender, self.state, self.outbox, self.allow,
                label=LABEL, intent_id="n2", text=TEXT, reason=REASON,
                seat=SEAT, now=t0)
        self.assertEqual(len(sender.calls), 2)

    def test_day_cap_is_three_across_hours(self):
        sender = FakeSender()
        t0 = 1_700_000_000
        initiate.send_initiated(
            sender, self.state, self.outbox, self.allow,
            label=LABEL, intent_id="d0", text=TEXT, reason=REASON,
            seat=SEAT, now=t0)
        initiate.send_initiated(
            sender, self.state, self.outbox, self.allow,
            label=LABEL, intent_id="d1", text=TEXT, reason=REASON,
            seat=SEAT, now=t0)
        initiate.send_initiated(
            sender, self.state, self.outbox, self.allow,
            label=LABEL, intent_id="d2", text=TEXT, reason=REASON,
            seat=SEAT, now=t0 + 3600)
        with self.assertRaises(budget.CapacityRefused):
            initiate.send_initiated(
                sender, self.state, self.outbox, self.allow,
                label=LABEL, intent_id="d3", text=TEXT, reason=REASON,
                seat=SEAT, now=t0 + 3600)
        self.assertEqual(len(sender.calls), 3)


class Authority(Base):
    def test_disabled_grant_cannot_send(self):
        grants.disable(self.state, self.grant["grant_id"])
        with self.assertRaises(grants.PolicyError):
            self.send()

    def test_unresolved_sending_then_revoke_is_ambiguous_not_refund(self):
        sender = FakeSender()
        real = outbound.record_event

        def crash_before_sent(state, oid, event, **kw):
            if event == "sent":
                raise RuntimeError("accepted; no sent receipt")
            return real(state, oid, event, **kw)

        with mock.patch.object(outbound, "record_event", side_effect=crash_before_sent):
            with self.assertRaises(RuntimeError):
                self.send(sender)
        grants.disable(self.state, self.grant["grant_id"])
        with self.assertRaises(reply.AmbiguousOutcome):
            self.send(sender)
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, INTENT)
        self.assertEqual(reservation.load(self.state, oid)["status"],
                         "settled-ambiguous")
        self.assertEqual(len(sender.calls), 1)

    def test_disabled_grant_on_resume_refunds(self):
        sender = FakeSender("throttled")
        with self.assertRaises(reply.Throttled):
            self.send(sender)
        grants.disable(self.state, self.grant["grant_id"])
        sender.outcome = "sent"
        with self.assertRaises(grants.PolicyError):
            self.send(sender)
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, INTENT)
        self.assertEqual(reservation.load(self.state, oid)["status"], "refunded")
        self.assertEqual(len(sender.calls), 1)

    def test_terminal_repair_without_letter(self):
        sender = FakeSender("throttled")
        with self.assertRaises(reply.Throttled):
            self.send(sender, intent_id="term-sent")
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, "term-sent")
        (self.outbox / f"{oid}.md").unlink()
        outbound.record_event(self.state, oid, "sent")
        with self.assertRaises(initiate.TerminalExists):
            self.send(FakeSender(), intent_id="term-sent")
        self.assertEqual(reservation.load(self.state, oid)["status"], "settled-sent")

    def test_missing_window_keys_quarantine(self):
        self.send(intent_id="charged-a")
        self.send(intent_id="charged-b")
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, "charged-a")
        path = self.state / "reservations" / f"{oid}.json"
        rec = json.loads(path.read_text())
        del rec["day_key"]
        del rec["hour_key"]
        path.write_text(json.dumps(rec), encoding="utf-8")
        sender = FakeSender()
        with self.assertRaises(budget.Quarantined):
            self.send(sender, intent_id="third")
        self.assertEqual(sender.calls, [])

    def test_held_with_sent_receipt_does_not_block_queue(self):
        t0 = 1_700_000_000
        for i in range(5):
            sender = FakeSender("throttled")
            with self.assertRaises(reply.Throttled):
                self.send(sender, intent_id=f"old-{i}", now=t0 + i * 86400)
            oid = ids.outbound_id(self.grant["grant_id"], SEAT, f"old-{i}")
            outbound.record_event(self.state, oid, "sent",
                                  platform_message_id=f"fix-{i}")
        sender = FakeSender()
        self.send(sender, intent_id="new-work", now=t0 + 86400 * 2)
        self.assertEqual(len(sender.calls), 1)

    def test_allowlist_removal_refuses_and_does_not_call_platform(self):
        self.allow.write_text(json.dumps({"chats": ["other"]}), encoding="utf-8")
        sender = FakeSender()
        with self.assertRaises(reply.NotPermitted):
            self.send(sender)
        self.assertEqual(sender.calls, [])

    def test_throttle_resume_sends_once(self):
        sender = FakeSender("throttled")
        with self.assertRaises(reply.Throttled):
            self.send(sender)
        sender.outcome = "sent"
        oid, _ = self.send(sender)
        self.assertEqual(len(sender.calls), 2)
        self.assertTrue(oid.startswith("v1-"))

    def test_throttle_then_sending_crash_is_not_retried(self):
        sender = FakeSender("throttled")
        with self.assertRaises(reply.Throttled):
            self.send(sender)
        sender.outcome = "sent"
        real = outbound.record_event

        def crash_before_sent(state, oid, event, **kw):
            if event == "sent":
                raise RuntimeError("accepted after throttle resume; no sent receipt")
            return real(state, oid, event, **kw)

        with mock.patch.object(outbound, "record_event", side_effect=crash_before_sent):
            with self.assertRaises(RuntimeError):
                self.send(sender)
        self.assertEqual(len(sender.calls), 2)
        with self.assertRaises(reply.AmbiguousOutcome):
            self.send(sender)
        self.assertEqual(len(sender.calls), 2)

    def test_latest_sending_wins_over_earlier_throttle_at_double_digits(self):
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, "ord-1")
        receipts = self.state / "receipts" / oid
        receipts.mkdir(parents=True)
        events = ["composed", "sending", "throttled"]
        events += ["sending", "throttled"] * 4
        events.append("sending")
        for i, name in enumerate(events, 1):
            (receipts / f"{i}-{name}.json").write_text("{}", encoding="utf-8")
        self.assertGreaterEqual(len(events), 10)
        self.assertTrue(initiate._unresolved_sending(self.state, oid))

    def test_sending_without_terminal_is_not_retried(self):
        sender = FakeSender()
        real = outbound.record_event

        def crash_before_sent(state, oid, event, **kw):
            if event == "sent":
                raise RuntimeError("accepted remotely; terminal receipt not yet written")
            return real(state, oid, event, **kw)

        with mock.patch.object(outbound, "record_event", side_effect=crash_before_sent):
            with self.assertRaises(RuntimeError):
                self.send(sender)
        self.assertEqual(len(sender.calls), 1)
        with self.assertRaises(reply.AmbiguousOutcome):
            self.send(sender)
        self.assertEqual(len(sender.calls), 1)

    def test_ambiguous_never_auto_retries(self):
        sender = FakeSender("ambiguous")
        with self.assertRaises(reply.AmbiguousOutcome):
            self.send(sender)
        with self.assertRaises(initiate.TerminalExists):
            self.send(FakeSender())
        self.assertEqual(len(sender.calls), 1)


class ReplyUntouched(Base):
    def test_send_does_not_call_reply_send(self):
        with mock.patch.object(reply, "send_reply") as patched:
            self.send()
            patched.assert_not_called()

    def test_cli_refuses_send_combined_with_reply_to(self):
        from alb import cli
        rc = cli.main([
            "--root", str(self.root), "--config", str(self.root / "missing.env"),
            "--send", "--reply-to", "x", "--text", "y", "--to", LABEL,
            "--id", INTENT, "--reason", REASON,
        ])
        self.assertEqual(rc, 2)

    def test_resume_missing_letter_refuses(self):
        sender = FakeSender("throttled")
        with self.assertRaises(reply.Throttled):
            self.send(sender)
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, INTENT)
        (self.outbox / f"{oid}.md").unlink()
        sender.outcome = "sent"
        with self.assertRaises(initiate.AuthorityRefused):
            self.send(sender)
        self.assertEqual(len(sender.calls), 1)

    def test_resume_altered_letter_body_refuses(self):
        sender = FakeSender("throttled")
        with self.assertRaises(reply.Throttled):
            self.send(sender)
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, INTENT)
        path = self.outbox / f"{oid}.md"
        path.write_text(path.read_text().replace(TEXT, "fixture altered body"),
                        encoding="utf-8")
        sender.outcome = "sent"
        with self.assertRaises(initiate.AuthorityRefused):
            self.send(sender)
        self.assertEqual(len(sender.calls), 1)

    def test_resume_altered_letter_route_refuses(self):
        sender = FakeSender("throttled")
        with self.assertRaises(reply.Throttled):
            self.send(sender)
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, INTENT)
        path = self.outbox / f"{oid}.md"
        original_ref = ids.route_ref(self.grant["grant_id"], self.grant["binding_key"])
        path.write_text(path.read_text().replace(original_ref, "0" * 16),
                        encoding="utf-8")
        sender.outcome = "sent"
        with self.assertRaises(initiate.AuthorityRefused):
            self.send(sender)
        self.assertEqual(len(sender.calls), 1)

    def test_reservation_without_claim_never_crosses(self):
        oid = ids.outbound_id(self.grant["grant_id"], SEAT, "no-claim")
        reservation.create(self.state, {
            "outbound_id": oid,
            "binding_key": self.grant["binding_key"],
            "grant_id": self.grant["grant_id"],
            "intent_id": "no-claim",
            "seat": SEAT,
            "payload_digest": ids.payload_digest(
                body=TEXT, kind="initiated", seat=SEAT, intent_id="no-claim",
                binding_key=self.grant["binding_key"], reason=REASON),
            "day_key": "2099-01-01",
            "hour_key": "2099-01-01T00",
            "status": "reserved",
        })
        sender = FakeSender()
        with self.assertRaises(initiate.AuthorityRefused):
            initiate.send_initiated(
                sender, self.state, self.outbox, self.allow,
                label=LABEL, intent_id="no-claim", text=TEXT, reason=REASON,
                seat=SEAT)
        self.assertEqual(sender.calls, [])

    def test_cli_refuses_non_telegram_grant(self):
        from types import SimpleNamespace
        from alb import cli
        other = grants.create(self.state, "fixture-other-platform", CHAT)
        destinations.bind(self.state, "otherplatform", other["grant_id"])
        args = SimpleNamespace(to_label="otherplatform", intent_id="wrong-platform",
                               text=TEXT, reason=REASON, photo=None)
        sender = FakeSender()
        with mock.patch.object(cli.api, "Telegram", return_value=sender):
            rc = cli._send_initiated(
                args, {"ALB_TOKEN": None, "ALB_FROM": SEAT},
                self.root, self.root / "mail", self.state)
        self.assertEqual(sender.calls, [])
        self.assertEqual(rc, 1)

    def test_cli_does_not_pair_refuse_photo_with_reply_to(self):
        """0.3.1: --reply-to --photo is allowed. The pair gate must not fire."""
        import io
        from alb import cli
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            rc = cli.main([
                "--root", str(self.root),
                "--config", str(self.root / "missing.env"),
                "--reply-to", "x", "--text", "y", "--photo", "/tmp/x.png",
            ])
        self.assertNotEqual(rc, 0)
        self.assertNotIn("--photo is not valid with --reply-to", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
