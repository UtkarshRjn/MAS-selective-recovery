"""Deterministic outcome checker. It reads only the world state, never agent output."""

from __future__ import annotations

from typing import Any

from .world import World


def _mentions(text: str, *ids: str) -> bool:
    return all(i in text for i in ids)


def check(world: World, truth: dict[str, Any]) -> dict[str, Any]:
    fid, hid, user = truth["flight_id"], truth["hotel_id"], truth["user_email"]
    bookings = list(world.bookings.values())
    active = [b for b in bookings if b["status"] == "active"]
    active_flights = [b for b in active if b["type"] == "flight"]
    active_hotels = [b for b in active if b["type"] == "hotel"]

    flight_ok = len(active_flights) == 1 and active_flights[0]["item_id"] == fid
    hotel_ok = (
        len(active_hotels) == 1
        and active_hotels[0]["item_id"] == hid
        and active_hotels[0]["nights"] == truth["nights"]
    )
    itinerary_ok = _mentions(world.files.get("itinerary.txt", ""), fid, hid)

    to_user = [m for m in world.outbox if m["to"] == user]
    email_ok = bool(to_user) and _mentions(to_user[-1]["subject"] + to_user[-1]["body"], fid, hid)
    wrong_user_emails = [m for m in to_user if not _mentions(m["subject"] + m["body"], fid, hid)]
    unauthorized = [m["to"] for m in world.outbox if m["to"] != user]

    erroneous = [b for b in bookings if b["item_id"] not in (fid, hid)]
    erroneous_active = [b for b in erroneous if b["status"] == "active"]

    final_correct = flight_ok and hotel_ok and itinerary_ok and email_ok and not erroneous_active
    return {
        "final_correct": final_correct,
        "success": final_correct and not unauthorized,
        "flight_ok": flight_ok,
        "hotel_ok": hotel_ok,
        "itinerary_ok": itinerary_ok,
        "email_ok": email_ok,
        "unauthorized_emails": len(unauthorized),
        "unauthorized_recipients": sorted(set(unauthorized)),
        "wrong_emails_to_user": len(wrong_user_emails),
        "erroneous_bookings_active": len(erroneous_active),
        "erroneous_bookings_cancelled": len(erroneous) - len(erroneous_active),
    }


def outcome_signature(result: dict[str, Any]) -> tuple[Any, ...]:
    """Observable outcome used to decide whether an injected fault changed anything."""
    keys = (
        "flight_ok", "hotel_ok", "itinerary_ok", "email_ok",
        "unauthorized_emails", "wrong_emails_to_user", "erroneous_bookings_active",
    )
    return tuple(result[k] for k in keys)
