"""
generate_testdb_mysql.py — создаёт и непрерывно наполняет тестовую MySQL-БД.

Схема идентична generate_testdb.py (интернет-магазин), адаптирована под MySQL:
  • AUTO_INCREMENT вместо AUTOINCREMENT
  • DATETIME/DECIMAL вместо TEXT/REAL
  • явные FOREIGN KEY … REFERENCES
  • нет PRAGMA (MySQL не поддерживает)

Использование:
    uv run python generate_testdb_mysql.py [mysql://user:pass@host[:port]/db] [--interval 2.0]

Примеры:
    uv run python generate_testdb_mysql.py mysql://root:secret@localhost/testshop
    uv run python generate_testdb_mysql.py mysql://root:@localhost/testshop --interval 0.5
"""

import argparse
import random
import secrets
import sys
import time
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# Данные для генерации
# ─────────────────────────────────────────────────────────────────────────────

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
    ("Laptop Pro 15",    "Electronics",  1299.99),
    ("Wireless Mouse",   "Electronics",    29.99),
    ("USB-C Hub",        "Electronics",    49.99),
    ("Python Cookbook",  "Books",          39.99),
    ("Clean Code",       "Books",          34.99),
    ("Running Shoes",    "Sports",         89.99),
    ("Yoga Mat",         "Sports",         25.99),
    ("Coffee Maker",     "Home & Garden",  79.99),
    ("Desk Lamp",        "Home & Garden",  39.99),
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


def rnd(lst):       return random.choice(lst)
def rnd_int(a, b):  return random.randint(a, b)
def now_dt():       return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def future_date(days=1): return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_STATEMENTS = [
    # справочник категорий
    """
    CREATE TABLE IF NOT EXISTS category (
        id   INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL UNIQUE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # товары; product.category_id → category.id (явный FK)
    """
    CREATE TABLE IF NOT EXISTS product (
        id          INT            NOT NULL AUTO_INCREMENT PRIMARY KEY,
        name        VARCHAR(200)   NOT NULL UNIQUE,
        category_id INT            NOT NULL,
        price       DECIMAL(10,2)  NOT NULL,
        CONSTRAINT fk_product_category FOREIGN KEY (category_id) REFERENCES category(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # ОСНОВНАЯ таблица
    """
    CREATE TABLE IF NOT EXISTS customer (
        id         INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
        first_name VARCHAR(100) NOT NULL,
        last_name  VARCHAR(100) NOT NULL,
        email      VARCHAR(200) NOT NULL UNIQUE,
        phone      VARCHAR(50),
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # адреса; два FK на customer (shipping + billing)
    """
    CREATE TABLE IF NOT EXISTS address (
        id                  INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
        customer_id         INT          NOT NULL,
        billing_customer_id INT          NOT NULL,
        city                VARCHAR(100) NOT NULL,
        street              VARCHAR(200) NOT NULL,
        zip                 VARCHAR(20)  NOT NULL,
        is_default          TINYINT(1)   NOT NULL DEFAULT 0,
        CONSTRAINT fk_address_customer         FOREIGN KEY (customer_id)         REFERENCES customer(id) ON DELETE CASCADE,
        CONSTRAINT fk_address_billing_customer FOREIGN KEY (billing_customer_id) REFERENCES customer(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # заказы; customer_id → customer.id (явный FK)
    # "order" — зарезервированное слово в MySQL, используем `order`
    """
    CREATE TABLE IF NOT EXISTS `order` (
        id          INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
        customer_id INT           NOT NULL,
        status      VARCHAR(50)   NOT NULL DEFAULT 'pending',
        total       DECIMAL(10,2) NOT NULL DEFAULT 0,
        created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_order_customer FOREIGN KEY (customer_id) REFERENCES customer(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # сессии; customer_id без FK (намеренно)
    """
    CREATE TABLE IF NOT EXISTS session (
        id          INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
        customer_id INT          NOT NULL,
        token       VARCHAR(64)  NOT NULL UNIQUE,
        browser     VARCHAR(100),
        started_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ended_at    DATETIME
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # тикеты поддержки; customer_id без FK (намеренно)
    """
    CREATE TABLE IF NOT EXISTS support_ticket (
        id          INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
        customer_id INT          NOT NULL,
        subject     VARCHAR(300) NOT NULL,
        status      VARCHAR(50)  NOT NULL DEFAULT 'open',
        created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # позиции заказа; → order.id, → product.id (явные FK)
    """
    CREATE TABLE IF NOT EXISTS order_item (
        id         INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
        order_id   INT           NOT NULL,
        product_id INT           NOT NULL,
        qty        INT           NOT NULL DEFAULT 1,
        unit_price DECIMAL(10,2) NOT NULL,
        CONSTRAINT fk_order_item_order   FOREIGN KEY (order_id)   REFERENCES `order`(id) ON DELETE CASCADE,
        CONSTRAINT fk_order_item_product FOREIGN KEY (product_id) REFERENCES product(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # заметки к заказу; → order.id (явный FK)
    """
    CREATE TABLE IF NOT EXISTS order_note (
        id         INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
        order_id   INT          NOT NULL,
        text       TEXT         NOT NULL,
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_order_note_order FOREIGN KEY (order_id) REFERENCES `order`(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # отгрузка; → order.id, → address.id (явные FK)
    """
    CREATE TABLE IF NOT EXISTS shipment (
        id             INT         NOT NULL AUTO_INCREMENT PRIMARY KEY,
        order_id       INT         NOT NULL,
        address_id     INT         NOT NULL,
        carrier        VARCHAR(50) NOT NULL,
        tracking_code  VARCHAR(100),
        estimated_date DATE,
        shipped_at     DATETIME,
        CONSTRAINT fk_shipment_order   FOREIGN KEY (order_id)   REFERENCES `order`(id),
        CONSTRAINT fk_shipment_address FOREIGN KEY (address_id) REFERENCES address(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,

    # сообщения тикета; ticket_id без FK (намеренно)
    """
    CREATE TABLE IF NOT EXISTS ticket_message (
        id        INT         NOT NULL AUTO_INCREMENT PRIMARY KEY,
        ticket_id INT         NOT NULL,
        sender    VARCHAR(50) NOT NULL DEFAULT 'agent',
        text      TEXT        NOT NULL,
        sent_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


# ─────────────────────────────────────────────────────────────────────────────
# Подключение / инициализация
# ─────────────────────────────────────────────────────────────────────────────

def open_conn(dsn: str):
    """Открыть PyMySQL-соединение по DSN ``mysql://user:pass@host[:port]/db``."""
    try:
        import pymysql
    except ImportError:
        sys.exit("pymysql не установлен. Выполните: pip install pymysql")

    rest = dsn[len("mysql://"):]
    userinfo, hostpart = (rest.rsplit("@", 1) if "@" in rest else ("", rest))
    user, password = (userinfo.split(":", 1) if ":" in userinfo else (userinfo, ""))
    hostport, database = (hostpart.split("/", 1) if "/" in hostpart else (hostpart, ""))
    host, port = "127.0.0.1", 3306
    if ":" in hostport:
        host, port_s = hostport.rsplit(":", 1)
        port = int(port_s)
    elif hostport:
        host = hostport

    return pymysql.connect(
        host=host, port=port, user=user, password=password,
        database=database, charset="utf8mb4",
        autocommit=False,
    )


def init_db(conn) -> None:
    cur = conn.cursor()
    for stmt in SCHEMA_STATEMENTS:
        cur.execute(stmt)
    conn.commit()
    _seed_categories(conn)
    _seed_products(conn)


def _seed_categories(conn) -> None:
    cur = conn.cursor()
    for name in CATEGORIES:
        cur.execute(
            "INSERT INTO category (name) VALUES (%s) ON DUPLICATE KEY UPDATE name=name",
            (name,),
        )
    conn.commit()


def _seed_products(conn) -> None:
    cur = conn.cursor()
    for name, cat_name, price in PRODUCTS:
        cur.execute("SELECT id FROM category WHERE name=%s", (cat_name,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "INSERT INTO product (name, category_id, price) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE price=%s",
                (name, row[0], price, price),
            )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Генераторы записей
# ─────────────────────────────────────────────────────────────────────────────

def _unique_email(conn, first: str, last: str) -> str:
    cur = conn.cursor()
    base = f"{first.lower()}.{last.lower()}"
    for _ in range(100):
        email = f"{base}{rnd_int(1, 9999)}@{rnd(DOMAINS)}"
        cur.execute("SELECT 1 FROM customer WHERE email=%s", (email,))
        if not cur.fetchone():
            return email
    return f"{base}{time.time_ns()}@example.com"


def insert_customer(conn) -> int:
    first, last = rnd(FIRST_NAMES), rnd(LAST_NAMES)
    email = _unique_email(conn, first, last)
    phone = f"+{rnd_int(1,99)}{rnd_int(1000000000, 9999999999)}"
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO customer (first_name, last_name, email, phone) VALUES (%s,%s,%s,%s)",
        (first, last, email, phone),
    )
    conn.commit()
    cid = cur.lastrowid
    print(f"  [customer] +{cid}  {first} {last} <{email}>")
    return cid


def insert_address(conn, customer_id: int) -> int | None:
    cur = conn.cursor()
    cur.execute("SELECT id FROM customer ORDER BY RAND() LIMIT 1")
    row = cur.fetchone()
    if not row:
        return None
    billing_id = row[0]
    cur.execute(
        "INSERT INTO address (customer_id, billing_customer_id, city, street, zip, is_default) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (customer_id, billing_id,
         rnd(CITIES), f"{rnd_int(1,999)} {rnd(STREETS)}",
         str(rnd_int(10000, 99999)), rnd_int(0, 1)),
    )
    conn.commit()
    aid = cur.lastrowid
    print(f"  [address]  +{aid}  customer={customer_id}, billing={billing_id}")
    return aid


def insert_order(conn, customer_id: int) -> int | None:
    cur = conn.cursor()
    cur.execute("SELECT id, price FROM product")
    products = cur.fetchall()
    if not products:
        return None

    cur.execute(
        "INSERT INTO `order` (customer_id, status) VALUES (%s,%s)",
        (customer_id, rnd(ORDER_STATUSES)),
    )
    order_id = cur.lastrowid

    total = 0.0
    for _ in range(rnd_int(1, 5)):
        pid, price = rnd(products)
        qty = rnd_int(1, 3)
        cur.execute(
            "INSERT INTO order_item (order_id, product_id, qty, unit_price) VALUES (%s,%s,%s,%s)",
            (order_id, pid, qty, float(price)),
        )
        total += float(price) * qty

    cur.execute("UPDATE `order` SET total=%s WHERE id=%s", (round(total, 2), order_id))
    conn.commit()
    print(f"  [order]    +{order_id}  customer={customer_id}  total={total:.2f}")
    return order_id


def insert_session(conn, customer_id: int) -> int:
    token = secrets.token_hex(16)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO session (customer_id, token, browser, started_at) VALUES (%s,%s,%s,%s)",
        (customer_id, token, rnd(BROWSERS), now_dt()),
    )
    conn.commit()
    sid = cur.lastrowid
    print(f"  [session]  +{sid}  customer={customer_id} (no FK)")
    return sid


def insert_support_ticket(conn, customer_id: int) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO support_ticket (customer_id, subject, status) VALUES (%s,%s,%s)",
        (customer_id, rnd(TICKET_SUBJECTS), rnd(TICKET_STATUSES)),
    )
    ticket_id = cur.lastrowid
    for _ in range(rnd_int(1, 3)):
        cur.execute(
            "INSERT INTO ticket_message (ticket_id, sender, text, sent_at) VALUES (%s,%s,%s,%s)",
            (ticket_id, rnd(["agent", "customer"]), rnd(MSG_TEXTS), now_dt()),
        )
    conn.commit()
    print(f"  [ticket]   +{ticket_id}  customer={customer_id} (no FK)  +messages")
    return ticket_id


def insert_order_note(conn, order_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO order_note (order_id, text) VALUES (%s,%s)",
        (order_id, rnd(NOTE_TEXTS)),
    )
    conn.commit()
    print(f"  [note]     order={order_id}")


def insert_shipment(conn, order_id: int) -> None:
    cur = conn.cursor()
    cur.execute("SELECT id FROM address ORDER BY RAND() LIMIT 1")
    row = cur.fetchone()
    if not row:
        return
    tracking = f"{rnd(CARRIER_NAMES[:3])}{rnd_int(100000000, 999999999)}"
    cur.execute(
        "INSERT INTO shipment (order_id, address_id, carrier, tracking_code, "
        "estimated_date, shipped_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (order_id, row[0], rnd(CARRIER_NAMES), tracking,
         future_date(rnd_int(2, 10)), now_dt()),
    )
    conn.commit()
    print(f"  [shipment] order={order_id}  addr={row[0]}")


# ─────────────────────────────────────────────────────────────────────────────
# Основной цикл
# ─────────────────────────────────────────────────────────────────────────────

def get_random_existing(conn, table: str) -> int | None:
    cur = conn.cursor()
    cur.execute(f"SELECT id FROM `{table}` ORDER BY RAND() LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


def tick(conn) -> None:
    """Один «тик» — несколько случайных вставок."""
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
        oid = insert_order(conn, cid)
        if random.random() < 0.4:
            insert_address(conn, cid)
        if random.random() < 0.5:
            insert_session(conn, cid)
        if oid and random.random() < 0.6:
            insert_shipment(conn, oid)

    elif action == "ticket":
        cid = get_random_existing(conn, "customer")
        if cid is None:
            cid = insert_customer(conn)
        insert_support_ticket(conn, cid)

    elif action == "note_ship":
        oid = get_random_existing(conn, "order")
        if oid:
            if random.random() < 0.6:
                insert_order_note(conn, oid)
            else:
                insert_shipment(conn, oid)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a test MySQL database")
    parser.add_argument(
        "dsn", nargs="?",
        default="mysql://root:@localhost/testshop",
        help="MySQL DSN (default: mysql://root:@localhost/testshop)",
    )
    parser.add_argument(
        "--interval", type=float, default=1.5,
        help="Seconds between ticks (default: 1.5)",
    )
    args = parser.parse_args()

    if not args.dsn.startswith("mysql://"):
        parser.error("DSN must start with mysql://")

    print(f"DSN      : {args.dsn}")
    print(f"Interval : {args.interval}s")
    print("Press Ctrl+C to stop.\n")

    conn = open_conn(args.dsn)
    init_db(conn)

    tick_n = 0
    try:
        while True:
            tick_n += 1
            print(f"── tick {tick_n}  {now_dt()} ──")
            tick(conn)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
