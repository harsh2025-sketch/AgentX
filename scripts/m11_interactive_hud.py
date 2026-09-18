"""Manual interactive desktop acceptance for the M11 Tk HUD."""

from __future__ import annotations

from agentx.hud import HudCommandGateway, HudController, HudModel, TkHudApp


class _DemoController(HudController):
    def cancel(self, task_id: str | None) -> bool:
        del task_id
        return True

    def confirm(self, task_id: str | None, nonce: str | None) -> bool:
        del task_id, nonce
        return True

    def reject(self, task_id: str | None, nonce: str | None) -> bool:
        del task_id, nonce
        return True

    def retry(self, task_id: str | None) -> bool:
        del task_id
        return True

    def dismiss(self, task_id: str | None) -> bool:
        del task_id
        return True


def main() -> int:
    model = HudModel()
    gateway = HudCommandGateway(_DemoController())
    TkHudApp(model=model, gateway=gateway).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
