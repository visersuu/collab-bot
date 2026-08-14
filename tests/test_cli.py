import unittest

from python_project import greeting


class GreetingTests(unittest.TestCase):
    def test_greeting_uses_name(self) -> None:
        self.assertEqual(greeting("Ada"), "Hello, Ada!")

    def test_greeting_accepts_default_style_name(self) -> None:
        self.assertEqual(greeting("world"), "Hello, world!")


if __name__ == "__main__":
    unittest.main()
