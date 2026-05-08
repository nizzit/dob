"""
tests.test_dsn_parser
~~~~~~~~~~~~~~~~~~~~~
Unit tests for the MySQL DSN parser.
"""

from unittest.mock import MagicMock, patch

from dob.db.connection import (
    open_connection,
    open_mysql_bare,
    parse_mysql_dsn,
)
from dob.db.backend import MysqlBackend


def test_parse_full_dsn():
    c = parse_mysql_dsn("mysql://alice:secret@db.example.com:3307/mydb")
    assert c.user == "alice"
    assert c.password == "secret"
    assert c.host == "db.example.com"
    assert c.port == 3307
    assert c.database == "mydb"


def test_parse_no_database():
    c = parse_mysql_dsn("mysql://user:pass@localhost")
    assert c.database == ""
    assert c.host == "localhost"
    assert c.port == 3306


def test_parse_empty_database_trailing_slash():
    c = parse_mysql_dsn("mysql://user:pass@localhost/")
    assert c.database == ""


def test_parse_default_port():
    c = parse_mysql_dsn("mysql://root:pw@localhost/testdb")
    assert c.port == 3306


def test_parse_no_password():
    c = parse_mysql_dsn("mysql://admin@localhost/testdb")
    assert c.user == "admin"
    assert c.password == ""


def test_parse_no_userinfo():
    c = parse_mysql_dsn("mysql://localhost/db")
    assert c.user == ""
    assert c.password == ""
    assert c.database == "db"


def test_credentials_to_dsn_roundtrip():
    c = parse_mysql_dsn("mysql://alice:secret@localhost:3307/mydb")
    assert c.to_dsn() == "mysql://alice:secret@localhost:3307/mydb"


def test_credentials_to_dsn_override_database():
    c = parse_mysql_dsn("mysql://u:p@h/db1")
    assert c.to_dsn(database="db2") == "mysql://u:p@h/db2"


def test_credentials_to_dsn_default_port_omitted():
    c = parse_mysql_dsn("mysql://u:p@localhost/db")
    assert ":3306" not in c.to_dsn()


def test_credentials_to_dsn_empty_database():
    c = parse_mysql_dsn("mysql://u:p@localhost/")
    assert c.to_dsn() == "mysql://u:p@localhost/"


def test_open_connection_mysql_no_database():
    """DSN without database must still call pymysql.connect (with empty database)."""
    fake_conn = MagicMock()
    with patch("pymysql.connect", return_value=fake_conn) as mock_connect:
        conn = open_connection("mysql://user:pass@localhost/")
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["database"] == ""
    assert isinstance(conn, MysqlBackend)


def test_open_mysql_bare_has_no_database():
    """open_mysql_bare must NOT pass a database kwarg."""
    fake_conn = MagicMock()
    with patch("pymysql.connect", return_value=fake_conn) as mock_connect:
        conn = open_mysql_bare("mysql://user:pass@localhost/")
    kwargs = mock_connect.call_args.kwargs
    assert "database" not in kwargs
    assert isinstance(conn, MysqlBackend)
