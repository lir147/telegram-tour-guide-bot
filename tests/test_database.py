import tempfile
import unittest
from pathlib import Path
import bot

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = bot.DB
        bot.DB = str(Path(self.temp.name) / "test.db")
        bot.init()

    def tearDown(self):
        bot.DB = self.old
        self.temp.cleanup()

    def test_schema_upgrade_is_repeatable(self):
        bot.init()
        self.assertIn("price", [r[1] for r in bot.query("PRAGMA table_info(tours)")])
        self.assertIn("lat", [r[1] for r in bot.query("PRAGMA table_info(stops)")])

    def test_default_price(self):
        i = bot.query("INSERT INTO tours(title) VALUES (?)", ("Tour",), True)
        self.assertEqual(bot.query("SELECT price FROM tours WHERE id=?", (i,)), [(0,)])

    def test_payment_idempotency(self):
        for _ in range(2):
            bot.query("INSERT OR IGNORE INTO purchases VALUES (?,?,?,?)", ("charge", 1, 2, 10), True)
        self.assertEqual(bot.query("SELECT SUM(amount) FROM purchases"), [(10,)])

if __name__ == "__main__":
    unittest.main()
