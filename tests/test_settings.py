"""워크스페이스별 설정 저장·암호화·관리자 판정 테스트 (AC11, AC12)."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from request_agent.flows import Status
from request_agent.settings import Settings, SettingsStore, is_admin


class ReversingCipher:
    """테스트용: 뒤집기만 해서 평문이 그대로 저장되지 않는지 볼 수 있게 한다."""

    def encrypt(self, text: str) -> str:
        return "enc:" + text[::-1]

    def decrypt(self, token: str) -> str:
        return token.removeprefix("enc:")[::-1]


SAMPLE = replace(Settings(), wrike_token="wrike-secret-token", project="운영",
                 request_channel_id="C1", approver_ids=("U1", "U2"),
                 status_ids={Status.REVIEWING: "S1", Status.CONFIRMED: "S2",
                             Status.REJECTED: "S3"},
                 llm_api_key="llm-secret-key", categories={"bug": "버그"})


class SettingsStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.db"
        self.store = SettingsStore(self.path, ReversingCipher())

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_token_encrypted_roundtrip(self) -> None:
        self.store.save("T1", SAMPLE)
        self.assertEqual(self.store.load("T1"), SAMPLE)
        self.assertIsNone(self.store.load("T2"))  # 워크스페이스별로 따로 저장된다
        self.assertEqual(self.store.teams(), ["T1"])

        with closing(sqlite3.connect(self.path)) as db:
            raw = db.execute("SELECT data FROM settings").fetchone()[0]
        self.assertNotIn("wrike-secret-token", raw)
        self.assertNotIn("llm-secret-key", raw)

    def test_survives_new_store_instance(self) -> None:
        self.store.save("T1", SAMPLE)
        reopened = SettingsStore(self.path, ReversingCipher())
        self.assertEqual(reopened.load("T1"), SAMPLE)

    def test_wrong_key_keeps_settings_without_secrets(self) -> None:
        class OtherKey(ReversingCipher):
            def decrypt(self, token: str) -> str:
                raise ValueError("다른 키")

        self.store.save("T1", SAMPLE)
        loaded = SettingsStore(self.path, OtherKey()).load("T1")
        assert loaded is not None
        self.assertEqual((loaded.wrike_token, loaded.llm_api_key), ("", ""))
        self.assertEqual(loaded.project, "운영")
        self.assertFalse(loaded.is_complete())  # 다시 입력할 때까지 등록은 멈추고 안내된다

    def test_save_overwrites(self) -> None:
        self.store.save("T1", SAMPLE)
        self.store.save("T1", replace(SAMPLE, project="다른 프로젝트"))
        loaded = self.store.load("T1")
        assert loaded is not None
        self.assertEqual(loaded.project, "다른 프로젝트")


class FromEnvTest(unittest.TestCase):
    def test_from_env(self) -> None:
        settings = Settings.from_env({
            "WRIKE_TOKEN": "t", "WRIKE_REQUEST_FOLDER_ID": "R", "WRIKE_WORK_FOLDER_ID": "W",
            "WRIKE_STATUS_REVIEWING": "S1", "WRIKE_STATUS_CONFIRMED": "S2",
            "WRIKE_STATUS_REJECTED": "S3", "APPROVER_IDS": "U1,U2",
            "CATEGORIES": "bug:버그,etc:기타", "AI_OPINION": "off", "LLM_MODEL": "m"})
        self.assertEqual(settings.request_folder_id, "R")
        self.assertEqual(settings.status_ids[Status.CONFIRMED], "S2")
        self.assertEqual(settings.approver_ids, ("U1", "U2"))
        self.assertEqual(settings.categories, {"bug": "버그", "etc": "기타"})
        self.assertFalse(settings.ai_opinion)
        self.assertTrue(settings.is_complete())

    def test_incomplete_without_wrike(self) -> None:
        self.assertFalse(Settings().is_complete())


class AdminTest(unittest.TestCase):
    def test_admin_only(self) -> None:
        self.assertTrue(is_admin("U1", {"U1"}))
        self.assertFalse(is_admin("U9", {"U1"}))
        self.assertFalse(is_admin("U1", set()))


if __name__ == "__main__":
    unittest.main()
