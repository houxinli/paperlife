"""采访规则与受授权背景的组合。"""
from pathlib import Path
from .preferences import TOPICS, render_preferences

POLICY_VERSION='2026-10-24-v2'
RULES=(Path(__file__).resolve().parents[2]/'policies/interviewer.md').read_text(encoding='utf-8')

def opening_instruction(profile, recent=''):
    """给首个 501 请求一条容易作答的具体问题，避免模型自行出大题。"""
    introduction = '先用一句话说明你是AI采访助手拾光，现在正在录音，随时可以跳过或结束。'
    if not recent.strip():
        return introduction + '这是首次交谈，档案已有称呼，不核实本名。只问这一句并等待回答：“我怎么称呼您比较合适？”回答后沿讲述者愿意说的具体日常继续，遵守已确认采访偏好。'
    entries = profile.get('_interview_preferences') or []
    avoided = {entry.get('key', '').removeprefix('avoid_topic:') for entry in entries
               if isinstance(entry, dict) and isinstance(entry.get('key'), str)
               and entry['key'].startswith('avoid_topic:') and entry.get('value') == 'avoid'}
    if 'family' in avoided:
        avoided.add('parents')
    preferred = next((entry.get('value') for entry in reversed(entries)
                      if isinstance(entry, dict) and entry.get('key') == 'topic'
                      and entry.get('value') in TOPICS), 'childhood')
    questions = {
        'childhood': '您小时候主要住在哪里？',
        'home': '您以前住的地方，房子是什么样的？',
        'school': '以前上学时，您每天怎么去学校？',
        'work': '以前上班时，您平常一天先做什么？',
        'family': '您以前在家时，平常一天先做什么？',
        'parents': '您想先聊父母平时做的哪件小事？',
        'hobbies': '您平时闲下来，喜欢做点什么？',
    }
    topic = preferred if preferred not in avoided else next((key for key in ('childhood', 'hobbies', 'home', 'work', 'school') if key not in avoided), None)
    question = questions.get(topic, '您今天想从哪件小事聊起？')
    return introduction + '这是回访，不再询问如何称呼，不要求重复上次故事。只问这一句并等待回答：“' + question + '”'


def session_config(profile, topic, recent=''):
    background = profile['context'][:14000]
    dialect=profile.get('voice_dialect','sichuan')
    extra={'explicit_dialect':'sichuan'} if dialect=='sichuan' else {}
    language = '本次使用清晰自然的四川话，不夸张模仿。' if dialect=='sichuan' else '本次使用清晰普通话。'
    preferences = render_preferences(profile.get('_interview_preferences'))
    return {
        'asr': {'extra': {'end_smooth_window_ms': 2500, 'enable_custom_vad': True}},
        'tts': {'extra':extra, 'speaker':'zh_female_vv_jupiter_bigtts', 'audio_config':{'channel':1,'format':'pcm_s16le','sample_rate':24000}},
        'dialog': {'bot_name':'拾光', 'system_role': RULES + '\n' + language + preferences + '\n档案称呼：' + profile['name'] + '\n本次话题：' + topic + '\n<采访素材>\n' + background + '\n</采访素材>\n<上次记录>\n' + recent[:3000] + '\n</上次记录>',
                   'speaking_style':('使用清晰自然的四川话，不夸张模仿。' if dialect=='sichuan' else '使用清晰普通话。')+'语气耐心，语速稍慢，一次一个短问题，少说多听。',
                   'extra': {'model':'1.2.1.1', 'input_mod':'keep_alive', 'enable_volc_websearch':False, 'enable_conversation_truncate':True}}}
