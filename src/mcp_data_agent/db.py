"""Deterministic seed data for a synthetic telecom / e-commerce database.

The dataset is generated from a fixed seed (42) with the standard-library
``random`` module only, so every fresh seed produces exactly the same rows,
the same aggregates, and the same demo answers. No external data is used.

Tables
------
customers      1,200 rows  -- people who bought a plan
plans              6 rows  -- the catalogue being sold
subscriptions  1,800 rows  -- customer -> plan, with a status
invoices       4,200 rows  -- billing events for subscriptions
"""

from __future__ import annotations

import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "app.db"

FIRST_NAMES = [
    "Aisha", "Liam", "Mei", "Carlos", "Fatima", "Jonas", "Priya", "Tom",
    "Sara", "Kenji", "Olga", "Diego", "Nina", "Omar", "Lena", "Raj",
    "Emma", "Yusuf", "Ivy", "Marco", "Tara", "Noah", "Zoe", "Felix",
]
LAST_NAMES = [
    "Sharma", "Smith", "Chen", "Garcia", "Khan", "Muller", "Patel", "Brown",
    "Ali", "Tanaka", "Novak", "Rossi", "Kim", "Haddad", "Fischer", "Singh",
    "Wilson", "Ahmed", "Dubois", "Costa", "Rao", "Johnson", "Park", "Weber",
]
CITIES = [
    ("Austin", "US"), ("Toronto", "CA"), ("London", "GB"), ("Berlin", "DE"),
    ("Dubai", "AE"), ("Singapore", "SG"), ("Sydney", "AU"), ("Bangalore", "IN"),
    ("Amsterdam", "NL"), ("Auckland", "NZ"),
]
SEGMENTS = ["consumer", "consumer", "consumer", "small_business", "enterprise"]

# name, monthly price (USD), data allowance in GB (None stored as -1 = unlimited)
PLANS = [
    ("Basic", 15, 5),
    ("Starter", 25, 20),
    ("Plus", 40, 50),
    ("Pro", 60, 120),
    ("Unlimited", 85, -1),
    ("Enterprise", 150, -1),
]
# Pro and Unlimited are deliberately the popular plans.
PLAN_WEIGHTS = [8, 12, 16, 30, 24, 10]
SUBSCRIPTION_STATUSES = ["active", "active", "active", "active", "paused", "cancelled"]
INVOICE_STATUSES = ["paid", "paid", "paid", "paid", "paid", "pending", "overdue"]

SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    segment TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE plans (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    monthly_price_usd REAL NOT NULL,
    data_gb INTEGER NOT NULL
);
CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_usd REAL NOT NULL,
    status TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    paid_at TEXT
);
"""


def db_path_from_env() -> Path:
    raw = os.environ.get("MCP_DATA_AGENT_DB")
    return Path(raw) if raw else DEFAULT_DB_PATH


def seed_database(path: Path | str | None = None, *, overwrite: bool = False) -> Path:
    """Create and populate the database. Idempotent unless overwrite=True."""
    db_path = Path(path) if path else db_path_from_env()
    if db_path.exists() and not overwrite:
        return db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rng = random.Random(42)
    base = date(2023, 1, 1)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)

        plan_ids: list[int] = []
        for name, price, data_gb in PLANS:
            cur = conn.execute(
                "INSERT INTO plans (name, monthly_price_usd, data_gb) VALUES (?, ?, ?)",
                (name, price, data_gb),
            )
            plan_ids.append(int(cur.lastrowid))

        customer_ids: list[int] = []
        for i in range(1200):
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            city, country = rng.choice(CITIES)
            created = base + timedelta(days=rng.randrange(0, 700))
            cur = conn.execute(
                "INSERT INTO customers (name, email, city, country, segment, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"{first} {last}",
                    f"{first.lower()}.{last.lower()}.{i}@example.com",
                    city,
                    country,
                    rng.choice(SEGMENTS),
                    created.isoformat(),
                ),
            )
            customer_ids.append(int(cur.lastrowid))

        subscription_ids: list[int] = []
        sub_customer: list[int] = []
        sub_plan_price: list[float] = []
        for _ in range(1800):
            customer_id = rng.choice(customer_ids)
            plan_idx = rng.choices(range(len(plan_ids)), weights=PLAN_WEIGHTS, k=1)[0]
            status = rng.choice(SUBSCRIPTION_STATUSES)
            started = base + timedelta(days=rng.randrange(0, 650))
            ended = None
            if status == "cancelled":
                ended = (started + timedelta(days=rng.randrange(30, 400))).isoformat()
            cur = conn.execute(
                "INSERT INTO subscriptions (customer_id, plan_id, status, started_at, ended_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (customer_id, plan_ids[plan_idx], status, started.isoformat(), ended),
            )
            subscription_ids.append(int(cur.lastrowid))
            sub_customer.append(customer_id)
            sub_plan_price.append(PLANS[plan_idx][1])

        for j in range(4200):
            idx = rng.randrange(len(subscription_ids))
            status = rng.choice(INVOICE_STATUSES)
            issued = base + timedelta(days=rng.randrange(0, 720))
            # Small variation around the plan price (taxes, add-ons, discounts).
            amount = round(sub_plan_price[idx] * rng.uniform(0.9, 1.15), 2)
            paid = (issued + timedelta(days=rng.randrange(0, 14))).isoformat() if status == "paid" else None
            conn.execute(
                "INSERT INTO invoices (subscription_id, customer_id, amount_usd, status, issued_at, paid_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (subscription_ids[idx], sub_customer[idx], amount, status, issued.isoformat(), paid),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def ensure_database(path: Path | str | None = None) -> Path:
    return seed_database(path)


if __name__ == "__main__":
    target = seed_database(overwrite=True)
    print(f"Seeded {target}")
