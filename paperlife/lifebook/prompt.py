"""采访规则与受授权背景的组合。"""
from pathlib import Path
POLICY_VERSION='2026-10-23-v1'
RULES=(Path(__file__).resolve().parents[2]/'policies/interviewer.md').read_text(encoding='utf-8')



def session_config(profile, topic, recent=''):
    background = profile['context'][:14000]
    dialect=profile.get('voice_dialect','sichuan')
    extra={'explicit_dialect':'sichuan'} if dialect=='sichuan' else {}
    return {
        'asr': {'extra': {'end_smooth_window_ms': 2500, 'enable_custom_vad': True}},
        'tts': {'extra':extra, 'speaker':'zh_female_vv_jupiter_bigtts', 'audio_config':{'channel':1,'format':'pcm_s16le','sample_rate':24000}},
        'dialog': {'bot_name':'拾光', 'system_role': RULES + '\n档案称呼：' + profile['name'] + '\n本次话题：' + topic + '\n<采访素材>\n' + background + '\n</采访素材>\n<上次记录>\n' + recent[:3000] + '\n</上次记录>',
                   'speaking_style':('使用清晰自然的四川话，不夸张模仿。' if dialect=='sichuan' else '使用清晰普通话。')+'语气耐心，语速稍慢，一次一个短问题，少说多听。',
                   'extra': {'model':'1.2.1.1', 'input_mod':'keep_alive', 'enable_volc_websearch':False, 'enable_conversation_truncate':True}}}
