"""采访规则与受授权背景的组合。"""
from pathlib import Path
from .preferences import render_preferences, safe_name

POLICY_VERSION='2026-10-24-v4'
RULES=(Path(__file__).resolve().parents[2]/'policies/interviewer.md').read_text(encoding='utf-8')

def interviewer_name(profile):
    return next((e['value'] for e in reversed(profile.get('_interview_preferences') or [])
                 if isinstance(e, dict) and e.get('key') == 'interviewer_name' and safe_name(e.get('value'))), '拾光')


def opening_naming_target(profile, recent=''):
    """日常聊天不进入短名字推断；明确的叫我/叫你请求仍可随时保存。"""
    return None


def opening_instruction(profile, recent=''):
    """从眼前小事轻松开始，避免让人进入接受访谈或回忆考试的状态。"""
    introduction='先简短说明你是AI'+interviewer_name(profile)+'，会把聊天录下来，对方说停就停。不要介绍自己是采访者，也不把对方称作被采访者。'
    question='您今天吃了些什么呀？' if recent.strip() else '您那边今天天气怎么样？'
    return (introduction+'不用先取名、核实身份或回顾上次记录。只问这一件当下的小事，然后等对方回答：“'+question+
            '”先顺着回答随便聊；不编造当地天气，不假设已经吃饭，不强迫转入往事。对方主动聊往事或明确想说的话题时跟随。')


def session_config(profile, topic, recent=''):
    background = profile['context'][:14000]
    dialect=profile.get('voice_dialect','sichuan')
    extra={'explicit_dialect':'sichuan'} if dialect=='sichuan' else {}
    language = '本次使用清晰自然的四川话，不夸张模仿。' if dialect=='sichuan' else '本次使用清晰普通话。'
    preferences = render_preferences(profile.get('_interview_preferences'))
    return {
        'asr': {'extra': {'end_smooth_window_ms': 2500, 'enable_custom_vad': True}},
        'tts': {'extra':extra, 'speaker':'zh_female_vv_jupiter_bigtts', 'audio_config':{'channel':1,'format':'pcm_s16le','sample_rate':24000}},
        'dialog': {'bot_name':interviewer_name(profile), 'system_role': RULES + '\n' + language + preferences + '\n档案称呼：' + profile['name'] + '\n可参考的话题（仅在对方想聊时跟随）：' + topic + '\n<采访素材>\n' + background + '\n</采访素材>\n<上次记录>\n' + recent[:3000] + '\n</上次记录>',
                   'speaking_style':('使用清晰自然的四川话，不夸张模仿。' if dialect=='sichuan' else '使用清晰普通话。')+'语气耐心，语速稍慢，一次一个短问题，少说多听。',
                   'extra': {'model':'1.2.1.1', 'input_mod':'keep_alive', 'enable_volc_websearch':False, 'enable_conversation_truncate':True, 'enable_user_query_exit':True}}}
