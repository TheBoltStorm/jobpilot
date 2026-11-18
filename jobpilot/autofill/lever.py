"""Fill a Lever application form (jobs.lever.co/{site}/{id}/apply).

Same rules and guards as the Greenhouse filler (it's a subclass): type, pick, tick and attach
only. Lever's differences: fields are found by name, custom questions are real radio buttons,
and dropdowns are native <select> elements.
"""

from __future__ import annotations

from jobpilot.autofill.greenhouse import GreenhouseFiller


class LeverFiller(GreenhouseFiller):
    def _fill(self, loc, f: dict, kind: str, fid: str, val: str) -> bool:
        if kind == "select":
            loc.first.select_option(label=val)
            return True
        if kind == "location":
            # Lever suggests places as you type; typing is enough to show them. You pick the suggestion.
            loc.first.fill(val)
            return True
        return super()._fill(loc, f, kind, fid, val)
