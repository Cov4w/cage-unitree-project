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

# 동적 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
TOKEN_FILE = os.path.join(PROJECT_ROOT, ".unitree_token")

class TokenManager:
    def __init__(self):
        self.email = os.getenv("UNITREE_USERNAME")
        self.password = os.getenv("UNITREE_PASSWORD")
        
        # 필수 환경변수 검증
        if not self.email or not self.password:
            print("[TokenManager] ❌ UNITREE_USERNAME 또는 UNITREE_PASSWORD가 설정되지 않았습니다.")
            print("[TokenManager] .env 파일을 확인하세요.")
            self.token = None
            return
        
        print(f"[TokenManager] 토큰 파일 경로: {TOKEN_FILE}")
        self.token = self._load_token()
        
        if self.token:
            try:
                payload = jwt.decode(self.token, options={"verify_signature": False})
                exp = payload.get("exp", 0)
                now = time.time()
                remain = exp - now
                if remain > 60:  # 1분 이상 남은 경우
                    print(f"[TokenManager] ✅ 유효한 토큰 로드 완료")
                    print(f"[TokenManager] 남은 시간: {int(remain)}초 ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))} 만료)")
                else:
                    print(f"[TokenManager] ⚠️ 토큰 만료 임박 또는 만료됨")
                    self._delete_token()
                    self.token = None
            except Exception as e:
                print(f"[TokenManager] ❌ 토큰 파싱 실패: {e}")
                self._delete_token()
                self.token = None
        else:
            print("[TokenManager] ℹ️ 기존 토큰 없음 - 필요시 새로 발급")

    def _load_token(self):
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, "r") as f:
                    token = f.read().strip()
                return token if token else None
            except Exception as e:
                print(f"[TokenManager] ❌ 토큰 파일 읽기 실패: {e}")
                return None
        return None

    def _save_token(self, token):
        try:
            # 디렉터리가 없으면 생성
            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            with open(TOKEN_FILE, "w") as f:
                f.write(token)
            print(f"[TokenManager] 토큰 저장 완료: {TOKEN_FILE}")
        except Exception as e:
            print(f"[TokenManager] ❌ 토큰 저장 실패: {e}")

    def _delete_token(self):
        try:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
                print("[TokenManager] 기존 토큰 파일 삭제됨")
        except Exception as e:
            print(f"[TokenManager] ❌ 토큰 삭제 실패: {e}")
        self.token = None

    def is_expired(self):
        if not self.token:
            return True
        try:
            payload = jwt.decode(self.token, options={"verify_signature": False})
            exp = payload.get("exp", 0)
            # 1분 여유를 두고 만료 판단
            return time.time() > (exp - 60)
        except Exception:
            return True

    def fetch_token(self):
        try:
            if not self.email or not self.password:
                print("[TokenManager] ❌ 이메일 또는 비밀번호가 설정되지 않았습니다.")
                return None
                
            print(f"[TokenManager] 🔄 새 토큰 발급 시도... (사용자: {self.email})")
            token = fetch_token(self.email, self.password)
            
            if token:
                self.token = token
                self._save_token(token)
                print("[TokenManager] ✅ 새 토큰 발급 및 저장 완료")
                
                # 만료 시간 출력
                try:
                    payload = jwt.decode(token, options={"verify_signature": False})
                    exp = payload.get("exp", 0)
                    exp_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))
                    print(f"[TokenManager] 토큰 만료 시간: {exp_time}")
                except:
                    pass
                    
                return token
            else:
                print("[TokenManager] ❌ 토큰 발급 실패 - 인증 정보를 확인하세요.")
                return None
                
        except Exception as e:
            print(f"[TokenManager] ❌ 토큰 발급 중 오류 발생: {e}")
            return None

    def get_token(self):
        # 토큰이 없거나 만료된 경우 자동 갱신
        if not self.token or self.is_expired():
            print("[TokenManager] 토큰 갱신 필요")
            new_token = self.fetch_token()
            if new_token:
                return new_token
            else:
                print("[TokenManager] ❌ 토큰 갱신 실패")
                return None
        return self.token


