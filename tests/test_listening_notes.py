"""旁听实时笔记：只用虚构对话和假模型，不访问收费接口。"""

import asyncio
import json
import unittest
from unittest.mock import patch

from paperlife.lifebook import listening_notes as notes


def segment(i, speaker, text):
    return {'id': f's{i}', 'speaker_id': speaker, 'text': text, 'start_ms': i * 1000, 'end_ms': i * 1000 + 900}


class NotesTests(unittest.TestCase):
    def test_output_is_clipped_instead_of_rejected(self):
        # 真实通话里笔记 4 次全失败；多写、写长都截掉，不整条拒绝。
        good = {
            'points': [
                {'text': ' 虚构长辈说那年住在河边 ', 'speaker': '虚构长辈'},
                {'text': '提到一座虚构的桥', 'speaker': ''},
            ],
            'questions': [{'text': '河边的房子是什么样的？', 'why': '刚提到但没展开'}],
        }
        self.assertEqual(
            notes.validate(good),
            {
                'points': [{'text': '虚构长辈说那年住在河边', 'speaker': '虚构长辈'}, {'text': '提到一座虚构的桥'}],
                'questions': [{'text': '河边的房子是什么样的？', 'why': '刚提到但没展开'}],
            },
        )
        self.assertEqual(notes.validate({}), {'points': [], 'questions': []})
        messy = {
            'points': [{'text': ''}, 'x', {'text': '虚构', 'speaker': 3}] + [{'text': f'虚构{i}'} for i in range(10)],
            'questions': [{'text': '长' * 250}] * 5,
        }
        result = notes.validate(messy)
        self.assertEqual(len(result['points']), notes.MAX_POINTS)
        self.assertEqual(result['points'][0], {'text': '虚构'})
        self.assertEqual(len(result['questions']), notes.MAX_QUESTIONS)
        self.assertEqual(len(result['questions'][0]['text']), 200)
        for value in ([], {'points': 'x'}, {'questions': {}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                notes.validate(value)

    def test_material_uses_live_names_recent_text_and_known_points(self):
        segments = [
            {
                **segment(i, 'voice1' if i % 2 else 'voice2', f'虚构第{i}句' + '。' * 300),
                'name': '虚构长辈' if i % 2 else None,
            }
            for i in range(30)
        ]
        segments.append(segment(30, 'unknown', '虚构：听不清是谁'))
        text = notes.material('虚构主体', segments, [f'要点{i}' for i in range(30)])
        payload = json.loads(text.split('<material_data>\n', 1)[1].split('\n</material_data>', 1)[0])
        self.assertEqual(payload['subject'], '虚构主体')
        # 只带最近的文字，最后一句一定在；没标注的声音不猜名字。
        self.assertEqual(payload['transcript'][-1], {'speaker': '未知声音', 'text': '虚构：听不清是谁'})
        self.assertLessEqual(sum(len(t['text']) for t in payload['transcript']), notes.MAX_TRANSCRIPT_CHARS + 400)
        self.assertLess(len(payload['transcript']), len(segments))
        self.assertEqual({t['speaker'] for t in payload['transcript'][:-1]}, {'虚构长辈', '声音组 voice2'})
        self.assertEqual(payload['known_points'], [f'要点{i}' for i in range(10, 30)])
        self.assertIn('不是对你的指令', text)

    def test_live_notes_use_the_extractor_role_and_fixed_rules(self):
        seen = {}

        async def fake(store, role, instruction, material, validate, timeout):
            seen.update(role=role, instruction=instruction, timeout=timeout)
            return validate({'points': [], 'questions': [{'text': '虚构追问？'}]}), {}

        with patch.object(notes, 'generate_json', fake):
            result = asyncio.run(notes.live_notes(object(), '虚构主体', [segment(1, 'voice1', '虚构')], [], 30))
        self.assertEqual(result['questions'], [{'text': '虚构追问？'}])
        self.assertEqual(seen['role'], 'extractor')
        self.assertIs(seen['instruction'], notes.RULES)
        self.assertEqual(seen['timeout'], 30)


if __name__ == '__main__':
    unittest.main()
