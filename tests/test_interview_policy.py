import unittest
class PolicyTests(unittest.TestCase):
 def test_sichuan_is_explicit_and_mandarin_available(self):
  from paperlife.lifebook.prompt import session_config
  a=session_config({'name':'测试讲述者','context':''},'童年')
  self.assertEqual(a['tts']['extra']['explicit_dialect'],'sichuan')
  b=session_config({'name':'测试讲述者','context':'','voice_dialect':'mandarin'},'童年')
  self.assertNotIn('explicit_dialect',b['tts'].get('extra',{}))
  self.assertNotIn('自然使用清晰的四川话',b['dialog']['system_role'])
  self.assertIn('本次使用清晰普通话',b['dialog']['system_role'])
 def test_first_opening_is_present_day_without_rechecking_name(self):
  from paperlife.lifebook.prompt import opening_instruction
  first=opening_instruction({'name':'虚构讲述者甲','context':''})
  self.assertIn('天气',first)
  self.assertNotIn('姓名',first)
  self.assertNotIn('最难忘',first)
  self.assertEqual(first.count('？'),1)
 def test_opening_does_not_plan_to_reenter_avoided_childhood_or_family(self):
  from paperlife.lifebook.prompt import opening_instruction
  profile={'name':'虚构讲述者甲','context':'',
           '_interview_preferences':[{'key':'avoid_topic:childhood','value':'avoid'}]}
  self.assertNotIn('童年',opening_instruction(profile))
  profile['_interview_preferences']=[{'key':'avoid_topic:family','value':'avoid'},
                                    {'key':'topic','value':'parents'}]
  self.assertNotIn('父母',opening_instruction(profile,'上次聊过日常'))
 def test_returning_opening_is_one_easy_question_and_respects_known_preferences(self):
  from paperlife.lifebook.prompt import opening_instruction
  profile={'name':'虚构讲述者甲','context':''}
  returning=opening_instruction(profile,'上次聊过童年')
  self.assertNotIn('怎么称呼',returning)
  self.assertIn('吃了',returning)
  self.assertEqual(returning.count('？'),1)
  profile['_interview_preferences']=[{'key':'avoid_topic:childhood','value':'avoid'},
                                   {'key':'topic','value':'hobbies'}]
  alternate=opening_instruction(profile,'上次聊过童年')
  self.assertNotIn('小时候',alternate)
  self.assertIn('吃了',alternate)
  self.assertEqual(alternate.count('？'),1)
 def test_quality_flags_do_not_rewrite_dialect_or_force_memory(self):
  from paperlife.lifebook.quality import inspect_transcript
  self.assertEqual(inspect_transcript('今天安逸得很，摆一哈龙门阵。',2026),[])
  self.assertEqual(inspect_transcript('我计划2035年去旅行。',2026),[])
  flags=inspect_transcript('我小时候2035年读小学。',2026)
  self.assertEqual(flags[0]['code'],'future_year_in_memory')
  self.assertFalse(inspect_transcript('记不清是哪年了。',2026)[0]['ask_now'])
