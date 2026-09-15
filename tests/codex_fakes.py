"""Offline SDK double with an owned stream and observable cancellation."""
import asyncio
from types import SimpleNamespace


class Payload:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **_kwargs):
        return self.value


def sdk_factory(state):
    class Handle:
        id = "turn-offline"

        async def interrupt(self):
            state["interrupted"] = True

        async def stream(self):
            try:
                for request in state.get("tool_requests", []):
                    state.setdefault("tool_responses", []).append(state["handler"].handle("item/tool/call", request))
                while state.get("wait") and not state.get("closed") and not state.get("interrupted"):
                    await asyncio.sleep(0.005)
                if state.get("raise"):
                    raise state["raise"]
                for method, payload in state.get("events", [
                    ("item/completed", {"item": {"type": "agentMessage", "phase": "final_answer", "text": '{"value": 1}'}}),
                    ("turn/completed", {"turn": {"status": "completed"}}),
                ]):
                    yield SimpleNamespace(method=method, payload=Payload(payload))
                while state.get("wait_after_events") and not state.get("closed") and not state.get("interrupted"):
                    await asyncio.sleep(0.005)
            finally:
                state["stream_exited"] = True

    class Thread:
        id = "thread-offline"

        async def turn(self, prompt, **kwargs):
            state["turn"] = (prompt, kwargs)
            return Handle()

    class SDK:
        def __init__(self, config):
            state["config"] = config
            self._client = SimpleNamespace(_sync=SimpleNamespace(_approval_handler=None))

        async def __aenter__(self):
            state["started"] = True
            return self

        async def account(self):
            return SimpleNamespace(account=SimpleNamespace(root=SimpleNamespace(type=state.get("auth", "chatgpt"))))

        async def thread_start(self, **kwargs):
            state["thread"] = kwargs
            return Thread()

        async def close(self):
            state["closed"] = True
    return SDK
