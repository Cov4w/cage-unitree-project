import hashlib
import json
import random
import requests
import time
import sys
import os
import jwt
from dotenv import load_dotenv  # 추가
from Crypto.PublicKey import RSA
from .unitree_auth import make_remote_request
from .encryption import rsa_encrypt, rsa_load_public_key, aes_decrypt, generate_aes_key

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

def _generate_md5(string: str) -> str:
    md5_hash = hashlib.md5(string.encode())
    return md5_hash.hexdigest()

def generate_uuid():
    def replace_char(char):
        rand = random.randint(0, 15)
        if char == "x":
            return format(rand, 'x')
        elif char == "y":
            return format((rand & 0x3) | 0x8, 'x')

    uuid_template = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx"
    return ''.join(replace_char(char) if char in 'xy' else char for char in uuid_template)

def get_nested_field(message, *fields):
    current_level = message
    for field in fields:
        if isinstance(current_level, dict) and field in current_level:
            current_level = current_level[field]
        else:
            return None
    return current_level

def fetch_token(email: str, password: str) -> str:
    path = "login/email"
    body = {
        'email': email,
        'password': _generate_md5(password)
    }
    response = make_remote_request(path, body, token="", method="POST")
    if response.get("code") == 100:
        data = response.get("data")
        access_token = data.get("accessToken")
        return access_token
    else:
        return None

def fetch_public_key() -> RSA.RsaKey:
    path = "system/pubKey"
    try:
        response = make_remote_request(path, {}, token="", method="GET")
        if response.get("code") == 100:
            public_key_pem = response.get("data")
            return rsa_load_public_key(public_key_pem)
        else:
            return None
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.RequestException:
        return None

def fetch_turn_server_info(serial: str, access_token: str, public_key: RSA.RsaKey) -> dict:
    aes_key = generate_aes_key()
    path = "webrtc/account"
    body = {
        "sn": serial,
        "sk": rsa_encrypt(aes_key, public_key)
    }
    response = make_remote_request(path, body, token=access_token, method="POST")
    if response.get("code") == 100:
        return json.loads(aes_decrypt(response['data'], aes_key))
    else:
        return None

def print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    print(f"🕒 {status_type:<25}: {status_message:<15} ({current_time})")

TOKEN_FILE = os.path.expanduser("~/cage/cage-unitree-project/.unitree_token")

class TokenManager:
    def __init__(self):
        self.email = os.getenv("UNITREE_USERNAME")
        self.password = os.getenv("UNITREE_PASSWORD")
        self.token = self._load_token()
        if self.token:
            try:
                payload = jwt.decode(self.token, options={"verify_signature": False})
                exp = payload.get("exp", 0)
                now = time.time()
                remain = exp - now
                if remain > 0:
                    print(f"[TokenManager] .unitree_token 파일에서 토큰을 정상적으로 불러왔습니다.")
                    print(f"[TokenManager] 토큰 만료까지 남은 시간: {int(remain)}초 ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))} 만료)")
                else:
                    print(f"[TokenManager] 불러온 토큰이 이미 만료되었습니다. ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))} 만료)")
                    self._delete_token()
                    self.token = None
            except Exception as e:
                print(f"[TokenManager] 토큰 파싱 실패: {e}")
                self._delete_token()
                self.token = None
        else:
            print("[TokenManager] .unitree_token 파일에서 토큰을 불러오지 못했습니다.")

    def _load_token(self):
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "r") as f:
                return f.read().strip()
        return None

    def _save_token(self, token):
        with open(TOKEN_FILE, "w") as f:
            f.write(token)

    def _delete_token(self):
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        self.token = None

    def is_expired(self):
        if not self.token:
            return True
        try:
            payload = jwt.decode(self.token, options={"verify_signature": False})
            exp = payload.get("exp", 0)
            if time.time() > exp - 60:
                return True
            return False
        except Exception:
            return True

    def fetch_token(self):
        token = fetch_token(self.email, self.password)
        if token:
            self.token = token
            self._save_token(token)
        return self.token

    def get_token(self):
        # 토큰이 없거나 만료되었으면 새로 발급
        if not self.token or self.is_expired():
            print("[TokenManager] 토큰을 새로 발급받습니다.")
            self.fetch_token()
        return self.token


