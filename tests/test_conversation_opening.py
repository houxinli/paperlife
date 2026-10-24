"""日常开场不考试、不猜天气，也不把普通回答当昵称。"""
import asyncio,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from paperlife.lifebook.prompt import opening_instruction,opening_naming_target,session_config
from paperlife.lifebook.realtime import interview
from paperlife.lifebook.store import Store
from test_realtime_rounds import RecordingClient,ScriptedProvider


class ConversationOpeningTests(unittest.IsolatedAsyncioTestCase):
    def test_new_and_returning_conversations_start_with_one_present_day_question(self):
        for recent in ('','user:过去聊过小时候住的房子'):
            profile={'name':'虚构人物','context':'已有历史资料','interviewer_name_asked':0}
            message=opening_instruction(profile,recent)
            self.assertIn('吃了' if recent else '天气',message);self.assertEqual(message.count('？'),1)
            self.assertNotIn('您想怎么称呼我',message)
            self.assertNotIn('您小时候主要住在哪里',message)
            self.assertIsNone(opening_naming_target(profile,recent))

    def test_policy_keeps_ai_identity_and_does_not_claim_live_weather(self):
        config=session_config({'name':'虚构人物','context':''},'随便聊聊今天的小事')
        role=config['dialog']['system_role']
        self.assertIn('AI',role);self.assertIn('不编造',role)
        self.assertIn('不强迫',role);self.assertIn('吃',role)

    async def test_weather_reply_is_archived_without_becoming_a_name(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root));store.configure({'api_key':'fictional'})
            profile=store.create_profile(1,'虚构人物','');session=store.create_session(profile['id'],1,'今天')
            provider=ScriptedProvider([(450,{'question_id':'q1'}),(451,{'results':[{'text':'晴天','is_interim':False}]})])
            with patch('paperlife.lifebook.realtime.connect',return_value=provider),patch('paperlife.lifebook.processing.enqueue'):
                await asyncio.wait_for(interview(RecordingClient(),store,session,profile),2)
            self.assertEqual(store.preferences(profile['id']),[])
            self.assertEqual(store.turns(session['id'])[0]['text'],'晴天')
            self.assertEqual(store.profile(profile['id'],1)['interviewer_name_asked'],0)
