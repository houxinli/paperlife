import tempfile,unittest,sqlite3,json
from pathlib import Path
from paperlife.lifebook.store import Store
from paperlife.auth import Auth
from scripts.migrate import migrate

class MigrationTests(unittest.TestCase):
 def test_private_migration_preserves_ids_but_excludes_unrelated_data_and_keys(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);auth=Auth(root/'old.sqlite3');uid=auth.create_user('fixture','long-test-password','admin')
   with auth.db() as db:db.execute('CREATE TABLE unrelated_financial_data(value TEXT)');db.execute("INSERT INTO unrelated_financial_data VALUES('fictional-test-only')")
   old=Store(root/'oldlife');p=old.create_profile(uid,'虚构人物','虚构原文');s=old.create_session(p['id'],uid,'test');old.audio_path(s['id']).write_bytes(b'fictional-audio-fixture');old.configure({'api_key':'test-only'})
   target=root/'new';dry=migrate(root/'oldlife',root/'old.sqlite3',target);self.assertFalse(target.exists());self.assertFalse(dry['applied'])
   result=migrate(root/'oldlife',root/'old.sqlite3',target,True,True);self.assertTrue(result['applied'])
   with sqlite3.connect(target/'auth.sqlite3') as db:
    self.assertEqual(db.execute('SELECT id FROM users').fetchone()[0],uid)
    self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='unrelated_financial_data'").fetchone())
   self.assertFalse((target/'life/provider.json').exists())
   self.assertEqual((target/'life'/old.audio_path(s['id']).name).read_bytes(),b'fictional-audio-fixture')
