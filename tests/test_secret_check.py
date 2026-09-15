import os
import tempfile
import unittest

from stock_alarm.secret_check import scan


class SecretCheckTest(unittest.TestCase):
    def test_scans_env_file_if_it_is_inside_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, ".env"), "w", encoding="utf-8") as file:
                file.write("TELEGRAM_BOT_TOKEN=" + "1234567890" + ":" + "abcdefghijklmnopqrstuvwxyzABCDE" + "\n")

            self.assertEqual([os.path.join(directory, ".env")], scan(directory))

    def test_finds_all_kakao_credential_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "settings.txt")
            with open(path, "w", encoding="utf-8") as file:
                file.write("KAKAO_JAVASCRIPT_KEY=real-looking-value-123\n")
                file.write("KAKAO_NATIVE_APP_KEY=real-looking-value-456\n")
                file.write("KAKAO_REST_API_KEY=real-looking-value-789\n")
            self.assertEqual([path], scan(directory))

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

    def test_finds_named_toss_and_naver_credentials_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "accidental.env.txt")
            with open(path, "w", encoding="utf-8") as file:
                file.write("TOSS_CLIENT_SECRET=example-sensitive-credential-123456789\n")
                file.write("NAVER_HUB_CLIENT_SECRET=another-sensitive-credential-123456789\n")

            self.assertEqual([path], scan(directory))

    def test_allows_empty_example_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, ".env.example")
            with open(path, "w", encoding="utf-8") as file:
                file.write("TOSS_CLIENT_SECRET=\nNAVER_HUB_CLIENT_SECRET=changeme\n")

            self.assertEqual([], scan(directory))


if __name__ == "__main__":
    unittest.main()
