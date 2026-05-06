import tempfile
import unittest
from pathlib import Path

import memory_store
import skill_manager


class SkillMemoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        # monkeypatch skill paths
        self._old_skills = (
            skill_manager.SKILLS_DIR,
            skill_manager.SKILLS_ITEMS_DIR,
            skill_manager.SKILLS_INDEX_FILE,
        )
        skill_manager.SKILLS_DIR = base / "skills"
        skill_manager.SKILLS_ITEMS_DIR = skill_manager.SKILLS_DIR / "items"
        skill_manager.SKILLS_INDEX_FILE = skill_manager.SKILLS_DIR / "index.json"
        # monkeypatch memory paths
        self._old_mem = (
            memory_store.MEMORY_DIR,
            memory_store.RULES_FILE,
            memory_store.FACTS_FILE,
            memory_store.ARCHIVES_FILE,
        )
        memory_store.MEMORY_DIR = base / "memory"
        memory_store.RULES_FILE = memory_store.MEMORY_DIR / "rules.json"
        memory_store.FACTS_FILE = memory_store.MEMORY_DIR / "facts.json"
        memory_store.ARCHIVES_FILE = memory_store.MEMORY_DIR / "archives.jsonl"

    def tearDown(self):
        (
            skill_manager.SKILLS_DIR,
            skill_manager.SKILLS_ITEMS_DIR,
            skill_manager.SKILLS_INDEX_FILE,
        ) = self._old_skills
        (
            memory_store.MEMORY_DIR,
            memory_store.RULES_FILE,
            memory_store.FACTS_FILE,
            memory_store.ARCHIVES_FILE,
        ) = self._old_mem
        self._tmp.cleanup()

    def test_skill_lifecycle_and_weighted_match(self):
        sm = skill_manager.SkillManager()
        a = sm.upsert_skill(
            name="Refactor Python",
            intent_pattern=["重构", "python", "模块"],
            steps=["read", "edit", "verify"],
            skill_id="s1",
        )
        b = sm.upsert_skill(
            name="Fix Bug",
            intent_pattern=["修复", "bug"],
            steps=["reproduce", "fix", "test"],
            skill_id="s2",
        )
        self.assertEqual(a.id, "s1")
        self.assertEqual(b.id, "s2")

        m = sm.match_detailed("请帮我重构 python 模块")
        self.assertIsNotNone(m.best)
        self.assertEqual(m.best.id, "s1")
        self.assertEqual(m.miss_reason, "")

        sm.mark_used("s1")
        s1 = sm.load_skill("s1")
        self.assertGreaterEqual(s1.success_count, 1)
        self.assertTrue(bool(s1.last_used_at))

        # disable should remove from matching
        s1.enabled = False
        sm.update_skill_full(s1)
        m2 = sm.match_detailed("请帮我重构 python 模块")
        self.assertTrue((m2.best is None) or (m2.best.id != "s1"))

    def test_memory_archive_and_injection(self):
        self.assertEqual(memory_store.load_rules(), [])
        memory_store.add_rule("Always explain tradeoffs.")
        memory_store.add_fact("Workspace is local-only", source="settings")
        memory_store.append_archive(
            session_id="sid-1",
            title="Refactor task",
            user_prompt="refactor agent.py",
            summary="suggested decomposition and validation",
            turns=3,
            workspace="E:/QuarkSpace/Agent",
        )
        text = memory_store.build_injection_text()
        self.assertIn("User rules", text)
        self.assertIn("Stable facts", text)
        self.assertIn("Recent task archives", text)


if __name__ == "__main__":
    unittest.main()
