// 自动生成：请修改 shared/audio-gate.js，再运行 node scripts/sync_shared.mjs。
// 源文件 SHA256: 86d78bf3f09db318ecb0c7525b4f1922c26b3160cfeff06a9c6b8ffa8d6520b8
// 只保存协议状态，不接触麦克风、网络、播放队列或身份凭证。
// 导出包装由 scripts/sync_shared.mjs 生成，两个客户端只导入各自的产物。
class AudioGate {
  constructor({allowLegacyAudio=false}={}) {
    this.allowLegacyAudio=allowLegacyAudio;
    this.open=allowLegacyAudio;
    this.closed=false;
    this.boundariesSeen=false;
    this.replyId=null;
    this.questionId=null;
    this.audioReplyId=null;
    this.audioQuestionId=null;
    this.cancelledReplies=new Set();
    this.cancelledQuestions=new Set();
    this.finished=new Set();
  }

  stale(event) {
    return Boolean((event.reply_id&&this.cancelledReplies.has(event.reply_id))||
      (event.question_id&&(this.cancelledQuestions.has(event.question_id)||
        (this.questionId&&event.question_id!==this.questionId))));
  }

  accepts() { return this.open&&!this.closed; }

  cancel() {
    if(this.replyId)this.cancelledReplies.add(this.replyId);
    if(this.audioReplyId)this.cancelledReplies.add(this.audioReplyId);
    if(this.audioQuestionId)this.cancelledQuestions.add(this.audioQuestionId);
    this.open=false;
  }

  close() { this.closed=true;this.open=false; }

  event(event) {
    if(this.closed)return;
    if(event.type==='interrupt') {
      this.cancel();
      this.questionId=event.question_id||null;
    }
    if(event.type==='answer_start'&&!this.stale(event)) {
      this.replyId=event.reply_id||null;
      // 只让网页兼容旧服务器；文字流和已见过独立边界的新协议不能开门。
      if(this.allowLegacyAudio&&!this.boundariesSeen&&event.source!=='text')this.open=true;
    }
    if(event.type==='audio_start') {
      this.boundariesSeen=true;
      this.audioReplyId=event.reply_id||null;
      this.audioQuestionId=event.question_id||null;
      this.open=!this.stale(event)&&!(event.reply_id&&this.finished.has(event.reply_id));
      if(this.open)this.replyId=event.reply_id||null;
    }
    // audio_sentence_end 只结束一句；一个回答可能有多句音频。
    if(event.type==='audio_end') {
      if(event.reply_id)this.finished.add(event.reply_id);
      if((!event.reply_id||event.reply_id===this.audioReplyId)&&
          (!event.question_id||event.question_id===this.audioQuestionId))this.open=false;
    }
    if(event.type==='ending'||event.type==='ended')this.close();
  }
}

module.exports={AudioGate};
