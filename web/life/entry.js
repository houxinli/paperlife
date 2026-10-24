/* 链接可重复使用；fragment不发往HTTP服务器，复制完整地址仍可打开。 */
export function resolveNarratorEntry(hash,session,local){
  const read=(s)=>{try{return s?.getItem('paperlife_narrator_token')||'';}catch{return '';}};
  const incoming=new URLSearchParams(hash.replace(/^#/, '')).get('invite');
  const token=incoming||read(session)||read(local);
  if(!/^[A-Za-z0-9_-]{32,100}$/.test(token))return {token:'',hash:''};
  for(const storage of [session,local]){try{storage?.setItem('paperlife_narrator_token',token);}catch{}}
  return {token,hash:'#invite='+encodeURIComponent(token)};
}
