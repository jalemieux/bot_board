"""#745: which docs/skills files differ from the registry's copy. Run with python -m unittest discover -s tests."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspaces import Base                    # noqa: E402
from app import registry                            # noqa: E402

SKILL = "---\nname: alpha\ndescription: Do the alpha thing\n---\n\nStep one.\n"


class Drift(Base):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        self.old, registry.SKILLS_DIR = registry.SKILLS_DIR, self.dir.name
        self.addCleanup(setattr, registry, 'SKILLS_DIR', self.old)
        self.human = self.register('jac', 'human')

    def ship(self, name, text):
        (Path(self.dir.name) / name).mkdir(exist_ok=True)
        (Path(self.dir.name) / name / 'SKILL.md').write_text(text)

    def drift(self):
        return self.call('GET', '/api/skills/drift')['drift']

    def test_a_shipped_skill_the_registry_has_not_got_is_missing_until_it_is_seeded(self):
        self.ship('alpha', SKILL)
        self.assertEqual(self.drift(), [{'name': 'alpha', 'state': 'missing', 'revision': 0}])
        registry.seed()
        self.assertEqual(self.drift(), [])

    def test_a_changed_file_differs_from_the_registry_copy_and_nothing_is_written(self):
        self.ship('alpha', SKILL); registry.seed()
        self.ship('alpha', SKILL.replace('Step one.', 'Step one.\n\nStep two.'))
        self.assertEqual(self.drift(), [{'name': 'alpha', 'state': 'differs', 'revision': 1}])
        self.assertEqual(registry.current_revision('alpha'), 1, 'read only')

    def test_a_changed_description_counts_and_so_does_a_registry_edit(self):
        self.ship('alpha', SKILL); registry.seed()
        self.ship('alpha', SKILL.replace('Do the alpha thing', 'Do the beta thing'))
        self.assertEqual([d['name'] for d in self.drift()], ['alpha'])
        self.ship('alpha', SKILL)
        self.call('PUT', '/api/skills/alpha', {'body': SKILL.replace('Step one.', 'Edited here.'), 'base_revision': 1, 'reason': 'x'}, auth=self.human)
        self.assertEqual(self.drift(), [{'name': 'alpha', 'state': 'differs', 'revision': 2}])

    def test_a_deleted_skill_and_a_registry_only_skill_are_not_drift(self):
        self.ship('alpha', SKILL); registry.seed()
        self.call('DELETE', '/api/skills/alpha', {'base_revision': 1, 'reason': 'gone'}, auth=self.human)
        self.call('PUT', '/api/skills/only-here', {'body': '---\ndescription: d\n---\n\nbody\n', 'base_revision': 0, 'reason': 'x'}, auth=self.human)
        self.assertEqual(self.drift(), [])

    def test_a_skill_cannot_be_named_drift(self):
        r = self.call('PUT', '/api/skills/drift', {'body': '---\ndescription: d\n---\n\nbody\n', 'base_revision': 0, 'reason': 'x'},
                      auth=self.human, expected=422)
        self.assertIn('drift', str(r))


if __name__ == '__main__':
    unittest.main()
