import unittest

import neumune_probe as n


class NeumuneProbeTests(unittest.TestCase):
    def test_choose_freshest(self):
        source, stock, update = n.choose_freshest({
            "YATA": (0, 100),
            "Prometheus": (3, 101),
        })
        self.assertEqual((source, stock, update), ("Prometheus", 3, 101))

    def test_equal_timestamp_prefers_yata(self):
        source, stock, update = n.choose_freshest({
            "Prometheus": (0, 100),
            "YATA": (3, 100),
        })
        self.assertEqual((source, stock, update), ("YATA", 3, 100))

    def test_classify(self):
        self.assertEqual(n.classify(None, 0), "baseline")
        self.assertEqual(n.classify({"stock": 0}, 3), "restock")
        self.assertEqual(n.classify({"stock": 3}, 0), "stockout")
        self.assertEqual(n.classify({"stock": 3}, 2), "quantity_change")
        self.assertIsNone(n.classify({"stock": 0}, 0))
        self.assertIsNone(n.classify({"stock": 3}, 3))

    def test_no_sources_raises(self):
        with self.assertRaises(RuntimeError):
            n.choose_freshest({})


if __name__ == "__main__":
    unittest.main()