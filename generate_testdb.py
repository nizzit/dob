"""
generate_testdb.py — создаёт и непрерывно наполняет тестовую БД.

Схема: интернет-магазин

  [ОСНОВНАЯ]
  customer          ← главная сущность

  [ВТОРИЧНЫЕ — явные FK на customer]
  order             → customer.id (FK явный)
  address           → customer.id (FK явный)
                    → customer.id ещё раз как billing_customer_id (два FK на одну таблицу!)

  [ВТОРИЧНЫЕ — "просто ID" без FK]
  session           → customer_id (без REFERENCES, просто INTEGER)
  support_ticket    → customer_id (без REFERENCES)

  [ТРЕТИЧНЫЕ — ссылаются на вторичные]
  order_item        → order.id (FK явный)
                    → product.id (FK явный)
  order_note        → order.id (FK явный)
  shipment          → order.id  (FK явный)
                    → address.id (FK явный)
  ticket_message    → support_ticket.id (без FK, просто INTEGER)

  [СПРАВОЧНИКИ]
  product           — отдельная таблица, FK нет от неё никуда
  category          — product.category_id → category.id (FK явный)

Скрипт работает бесконечно, вставляет записи каждые N секунд.
Можно остановить Ctrl+C.

Использование:
    uv run python generate_testdb.py [path/to/test.db] [--interval 2.0]
"""

import random
import sqlite3
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────
# Данные для генерации
# ─────────────────────────────────────────────

FIRST_NAMES = ["Alice", "Bob", "Carol", "Dave", "Eva", "Frank", "Grace", "Hank",
               "Iris", "Jack", "Kim", "Leo", "Mia", "Nick", "Olivia", "Pete"]
LAST_NAMES  = ["Smith", "Jones", "Brown", "Taylor", "Wilson", "Davies", "Evans",
               "Thomas", "Roberts", "Johnson", "White", "Martin", "Garcia", "Lee"]
DOMAINS     = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "icloud.com"]
CITIES      = ["Moscow", "Berlin", "Paris", "London", "New York", "Tokyo",
               "Sydney", "Toronto", "Madrid", "Rome"]
STREETS     = ["Main St", "Oak Ave", "Maple Rd", "Park Blvd", "Lake Dr",
               "Hill Ln", "River Ct", "Forest Way"]
CATEGORIES  = ["Electronics", "Books", "Clothing", "Food", "Sports",
               "Home & Garden", "Toys", "Beauty"]
PRODUCTS = [
    ("Laptop Pro 15",    "Electronics", 1299.99),
    ("Wireless Mouse",   "Electronics",   29.99),
    ("USB-C Hub",        "Electronics",   49.99),
    ("Python Cookbook",  "Books",          39.99),
    ("Clean Code",       "Books",          34.99),
    ("Running Shoes",    "Sports",         89.99),
    ("Yoga Mat",         "Sports",         25.99),
    ("Coffee Maker",     "Home & Garden", 79.99),
    ("Desk Lamp",        "Home & Garden", 39.99),
    ("T-Shirt Basic",    "Clothing",       19.99),
    ("Jeans Slim",       "Clothing",       59.99),
    ("Protein Powder",   "Food",           44.99),
    ("Vitamin C 1000",   "Food",           12.99),
    ("LEGO Set 500",     "Toys",           49.99),
    ("Face Cream SPF",   "Beauty",         22.99),
    ("Mechanical KB",    "Electronics",   129.99),
    ("Monitor 27\"",     "Electronics",   349.99),
    ("Headphones BT",    "Electronics",    79.99),
    ("Notebook A5",      "Books",           8.99),
    ("Water Bottle",     "Sports",         18.99),
]
ORDER_STATUSES  = ["pending", "confirmed", "shipped", "delivered", "cancelled", "refunded"]
TICKET_SUBJECTS = ["Order not received", "Wrong item sent", "Refund request",
                   "Account issue", "Payment failed", "Product damaged",
                   "Change delivery address", "Promo code not working"]
TICKET_STATUSES = ["open", "in_progress", "resolved", "closed"]
NOTE_TEXTS      = ["Called customer, no answer.", "Left voicemail.",
                   "Customer confirmed address.", "Escalated to manager.",
                   "Refund initiated.", "Replacement sent.",
                   "Waiting for customer reply.", "Issue resolved."]
MSG_TEXTS       = ["Thank you for contacting us.", "We are looking into it.",
                   "Could you provide more details?", "Your request has been processed.",
                   "Please allow 3-5 business days.", "Sorry for the inconvenience.",
                   "The issue has been escalated.", "Your case is now closed."]
CARRIER_NAMES   = ["DHL", "FedEx", "UPS", "USPS", "Royal Mail", "DPD", "GLS"]
BROWSERS        = ["Chrome/120", "Firefox/121", "Safari/17", "Edge/120"]


def rnd(lst): return random.choice(lst)
def rnd_int(a, b): return random.randint(a, b)
def now_str(): return datetime.now().isoformat(sep=" ", timespec="seconds")
def future_str(days=1): return (datetime.now() + timedelta(days=days)).date().isoformat()


# ─────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- справочник категорий
CREATE TABLE IF NOT EXISTS category (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- товары; product.category_id → category.id (явный FK)
CREATE TABLE IF NOT EXISTS product (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    category_id INTEGER NOT NULL REFERENCES category(id),
    price       REAL    NOT NULL
);

-- ОСНОВНАЯ таблица
CREATE TABLE IF NOT EXISTS customer (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT    NOT NULL,
    last_name  TEXT    NOT NULL,
    email      TEXT    NOT NULL UNIQUE,
    phone      TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- адреса; два FK на customer (shipping + billing)
CREATE TABLE IF NOT EXISTS address (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id         INTEGER NOT NULL REFERENCES customer(id) ON DELETE CASCADE,
    billing_customer_id INTEGER NOT NULL REFERENCES customer(id),   -- второй FK на ту же таблицу
    city                TEXT    NOT NULL,
    street              TEXT    NOT NULL,
    zip                 TEXT    NOT NULL,
    is_default          INTEGER NOT NULL DEFAULT 0
);

-- заказы; order.customer_id → customer.id (явный FK)
CREATE TABLE IF NOT EXISTS "order" (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customer(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL DEFAULT 'pending',
    total       REAL    NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- сессии; customer_id без FK (просто INTEGER)
CREATE TABLE IF NOT EXISTS session (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,           -- намеренно без REFERENCES
    token       TEXT    NOT NULL UNIQUE,
    browser     TEXT,
    started_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    ended_at    TEXT
);

-- тикеты поддержки; customer_id без FK
CREATE TABLE IF NOT EXISTS support_ticket (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,           -- намеренно без REFERENCES
    subject     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── ТРЕТИЧНЫЕ ────────────────────────────────────────────────────────────────

-- позиции заказа; → order.id (FK явный), → product.id (FK явный)
CREATE TABLE IF NOT EXISTS order_item (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES "order"(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES product(id),
    qty        INTEGER NOT NULL DEFAULT 1,
    unit_price REAL    NOT NULL
);

-- заметки к заказу; → order.id (FK явный)
CREATE TABLE IF NOT EXISTS order_note (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES "order"(id) ON DELETE CASCADE,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- отгрузка; → order.id (FK явный), → address.id (FK явный)
CREATE TABLE IF NOT EXISTS shipment (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       INTEGER NOT NULL REFERENCES "order"(id),
    address_id     INTEGER NOT NULL REFERENCES address(id),
    carrier        TEXT    NOT NULL,
    tracking_code  TEXT,
    estimated_date TEXT,
    shipped_at     TEXT
);

-- сообщения тикета; ticket_id без FK (просто INTEGER)
CREATE TABLE IF NOT EXISTS ticket_message (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,             -- намеренно без REFERENCES
    sender    TEXT    NOT NULL DEFAULT 'agent',
    text      TEXT    NOT NULL,
    sent_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


# ─────────────────────────────────────────────
# Инициализация / заполнение справочников
# ─────────────────────────────────────────────

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    _seed_categories(conn)
    _seed_products(conn)


def _seed_categories(conn: sqlite3.Connection) -> None:
    for name in CATEGORIES:
        conn.execute("INSERT OR IGNORE INTO category(name) VALUES(?)", (name,))
    conn.commit()


def _seed_products(conn: sqlite3.Connection) -> None:
    for name, cat_name, price in PRODUCTS:
        row = conn.execute("SELECT id FROM category WHERE name=?", (cat_name,)).fetchone()
        if row:
            conn.execute(
                "INSERT OR IGNORE INTO product(name, category_id, price) VALUES(?,?,?)",
                (name, row[0], price),
            )
    conn.commit()


# ─────────────────────────────────────────────
# Генераторы записей
# ─────────────────────────────────────────────

def _unique_email(conn: sqlite3.Connection, first: str, last: str) -> str:
    base = f"{first.lower()}.{last.lower()}"
    for _ in range(100):
        suffix = random.randint(1, 9999)
        email = f"{base}{suffix}@{rnd(DOMAINS)}"
        if not conn.execute("SELECT 1 FROM customer WHERE email=?", (email,)).fetchone():
            return email
    return f"{base}{time.time_ns()}@example.com"


def insert_customer(conn: sqlite3.Connection) -> int:
    first, last = rnd(FIRST_NAMES), rnd(LAST_NAMES)
    email = _unique_email(conn, first, last)
    cur = conn.execute(
        "INSERT INTO customer(first_name, last_name, email, phone) VALUES(?,?,?,?)",
        (first, last, email, f"+{rnd_int(1,99)}{rnd_int(1000000000, 9999999999)}"),
    )
    conn.commit()
    print(f"  [customer] +{cur.lastrowid}  {first} {last} <{email}>")
    return cur.lastrowid


def insert_address(conn: sqlite3.Connection, customer_id: int) -> int | None:
    # billing_customer_id может быть тем же или другим существующим customer
    other = conn.execute(
        "SELECT id FROM customer ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    if not other:
        return None
    billing_id = other[0]
    cur = conn.execute(
        """INSERT INTO address(customer_id, billing_customer_id, city, street, zip, is_default)
           VALUES(?,?,?,?,?,?)""",
        (customer_id, billing_id,
         rnd(CITIES), f"{rnd_int(1,999)} {rnd(STREETS)}",
         f"{rnd_int(10000, 99999)}", rnd_int(0, 1)),
    )
    conn.commit()
    print(f"  [address]  +{cur.lastrowid}  customer={customer_id}, billing={billing_id}")
    return cur.lastrowid


def insert_order(conn: sqlite3.Connection, customer_id: int) -> int | None:
    product_ids = [r[0] for r in conn.execute("SELECT id FROM product").fetchall()]
    if not product_ids:
        return None

    cur = conn.execute(
        "INSERT INTO \"order\"(customer_id, status) VALUES(?,?)",
        (customer_id, rnd(ORDER_STATUSES)),
    )
    order_id = cur.lastrowid

    # 1-5 позиций
    total = 0.0
    for _ in range(rnd_int(1, 5)):
        pid = rnd(product_ids)
        price = conn.execute("SELECT price FROM product WHERE id=?", (pid,)).fetchone()[0]
        qty = rnd_int(1, 3)
        conn.execute(
            "INSERT INTO order_item(order_id, product_id, qty, unit_price) VALUES(?,?,?,?)",
            (order_id, pid, qty, price),
        )
        total += price * qty

    conn.execute('UPDATE "order" SET total=? WHERE id=?', (round(total, 2), order_id))
    conn.commit()
    print(f"  [order]    +{order_id}  customer={customer_id}  total={total:.2f}")
    return order_id


def insert_session(conn: sqlite3.Connection, customer_id: int) -> int:
    import secrets
    token = secrets.token_hex(16)
    cur = conn.execute(
        "INSERT INTO session(customer_id, token, browser, started_at) VALUES(?,?,?,?)",
        (customer_id, token, rnd(BROWSERS), now_str()),
    )
    conn.commit()
    print(f"  [session]  +{cur.lastrowid}  customer={customer_id} (no FK)")
    return cur.lastrowid


def insert_support_ticket(conn: sqlite3.Connection, customer_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO support_ticket(customer_id, subject, status) VALUES(?,?,?)",
        (customer_id, rnd(TICKET_SUBJECTS), rnd(TICKET_STATUSES)),
    )
    ticket_id = cur.lastrowid
    # сразу пару сообщений
    for _ in range(rnd_int(1, 3)):
        conn.execute(
            "INSERT INTO ticket_message(ticket_id, sender, text, sent_at) VALUES(?,?,?,?)",
            (ticket_id, rnd(["agent", "customer"]), rnd(MSG_TEXTS), now_str()),
        )
    conn.commit()
    print(f"  [ticket]   +{ticket_id}  customer={customer_id} (no FK)  +messages")
    return ticket_id


def insert_order_note(conn: sqlite3.Connection, order_id: int) -> None:
    conn.execute(
        "INSERT INTO order_note(order_id, text) VALUES(?,?)",
        (order_id, rnd(NOTE_TEXTS)),
    )
    conn.commit()
    print(f"  [note]     order={order_id}")


def insert_shipment(conn: sqlite3.Connection, order_id: int) -> None:
    addr = conn.execute(
        "SELECT id FROM address ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    if not addr:
        return
    tracking = f"{rnd(CARRIER_NAMES[:3])}{rnd_int(100000000, 999999999)}"
    conn.execute(
        """INSERT INTO shipment(order_id, address_id, carrier, tracking_code,
                                estimated_date, shipped_at)
           VALUES(?,?,?,?,?,?)""",
        (order_id, addr[0], rnd(CARRIER_NAMES), tracking,
         future_str(rnd_int(2, 10)), now_str()),
    )
    conn.commit()
    print(f"  [shipment] order={order_id}  addr={addr[0]}")


# ─────────────────────────────────────────────
# Основной цикл
# ─────────────────────────────────────────────

def get_random_existing(conn: sqlite3.Connection, table: str, col: str = "id"):
    clean = table.strip('"')
    row = conn.execute(f'SELECT {col} FROM "{clean}" ORDER BY RANDOM() LIMIT 1').fetchone()
    return row[0] if row else None


def tick(conn: sqlite3.Connection) -> None:
    """Один «тик» — делаем несколько случайных вставок."""

    action = random.choices(
        ["new_customer", "activity", "ticket", "note_ship"],
        weights=[20, 50, 20, 10],
    )[0]

    if action == "new_customer":
        cid = insert_customer(conn)
        insert_address(conn, cid)
        insert_session(conn, cid)

    elif action == "activity":
        cid = get_random_existing(conn, "customer")
        if cid is None:
            cid = insert_customer(conn)
        # заказ
        oid = insert_order(conn, cid)
        # иногда тут же адрес или ещё сессия
        if random.random() < 0.4:
            insert_address(conn, cid)
        if random.random() < 0.5:
            insert_session(conn, cid)
        # отгрузка если есть адрес
        if oid and random.random() < 0.6:
            insert_shipment(conn, oid)

    elif action == "ticket":
        cid = get_random_existing(conn, "customer")
        if cid is None:
            cid = insert_customer(conn)
        insert_support_ticket(conn, cid)

    elif action == "note_ship":
        oid = get_random_existing(conn, 'order')
        if oid:
            if random.random() < 0.6:
                insert_order_note(conn, oid)
            else:
                insert_shipment(conn, oid)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a test SQLite database")
    parser.add_argument("db", nargs="?", default="test.db",
                        help="Path to the SQLite database file (default: test.db)")
    parser.add_argument("--interval", type=float, default=1.5,
                        help="Seconds between ticks (default: 1.5)")
    args = parser.parse_args()

    db_path = Path(args.db)
    print(f"Database : {db_path.resolve()}")
    print(f"Interval : {args.interval}s")
    print("Press Ctrl+C to stop.\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    init_db(conn)

    tick_n = 0
    try:
        while True:
            tick_n += 1
            print(f"── tick {tick_n}  {now_str()} ──")
            tick(conn)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
