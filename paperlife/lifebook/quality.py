"""低延迟旁路疑点检查。不是ASR置信度，不改写原文。"""
import re

def inspect_transcript(text,current_year):
 flags=[]
 def add(code,reason,ask_now=True):flags.append({'code':code,'reason':reason,'ask_now':ask_now})
 if re.search(r'\[听不清\]|\[无法识别\]|�|哪个字|什么字|名字不确定',text):
  add('unclear_word','存在字词或姓名用字疑点，适合一次中性澄清。')
 if re.search(r'记不清|记不得|想不起来|不想说|不愿意讲',text):
  add('defer_memory','讲述者表达不确定或拒绝，不继续逼问，留给事后核对。',False)
 if re.search(r'小时候|那时候|当时|出生|毕业|结婚',text) and not re.search(r'计划|将来|未来|希望|明年',text):
  if any(int(y)>current_year+1 for y in re.findall(r'(?<!\d)([12]\d{3})年',text)):
   add('future_year_in_memory','回忆语境出现未来年份；可能是识别或记忆差异，不可自动改写。')
 if re.search(r'说错了|不是.{1,20}是|刚才.{0,20}应该',text):
  add('speaker_correction','讲述者主动更正；保留前后说法，不覆盖原始记录。',False)
 return flags
