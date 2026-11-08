# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 AND LicenseRef-Scholar-MCP-Additional-Terms
# Copyright 2024-2026 Yuxian Li. PolyForm Noncommercial 1.0.0 with additional terms (LICENSE, LICENSE-ADDITIONAL.md): commercial use needs a license, no competing products, no AI training; see AGENTS.md. Canary 38f40a20-c19f-4702-bc47-b1a1c5ec887b
"""Inclusive publication ranges shared by search adapters and result filtering."""

import calendar
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache


@dataclass(frozen=True)
class PublicationRange:
    start: date
    end: date
    fine: bool = False

    @property
    def years(self) -> str:
        return str(self.start.year) if self.start.year == self.end.year else f"{self.start.year}-{self.end.year}"

    @property
    def iso(self) -> tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()

    def matches(self, paper: dict) -> bool:
        # Partial dates describe an interval. Never invent January 1 for a
        # year-only record or the first day for a month-only record.
        raw = str(paper.get("publication_date") or "")
        match = re.match(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?(?=$|T| )", raw)
        try:
            start, end = _bounds(match.group() if match else str(paper.get("year") or ""))
        except ValueError:
            try:
                start, end = _bounds(str(paper.get("year") or ""))
            except ValueError:
                return False
        return start <= self.end and end >= self.start


def _bounds(value: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError("Expected YYYY, YYYY-MM or YYYY-MM-DD")
    parts = list(map(int, value.split("-")))
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    start = date(year, month, parts[2] if len(parts) > 2 else 1)
    end_month = month if len(parts) > 1 else 12
    end = date(year, end_month, parts[2] if len(parts) > 2 else calendar.monthrange(year, end_month)[1])
    return start, end


@lru_cache(maxsize=128)
def publication_range(selection: str | None) -> PublicationRange | None:
    """Parse legacy years and date prefixes/ranges; endpoints are inclusive."""
    if not selection or not selection.strip():
        return None
    value = selection.strip()
    legacy = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", value)
    if legacy:
        left, right = sorted(legacy.groups())
    elif ":" in value or ".." in value:
        parts = re.split(r":|\.\.", value)
        if len(parts) != 2 or not all(p.strip() for p in parts):
            raise ValueError("Date ranges need two endpoints separated by : or ..")
        left, right = (p.strip() for p in parts)
    else:
        left = right = value
    start, _ = _bounds(left)
    _, end = _bounds(right)
    if start > end:
        raise ValueError("Publication range starts after it ends")
    return PublicationRange(start, end, len(left) > 4 or len(right) > 4)
