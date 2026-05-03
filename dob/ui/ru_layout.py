"""
dob.ui.ru_layout
~~~~~~~~~~~~~~~~
Russian ЙЦУКЕН → QWERTY layout remapping.

The map and the global hook are defined here.  The hook is installed once
on DobApp.on_key so no per-screen RuKeysMixin is needed.
"""

from __future__ import annotations

from textual import events

# Maps Russian ЙЦУКЕН keys to QWERTY equivalents
RU_TO_EN: dict[str, str] = {
    "й": "q", "ц": "w", "у": "e", "к": "r", "е": "t", "н": "y",
    "г": "u", "ш": "i", "щ": "o", "з": "p",
    "ф": "a", "ы": "s", "в": "d", "а": "f", "п": "g", "р": "h",
    "о": "j", "л": "k", "д": "l",
    "я": "z", "ч": "x", "с": "c", "м": "v", "и": "b", "т": "n", "ь": "m",
    ".": "/",
    ",": "/",
}


async def handle_ru_key(widget: "object", event: events.Key) -> None:
    """
    Re-fire a translated key event for widgets that have bound the English key.

    Call from DobApp.on_key (or any screen's on_key):
        await handle_ru_key(self, event)
    """
    en = RU_TO_EN.get(event.character or "")
    if not en:
        return
    for binding in getattr(widget, "BINDINGS", []):
        keys = [k.strip() for k in binding.key.split(",")]
        if en in keys:
            await widget.run_action(binding.action)  # type: ignore[attr-defined]
            event.stop()
            break
