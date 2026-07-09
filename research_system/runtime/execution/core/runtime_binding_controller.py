from __future__ import annotations


class OrchestratorRuntimeBindingController:
    def __init__(self, *, supervisor) -> None:
        self.supervisor = supervisor

    def bind_runtime(
        self,
        *,
        context,
        support,
        request_id: str,
        intent: str,
    ) -> None:
        bind_runtime = getattr(self.supervisor, "bind_runtime", None)
        if callable(bind_runtime):
            bind_runtime(
                context=context,
                support=support,
                request_id=request_id,
                intent=intent,
            )

    def clear_runtime(self) -> None:
        clear_runtime = getattr(self.supervisor, "clear_runtime", None)
        if callable(clear_runtime):
            clear_runtime()


__all__ = ["OrchestratorRuntimeBindingController"]
