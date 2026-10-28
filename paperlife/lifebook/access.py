"""档案、会话与专属链接的统一授权判定；各路由模块共用，不各自实现。"""

from fastapi import Header, HTTPException


class Access:
    def __init__(self, store, account_authorized=None):
        self.store = store
        self.account_authorized = account_authorized

    def profile(self, ident, user, edit=False, owner=False):
        p = self.store.profile(ident, user.id)
        if not p:
            raise HTTPException(404, '档案不存在或未获授权')
        if (owner and p['role'] != 'owner') or (edit and p['role'] == 'reader'):
            raise HTTPException(403, '没有修改权限')
        return p

    def session(self, ident, user, edit=False):
        s = self.store.session(ident)
        if not s:
            raise HTTPException(404, '聊天记录不存在')
        self.profile(s['profile'], user, edit=edit)
        return s

    def owner_active(self, link):
        return not self.account_authorized or self.account_authorized(link['profile_data']['owner'])

    def guest(self, x_life_link: str = Header(default='')):
        """FastAPI 依赖：专属链接只授权绑定档案，所有者账号停用时一并失效。"""
        link = self.store.link(token=x_life_link) if len(x_life_link) <= 200 else None
        if not link or not self.owner_active(link):
            raise HTTPException(401, '专属链接已失效，请联系家人重新发送')
        return link
