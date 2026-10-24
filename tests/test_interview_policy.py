import unittest
class PolicyTests(unittest.TestCase):
 def test_sichuan_is_explicit_and_mandarin_available(self):
  from paperlife.lifebook.prompt import session_config
  a=session_config({'name':'测试讲述者','context':''},'童年')
  self.assertEqual(a['tts']['extra']['explicit_dialect'],'sichuan')
  b=session_config({'name':'测试讲述者','context':'','voice_dialect':'mandarin'},'童年')
  self.assertNotIn('explicit_dialect',b['tts'].get('extra',{}))
 def test_quality_flags_do_not_rewrite_dialect_or_force_memory(self):
  from paperlife.lifebook.quality import inspect_transcript
  self.assertEqual(inspect_transcript('今天安逸得很，摆一哈龙门阵。',2026),[])
  self.assertEqual(inspect_transcript('我计划2035年去旅行。',2026),[])
  flags=inspect_transcript('我小时候2035年读小学。',2026)
  self.assertEqual(flags[0]['code'],'future_year_in_memory')
  self.assertFalse(inspect_transcript('记不清是哪年了。',2026)[0]['ask_now'])
