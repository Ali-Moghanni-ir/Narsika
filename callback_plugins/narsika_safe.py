"""Emit task lifecycle only; never serialize module results, arguments or raw output."""
import json
import re
from ansible.plugins.callback import CallbackBase

class CallbackModule(CallbackBase):
    CALLBACK_VERSION=2.0
    CALLBACK_TYPE='stdout'
    CALLBACK_NAME='narsika_safe'
    def emit(self,event,result=None):
        action=str(getattr(getattr(result,'_task',None),'action',''))
        if not re.fullmatch(r'[A-Za-z0-9_.-]{0,120}',action):action='task'
        changed=bool(getattr(result,'_result',{}).get('changed',False))
        self._display.display('NARSIKA_EVENT '+json.dumps(dict(event=event,action=action,changed=changed)))
    def v2_runner_on_ok(self,result):self.emit('ok',result)
    def v2_runner_on_failed(self,result,ignore_errors=False):self.emit('failed',result)
    def v2_runner_on_unreachable(self,result):self.emit('unreachable',result)
    def v2_runner_on_skipped(self,result):self.emit('skipped',result)
    def v2_playbook_on_stats(self,stats):self.emit('recap')
