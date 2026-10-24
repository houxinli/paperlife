"""采访规则与受授权背景的组合。"""
from pathlib import Path
from .preferences import TOPICS, render_preferences, safe_name

POLICY_VERSION='2026-10-24-v3'
RULES=(Path(__file__).resolve().parents[2]/'policies/interviewer.md').read_text(encoding='utf-8')

def interviewer_name(profile):
    return next((e['value'] for e in reversed(profile.get('_interview_preferences') or [])
                 if isinstance(e, dict) and e.get('key') == 'interviewer_name' and safe_name(e.get('value'))), '拾光')


def opening_naming_target(profile, recent=''):
    has_name = any(isinstance(e, dict) and e.get('key') == 'interviewer_name' and safe_name(e.get('value'))
                   for e in profile.get('_interview_preferences') or [])
    if profile.get('interviewer_name_asked') or has_name:
        return None
    # 没有迁移字段的旧调用方保持原有回访行为。
    return 'interviewer_name' if 'interviewer_name_asked' in profile or not recent.strip() else None


def opening_instruction(profile, recent=''):
    """给首个 501 请求一条容易作答的具体问题，避免模型自行出大题。"""
    introduction = '先用一句话说明你是AI采访助手' + interviewer_name(profile) + '，现在正在录音，随时可以跳过或结束。'
    if opening_naming_target(profile, recent):
        return introduction + '这是第一次邀请讲述者给AI取昵称，档案已有称呼，不核实本名。只问这一句并等待回答：“您想怎么称呼我？”不再追加称呼问题；对方说随便、跳过或直接讲故事就保留默认昵称并顺着聊。明确“叫我某某”是讲述者自己的称呼，不能当作AI昵称。'
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
        'dialog': {'bot_name':interviewer_name(profile), 'system_role': RULES + '\n' + language + preferences + '\n档案称呼：' + profile['name'] + '\n本次话题：' + topic + '\n<采访素材>\n' + background + '\n</采访素材>\n<上次记录>\n' + recent[:3000] + '\n</上次记录>',
                   'speaking_style':('使用清晰自然的四川话，不夸张模仿。' if dialect=='sichuan' else '使用清晰普通话。')+'语气耐心，语速稍慢，一次一个短问题，少说多听。',
                   'extra': {'model':'1.2.1.1', 'input_mod':'keep_alive', 'enable_volc_websearch':False, 'enable_conversation_truncate':True, 'enable_user_query_exit':True}}}
