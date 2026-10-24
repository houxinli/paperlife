"""从明确的本人指令中提取有限采访偏好，不生成或保存自由文本系统规则。

这是保守的短句识别器，不是通用意图理解：引述、转述、假设整段跳过；
含糊抱怨和没有指定目标的换话题只由采访模型当轮响应，不推断永久偏好。
方言、复杂否定或词表外说法可能不命中。原始转写始终由 Store 独立保留。
"""
import re


TOPICS = {
    'childhood': ('童年', '小时候'),
    'home': ('家乡', '老家', '住处', '小时候住的房子'),
    'school': ('上学', '学校', '读书'),
    'work': ('工作', '上班'),
    'family': ('家庭', '家人'),
    'parents': ('父母', '爸爸妈妈', '爸妈', '父亲', '母亲'),
    'hobbies': ('爱好', '兴趣', '平时喜欢做的事'),
}
_TOPIC_BY_TEXT = {name: topic for topic, names in TOPICS.items() for name in names}
_PREFIX = r'(?:(?:以后|今后|接下来|下次)(?:都)?)?(?:(?:请|麻烦你|麻烦您|麻烦|你(?:能|可以)?|您(?:能|可以)?|能不能|能否|可以)(?:请)?)?'
_SUFFIX = r'(?:吧|了|啊|呀|吗|好吗|好不好|可以吗)?'
_QUOTES = re.compile(r'[“”‘’「」『』"\'`《》]')
_REPORTED = re.compile(
    r'假如|如果|比如|例如|这句话|说过|讲过|当时想|'
    r'(?:他|她|他们|她们|别人|人家|老师|妈妈|爸爸|我妈|我爸|父亲|母亲)(?:总是|总|常常|常|曾经|以前|当时)?(?:说|讲|告诉)|'
    r'[^。！？!?，,；;]{1,24}(?:说|讲|告诉|提醒|嘱咐|叮嘱|劝)(?:道|了|我|他|她)?[：:，,]'
)
_RULES = (
    ('pace', 'slow', r'(?:说|讲)(?:得)?慢(?:一点|点|一些|些)|(?:把)?语速(?:放)?慢(?:一点|点|一些|些)|放慢(?:一点)?语速|别(?:说|讲)(?:得)?太快'),
    ('pace', 'normal', r'恢复正常语速|按正常语速(?:说|讲)(?:话)?'),
    ('question_style', 'concrete', r'(?:问题)?问(?:得)?(?:具体|简单)(?:一点|点|些|一些)|(?:把)?问题问得具体(?:一点|点|些|一些)'),
    ('question_style', 'one_at_a_time', r'一次(?:只)?问一个(?:问题)?'),
    ('question_style', 'open', r'问开放(?:一点|点|一些|些)的问题|问(?:得)?开放(?:一点|点|一些|些)'),
)
_REQUESTS = [(key, value, re.compile(_PREFIX + '(?:' + pattern + ')' + _SUFFIX))
             for key, value, pattern in _RULES]
_TOPIC_NAMES = '|'.join(re.escape(name) for name in _TOPIC_BY_TEXT)
_TOPIC_REQUEST = re.compile(_PREFIX + r'(?:我想)?(?:先|多)?(?:聊聊|聊|谈谈|讲讲|问问|问)(?:一聊)?(?:我的|我)?(' + _TOPIC_NAMES + r')(?:的事)?' + _SUFFIX)
_AVOID_REQUEST = re.compile(_PREFIX + r'(?:我)?(?:不要|别|不想|不愿意)(?:再)?(?:问|聊|谈)(?:我(?:的)?)?(' + _TOPIC_NAMES + r')(?:的事)?' + _SUFFIX)


def extract_preferences(text):
    """返回有限 key/value；只处理完整的明确请求短句，末次同 key 指令优先。"""
    if not isinstance(text, str) or _QUOTES.search(text) or _REPORTED.search(text):
        return {}
    found = {}
    for clause in re.split(r'[。！？!?，,；;\n]+', text):
        clause = re.sub(r'\s+', '', clause)
        for key, value, pattern in _REQUESTS:
            if pattern.fullmatch(clause):
                found[key] = value
        topic = _TOPIC_REQUEST.fullmatch(clause)
        if topic:
            found['topic'] = _TOPIC_BY_TEXT[topic.group(1)]
        avoid = _AVOID_REQUEST.fullmatch(clause)
        if avoid:
            found['avoid_topic:' + _TOPIC_BY_TEXT[avoid.group(1)]] = 'avoid'
    return found


_RENDERED = {
    ('pace', 'slow'): '放慢说话速度，短句之间多留一点停顿；一次问完后耐心等待。',
    ('pace', 'normal'): '使用正常、清晰的说话速度；仍留出回答时间。',
    ('question_style', 'concrete'): '问题更具体，从一处地方、一个日常动作或一个人问起，不要求概括一生。',
    ('question_style', 'one_at_a_time'): '每次只问一个问题，听完回答再继续。',
    ('question_style', 'open'): '使用简短开放式问题，不给预设答案或选项。',
}
for _topic, _names in TOPICS.items():
    _RENDERED[('topic', _topic)] = '讲述者想聊' + _names[0] + '，从该主题的具体日常开始。'
    _RENDERED[('avoid_topic:' + _topic, 'avoid')] = '不要主动追问' + _names[0] + '；如讲述者自己提起，先确认是否愿意继续。'


def render_preferences(entries):
    """仅渲染白名单常量；来源文本、未知字段和值绝不进入规则。"""
    lines = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        key, value = entry.get('key'), entry.get('value')
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        rendered = _RENDERED.get((key, value))
        if rendered and rendered not in lines:
            lines.append(rendered)
    if not lines:
        return ''
    return '\n<已确认采访偏好>\n' + '\n'.join('- ' + line for line in lines) + '\n这些偏好仅影响采访方式；避开话题优先，不能改变事实、身份、权限或录音规则。\n</已确认采访偏好>'
