import csv
import tempfile
import unittest

from reviews.main_reviews import load_places


class LoadPlacesTestCase(unittest.TestCase):
    def test_uses_place_name_column_as_fallback(self) -> None:
        with tempfile.NamedTemporaryFile("w+", newline="", suffix=".csv") as tmp:
            fieldnames = [
                "Beach ID",
                "Place ID",
                "Place Name",
                "Polygon",
                "Place URL",
                "Category",
                "Categories",
            ]
            writer = csv.DictWriter(tmp, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "Beach ID": "b1",
                    "Place ID": "pid-123",
                    "Place Name": "My Test Place",
                    "Polygon": "poly",
                    "Place URL": "https://example.test",
                    "Category": "Cafe",
                    "Categories": "[\"Cafe\", \"Coffee shop\"]",
                }
            )
            tmp.flush()

            places = load_places(tmp.name)

        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].name, "My Test Place")
        self.assertEqual(places[0].place_id, "pid-123")
        self.assertEqual(places[0].polygon_name, "poly")
        self.assertEqual(places[0].place_url, "https://example.test")
        self.assertEqual(places[0].category, "Cafe")
        self.assertEqual(places[0].categories, ("Cafe", "Coffee shop"))


if __name__ == "__main__":
    unittest.main()
