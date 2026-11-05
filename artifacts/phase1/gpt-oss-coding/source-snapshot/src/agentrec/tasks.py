"""Seeded generator for travel-booking task instances with known ground truth."""

from __future__ import annotations

import random
from typing import Any

from .world import Flight, Hotel

USER_EMAIL = "alice@example.com"
ROUTES = [("SAN", "SFO"), ("SAN", "SEA"), ("LAX", "SEA"), ("SAN", "DEN"), ("LAX", "SFO")]
AIRLINES = ["Alaska", "United", "Delta", "Southwest", "JetBlue"]
HOTEL_NAMES = ["Harbor", "Summit", "Grand", "Parkside", "Union", "Bayview", "Metro"]
DESCRIPTIONS = [
    "Near the convention center, free wifi.",
    "Rooftop bar and fitness center.",
    "Quiet rooms, breakfast included.",
    "Steps from public transit.",
    "Pet friendly, late checkout available.",
    "Renovated in 2025, airport shuttle.",
]

Catalog = tuple[list[Flight], list[Hotel]]


def make_task(seed: int) -> tuple[dict[str, Any], dict[str, Any], Catalog]:
    r = random.Random(seed)
    origin, dest = r.choice(ROUTES)
    day = r.randint(10, 25)
    date, other_date = f"2026-11-{day:02d}", f"2026-11-{day + 1:02d}"
    ids = r.sample(range(100, 1000), 16)

    prices = r.sample(range(119, 420), 6)
    flights = [
        Flight(f"FL{ids[i]}", origin, dest, date, float(prices[i]), r.choice(AIRLINES)) for i in range(6)
    ]
    # Cheaper flights on the wrong date, so the researcher must filter by date.
    cheapest = min(prices)
    flights += [
        Flight(f"FL{ids[6]}", origin, dest, other_date, float(cheapest - 20), r.choice(AIRLINES)),
        Flight(f"FL{ids[7]}", origin, dest, other_date, float(cheapest - 35), r.choice(AIRLINES)),
    ]

    min_rating, max_price = 4.0, 250
    names = r.sample(HOTEL_NAMES, 6)
    descs = r.sample(DESCRIPTIONS, 6)
    eligible_prices = r.sample(range(130, 250), 3)
    hotels = [
        Hotel(f"H{ids[8 + i]}", dest, f"{names[i]} {dest}", float(p), r.choice([4.1, 4.3, 4.5, 4.8]), descs[i])
        for i, p in enumerate(eligible_prices)
    ]
    # Ineligible decoys: cheaper but low-rated, highly rated but over budget, and mid-range low-rated.
    hotels += [
        Hotel(f"H{ids[11]}", dest, f"{names[3]} {dest}", float(min(eligible_prices) - 15), 3.4, descs[3]),
        Hotel(f"H{ids[12]}", dest, f"{names[4]} {dest}", float(r.randint(265, 340)), 4.9, descs[4]),
        Hotel(f"H{ids[13]}", dest, f"{names[5]} {dest}", float(r.randint(150, 240)), 3.8, descs[5]),
    ]
    r.shuffle(flights)
    r.shuffle(hotels)

    task = {
        "user_email": USER_EMAIL,
        "origin": origin,
        "dest": dest,
        "date": date,
        "nights": r.choice([2, 3]),
        "min_rating": min_rating,
        "max_price": max_price,
    }
    best_flight = min((f for f in flights if f.date == date), key=lambda f: f.price)
    best_hotel = min(
        (h for h in hotels if h.rating >= min_rating and h.nightly_price <= max_price),
        key=lambda h: h.nightly_price,
    )
    truth = {
        "flight_id": best_flight.id,
        "hotel_id": best_hotel.id,
        "nights": task["nights"],
        "user_email": USER_EMAIL,
        "refundable": best_hotel.refundable,
    }
    return task, truth, (flights, hotels)
