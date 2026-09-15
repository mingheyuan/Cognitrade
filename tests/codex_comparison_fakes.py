"""Offline transport for native comparison fixtures; never creates a runtime."""
from contextlib import asynccontextmanager


def model_transport(factory):
    @asynccontextmanager
    async def transport():
        fixture = factory()

        class Adapter:
            async def execute_async(self, *, system_prompt, user_prompt, timeout_seconds, **_kwargs):
                response = await fixture.create(timeout=timeout_seconds,
                    instructions=system_prompt, input=user_prompt)
                return {"output_text": response.parse().output_text,
                        "metadata": {"status": "completed", "provider": "codex", "simulated": True}}

        yield Adapter()
    return transport
