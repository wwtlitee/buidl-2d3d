"""User-scoped encrypted HF credentials; secrets never enter jobs. v0.7.0 / Unclecow."""
from __future__ import annotations
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import threading
import urllib.request
from urllib.parse import urlparse
import uuid


def _dpapi(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_=[('length',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    if decrypt:
        fn=crypt.CryptUnprotectData
        fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok=fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    else:
        fn=crypt.CryptProtectData
        fn.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok=fn(ctypes.byref(source),'BuildSomething Hugging Face',None,None,None,1,ctypes.byref(target))
    if not ok:raise RuntimeError('无法使用当前 Windows 用户加密凭证，请重新填写授权令牌。')
    try:return ctypes.string_at(target.data,target.length)
    finally:kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))


class CredentialStore:
    def __init__(self,root):
        self.path=Path(root).resolve()/'logs/setup/credentials.dpapi'
        self.lock=threading.RLock()
        self._token=''
        if os.name=='nt' and self.path.is_file():
            try:
                value=json.loads(self.path.read_text(encoding='utf-8'))
                self._token=_dpapi(base64.b64decode(value['ciphertext'],validate=True),True).decode('utf-8')
                if not re.fullmatch(r'hf_[A-Za-z0-9]{16,256}',self._token):self._token=''
            except (ValueError,KeyError,OSError,RuntimeError):
                self._token=''

    def configure(self,payload):
        if not isinstance(payload,dict) or payload.get('provider')!='huggingface':
            raise ValueError('目前授权提供方仅支持 Hugging Face。')
        token=payload.get('token')
        if not isinstance(token,str) or token and not re.fullmatch(r'hf_[A-Za-z0-9]{16,256}',token):
            raise ValueError('请填写 Hugging Face 的 hf_ 只读令牌；留空可清除。')
        with self.lock:
            if token and os.name=='nt':
                encrypted=_dpapi(token.encode('utf-8'))
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temporary=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
                temporary.write_text(json.dumps({'format':'windows-user-dpapi','ciphertext':base64.b64encode(encrypted).decode('ascii')}),encoding='utf-8')
                temporary.replace(self.path)
            elif not token:
                self.path.unlink(missing_ok=True)
            self._token=token
            return {'configured':bool(token)}

    def public(self):
        with self.lock:return {'huggingface':{'configured':bool(self._token)}}

    def headers(self,url):
        parsed=urlparse(url)
        with self.lock:
            return {'Authorization':'Bearer '+self._token} if self._token and parsed.scheme=='https' and parsed.hostname=='huggingface.co' else {}

    def redact(self,text):
        with self.lock:
            return str(text).replace(self._token,'[redacted]') if self._token else str(text)


class ScopedAuthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        result=super().redirect_request(req,fp,code,msg,headers,newurl)
        if result is not None and (urlparse(newurl).scheme!='https' or urlparse(newurl).hostname!='huggingface.co'):
            result.remove_header('Authorization')
        return result
