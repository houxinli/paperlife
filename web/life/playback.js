/* 录音开始/编辑器关闭使所有旧回听请求失效，包括下载和媒体解码仍在等待的请求。 */
export function createPlaybackGuard({isRecording,getPlayers}){
  let generation=0;
  const assert=ticket=>{if(ticket!==generation||isRecording())throw Error('回听已取消：请结束录音后重新点击播放。');};
  return {
    capture:()=>generation,
    assert,
    invalidate(){generation++;for(const player of getPlayers()){try{player.pause();}catch{}}},
    onPlay(event){if(isRecording())event.target?.pause?.();},
    async play(player,ticket){
      assert(ticket);
      try{await player.play();assert(ticket);}catch(error){player.pause();throw error;}
    }
  };
}
