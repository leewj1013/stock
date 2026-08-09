import os
import tempfile
import unittest

from stock_alarm.secret_check import scan


class SecretCheckTest(unittest.TestCase):
    def test_ignores_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, ".env"), "w", encoding="utf-8") as file:
                file.write("TELEGRAM_BOT_TOKEN=" + "1234567890" + ":" + "abcdefghijklmnopqrstuvwxyzABCDE" + "\n")

            self.assertEqual([], scan(directory))

    def test_finds_token_in_public_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "README.md")
            with open(path, "w", encoding="utf-8") as file:
                file.write("token=" + "1234567890" + ":" + "abcdefghijklmnopqrstuvwxyzABCDE" + "\n")

            self.assertEqual([path], scan(directory))

    def test_ignores_sqlite_and_binary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            token = b"1234567890:" + b"abcdefghijklmnopqrstuvwxyzABCDE"
            for name, content in (("stock_alarm.db", token), ("cache.bin", b"\x00" + token)):
                with open(os.path.join(directory, name), "wb") as file:
                    file.write(content)

            self.assertEqual([], scan(directory))

    def test_still_scans_text_data_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "watchlist.csv")
            with open(path, "w", encoding="utf-8") as file:
                file.write("token," + "1234567890" + ":" + "abcdefghijklmnopqrstuvwxyzABCDE\n")

            self.assertEqual([path], scan(directory))


if __name__ == "__main__":
    unittest.main()
