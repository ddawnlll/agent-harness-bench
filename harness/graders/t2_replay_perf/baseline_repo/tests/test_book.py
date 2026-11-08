"""Unit tests for the price-level order book."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from replay.book import Book, Order  # noqa: E402


def order(order_id, side, price, qty, account="acct00", symbol="AAA"):
    return Order(order_id, account, symbol, side, price, qty)


class BookTests(unittest.TestCase):
    def test_empty_book(self):
        book = Book("AAA")
        self.assertIsNone(book.best_bid())
        self.assertIsNone(book.best_ask())
        self.assertEqual(book.resting_count(), 0)
        self.assertEqual(book.open_qty(), 0)
        self.assertEqual(book.checksum(), 0)
        self.assertFalse(book.is_crossed())

    def test_best_prices(self):
        book = Book("AAA")
        for oid, side, price, qty in ((1, "buy", 100, 10),
                                      (2, "buy", 105, 10),
                                      (3, "buy", 102, 10),
                                      (4, "sell", 110, 10),
                                      (5, "sell", 108, 10),
                                      (6, "sell", 112, 10)):
            book.rest(order(oid, side, price, qty))
        self.assertEqual(book.best_bid(), 105)
        self.assertEqual(book.best_ask(), 108)
        self.assertFalse(book.is_crossed())

    def test_checksum_and_open_qty(self):
        book = Book("AAA")
        book.rest(order(1, "buy", 100, 10))
        book.rest(order(2, "buy", 100, 5))
        book.rest(order(3, "sell", 110, 7))
        self.assertEqual(book.open_qty(), 22)
        self.assertEqual(book.checksum(), 100 * 15 + 110 * 7)
        self.assertEqual(book.resting_count(), 3)

    def test_level_orders_are_fifo(self):
        book = Book("AAA")
        book.rest(order(1, "buy", 100, 10))
        book.rest(order(2, "buy", 100, 10))
        book.rest(order(3, "buy", 100, 10))
        level = book.level_orders("buy", 100)
        self.assertEqual([o.id for o in level], [1, 2, 3])
        self.assertEqual(book.level_orders("sell", 100), [])

    def test_find(self):
        book = Book("AAA")
        book.rest(order(1, "buy", 100, 10))
        book.rest(order(2, "buy", 101, 10))
        self.assertEqual(book.find(2).price, 101)
        self.assertIsNone(book.find(3))

    def test_remove(self):
        book = Book("AAA")
        first = order(1, "buy", 100, 10)
        second = order(2, "buy", 100, 4)
        book.rest(first)
        book.rest(second)
        self.assertTrue(book.remove(first))
        self.assertFalse(book.remove(first))
        self.assertIsNone(book.find(1))
        self.assertEqual(book.resting_count(), 1)
        self.assertEqual(book.checksum(), 100 * 4)
        self.assertEqual(book.open_qty(), 4)
        self.assertEqual([o.id for o in book.level_orders("buy", 100)], [2])

    def test_remove_last_order_clears_level(self):
        book = Book("AAA")
        only = order(1, "sell", 120, 3)
        book.rest(only)
        book.remove(only)
        self.assertIsNone(book.best_ask())
        self.assertEqual(book.level_orders("sell", 120), [])

    def test_order_validation(self):
        with self.assertRaises(ValueError):
            Order(1, "a", "AAA", "hold", 100, 1)
        with self.assertRaises(ValueError):
            Order(1, "a", "AAA", "buy", 0, 1)
        with self.assertRaises(ValueError):
            Order(1, "a", "AAA", "buy", 100, 0)

    def test_crossed_book_detection(self):
        book = Book("AAA")
        book.rest(order(1, "buy", 105, 10))
        book.rest(order(2, "sell", 103, 10))
        self.assertTrue(book.is_crossed())


if __name__ == "__main__":
    unittest.main()
