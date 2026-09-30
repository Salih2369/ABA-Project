"""Live-shell HTTP/WebSocket tests. SYNTHETIC ONLY; requires requirements-live.txt."""
import asyncio
import importlib.util
import unittest

from aba_demo.live.scenarios import SCENARIOS

HAS_API_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
BASE = "http://127.0.0.1:8767"
ORIGIN = {"origin": BASE}


class FakeClock:
    def __init__(self):
        self.now = 50.0

    def __call__(self):
        return self.now


@unittest.skipUnless(HAS_API_DEPS, "live API deps missing: pip install -r requirements-live.txt")
class LiveApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from aba_demo.live.api import create_app
        from aba_demo.live.runtime import SessionManager
        self.clock = FakeClock()
        self.manager = SessionManager(max_sessions=2, clock=self.clock)
        self.client = TestClient(create_app(self.manager, port=8767), base_url=BASE)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def create(self, scenario="table-routine"):
        response = self.client.post("/api/live/sessions", json={"scenario": scenario}, headers=ORIGIN)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["session_id"]

    def command(self, session_id, command, **extra):
        return self.client.post(f"/api/live/sessions/{session_id}/commands",
                                json={"command": command, **extra}, headers=ORIGIN)

    def run_to(self, session_id, final="start"):
        for name in ("open", "select_target", "start"):
            self.assertEqual(self.command(session_id, name).status_code, 200)
            if name == final:
                break

    def test_health_declares_simulation(self):
        body = self.client.get("/api/live/health").json()
        self.assertEqual(body, {"status": "ok", "mode": "simulation", "camera": False, "inference": False})

    def test_security_headers(self):
        response = self.client.get("/api/live/health")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_html_shell_is_revalidated(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_scenarios_are_listed(self):
        scenarios = self.client.get("/api/live/scenarios").json()["scenarios"]
        self.assertEqual({s["id"] for s in scenarios}, set(SCENARIOS))

    def test_lifecycle_over_http(self):
        session_id = self.create()
        self.run_to(session_id)
        body = self.client.get(f"/api/live/sessions/{session_id}").json()
        self.assertEqual(body["state"], "running")
        self.assertEqual(body["provider"], "synthetic")
        self.assertEqual(body["recording"], "simulated_ledger")
        self.assertFalse(body["recorder"]["pixels_stored"])
        self.assertEqual(self.command(session_id, "start").status_code, 409)
        self.assertEqual(self.command(session_id, "pause").json()["state"], "paused")
        stopped = self.command(session_id, "stop").json()
        self.assertEqual((stopped["state"], stopped["termination_reason"]), ("completed", "user_stop"))

    def test_activity_command_validation(self):
        session_id = self.create()
        self.run_to(session_id, final="select_target")
        self.assertEqual(self.command(session_id, "set_activity").status_code, 400)
        self.assertEqual(self.command(session_id, "start", activity="table").status_code, 400)
        self.assertEqual(self.command(session_id, "set_activity", activity="nap").status_code, 422)
        body = self.command(session_id, "set_activity", activity="movement").json()
        self.assertEqual(body["activity"], "movement")

    def test_rejects_bad_input_and_unknown_sessions(self):
        self.assertEqual(self.client.post("/api/live/sessions", json={"scenario": "nope"},
                                          headers=ORIGIN).status_code, 400)
        self.assertEqual(self.client.post("/api/live/sessions", json={"scenario": "table-routine",
                                                                      "extra": 1}, headers=ORIGIN).status_code, 422)
        self.assertEqual(self.client.get("/api/live/sessions/missing").status_code, 404)
        self.assertEqual(self.command("missing", "open").status_code, 404)

    def test_cross_origin_writes_and_foreign_hosts_are_rejected(self):
        response = self.client.post("/api/live/sessions", json={"scenario": "table-routine"},
                                    headers={"origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/api/live/health", headers={"host": "evil.example"})
        self.assertEqual(response.status_code, 400)

    def test_discard_ends_and_frees_capacity(self):
        first = self.create()
        self.create()
        response = self.client.delete(f"/api/live/sessions/{first}", headers=ORIGIN)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get(f"/api/live/sessions/{first}").status_code, 404)
        self.create()  # capacity was released
        self.assertEqual(self.client.delete("/api/live/sessions/missing", headers=ORIGIN).status_code, 404)

    def test_discard_rejects_cross_origin(self):
        session_id = self.create()
        response = self.client.delete(f"/api/live/sessions/{session_id}", headers={"origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_capacity_limit(self):
        self.create()
        self.create()
        response = self.client.post("/api/live/sessions", json={"scenario": "table-routine"}, headers=ORIGIN)
        self.assertEqual(response.status_code, 429)

    def test_finished_sessions_are_evicted_when_capacity_is_needed(self):
        first = self.create()
        self.run_to(first)
        self.command(first, "stop")
        self.create()
        self.create()
        self.assertEqual(self.client.get(f"/api/live/sessions/{first}").status_code, 404)

    def test_websocket_streams_backlog_and_resumes_by_sequence(self):
        session_id = self.create()
        self.run_to(session_id)
        path = f"ws://127.0.0.1:8767/api/live/sessions/{session_id}/events"
        with self.client.websocket_connect(path, headers=ORIGIN) as ws:
            ready = ws.receive_json()
            self.assertEqual(ready["event_type"], "stream_ready")
            events = []
            # The background worker processes the frame due at t=0 shortly after start.
            while not any(e["event_type"] == "observation" for e in events):
                events.append(ws.receive_json())
        sequences = [event["sequence"] for event in events]
        self.assertEqual(sequences, list(range(1, len(events) + 1)))
        states = [e["state"] for e in events if e["event_type"] == "session_state"]
        self.assertEqual(states, ["created", "previewing", "target_selected", "running"])
        self.assertTrue(any(e["event_type"] == "observation" for e in events))
        self.command(session_id, "pause")
        with self.client.websocket_connect(f"{path}?after={sequences[-1]}", headers=ORIGIN) as ws:
            ws.receive_json()
            resumed = [ws.receive_json()]
            while resumed[-1]["event_type"] != "session_state":
                resumed.append(ws.receive_json())
        self.assertEqual(resumed[0]["sequence"], sequences[-1] + 1)
        self.assertEqual([e["sequence"] for e in resumed],
                         list(range(sequences[-1] + 1, sequences[-1] + 1 + len(resumed))))
        self.assertEqual(resumed[-1]["state"], "paused")

    def test_websocket_reports_unknown_session_with_definitive_code(self):
        from starlette.websockets import WebSocketDisconnect
        with self.client.websocket_connect("ws://127.0.0.1:8767/api/live/sessions/missing/events",
                                           headers=ORIGIN) as ws:
            with self.assertRaises(WebSocketDisconnect) as closed:
                ws.receive_json()
        self.assertEqual(closed.exception.code, 4404)

    def test_websocket_rejects_foreign_origin(self):
        from starlette.websockets import WebSocketDisconnect
        session_id = self.create()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(f"ws://127.0.0.1:8767/api/live/sessions/{session_id}/events",
                                               headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()


class SubscriptionTests(unittest.TestCase):
    def test_overflow_forces_resume(self):
        from aba_demo.live.runtime import SessionManager, SubscriberOverflow

        async def scenario():
            manager = SessionManager(clock=FakeClock())
            runner = manager.create("table-routine")
            _, subscription = runner.subscribe()
            subscription._maxsize = 1
            subscription.offer({"sequence": 99})
            subscription.offer({"sequence": 100})
            with self.assertRaises(SubscriberOverflow):
                await subscription.next()
            await manager.shutdown()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
