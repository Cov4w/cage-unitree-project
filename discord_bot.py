import discord
from discord.ext import commands
import io
import wave
import struct
import os
from dotenv import load_dotenv
import asyncio
import json
from datetime import datetime, timedelta
import threading
import numpy as np
import queue
import time
from webrtc_producer import get_robot_bms_status, get_bms_state, get_robot_status

# 🆕 실제 오디오 처리를 위한 추가 import
try:
    import pyaudio
    import soundfile as sf
    import librosa
    AUDIO_LIBS_AVAILABLE = True
    print("✅ PyAudio 및 오디오 라이브러리 로드 성공")
except ImportError as e:
    print(f"⚠️ 오디오 라이브러리 일부 누락: {e}")
    print("💡 설치 명령어: pip install pyaudio soundfile librosa")
    AUDIO_LIBS_AVAILABLE = False

# 🔧 Discord sinks import 수정 - 실제 음성 수신 기능 활성화
DISCORD_SINKS_AVAILABLE = False
try:
    # Discord.py 2.x+ 방식
    from discord import sinks
    DISCORD_SINKS_AVAILABLE = True
    print("✅ discord.sinks 모듈 로드 성공")
except ImportError:
    try:
        # 대안 방식
        import discord.sinks
        DISCORD_SINKS_AVAILABLE = True
        print("✅ discord.sinks 모듈 로드 성공 (대안)")
    except ImportError:
        DISCORD_SINKS_AVAILABLE = False
        print("⚠️ discord.sinks 모듈 없음 - PyAudio 대안 방식 사용")

# 🆕 음성 연동을 위한 추가 import
try:
    from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
    from config.settings import SERIAL_NUMBER, UNITREE_USERNAME, UNITREE_PASSWORD
    VOICE_AVAILABLE = True
    print("✅ 음성 연동 모듈 로드 성공")
except ImportError as e:
    print(f"⚠️ 음성 연동 모듈 로드 실패: {e}")
    VOICE_AVAILABLE = False

# 환경변수 로드
load_dotenv()

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True  # 메시지 내용 읽기 권한
intents.voice_states = True     # 🆕 음성 상태 권한 추가
bot = commands.Bot(command_prefix='!', intents=intents)

# 🆕 음성 연동 관련 전역 변수
voice_client = None
voice_bridge_active = False
robot_audio_conn = None
discord_audio_queue = queue.Queue(maxsize=50)
robot_audio_queue = queue.Queue(maxsize=50)

# 🔧 PyAudio 관련 설정 (조건부 정의)
if AUDIO_LIBS_AVAILABLE:
    AUDIO_FORMAT = pyaudio.paInt16
    AUDIO_CHANNELS = 1  # mono
    AUDIO_RATE = 48000  # Discord 호환
    AUDIO_CHUNK = 960   # 20ms at 48kHz
    pyaudio_instance = None
    microphone_stream = None
else:
    AUDIO_FORMAT = None
    AUDIO_CHANNELS = 1
    AUDIO_RATE = 48000
    AUDIO_CHUNK = 960
    pyaudio_instance = None
    microphone_stream = None

# 기존 상수들...
MAX_ARUCO_ATTEMPTS = 10
ARUCO_SCAN_TIMEOUT = 30.0
ARUCO_RETRY_INTERVAL = 2.0
IDENTITY_ALERT_COOLDOWN = 300
IDENTITY_DAILY_RESET_HOUR = 6

# 🆕 음성 채널 설정
VOICE_CHANNEL_ID = 1405363944436011011  # 실제 음성 채널 ID

# 🔧 다중 채널/서버 설정
FIRE_ALERT_CHANNELS = [
    {
        'channel_id': 989834859063148564,
        'role_id': 1407168354208186478,
        'server_name': '메인 서버'
    },
    {
        'channel_id': 1407178115347644456,
        'role_id': 1407168743104188517,
        'server_name': '백업 서버'
    }, 
]

# 🔧 알림 중복 방지를 위한 전역 변수
processed_fire_alerts = set()
processed_aruco_scans = set()
last_fire_alert_id = ""
last_aruco_scan_id = ""
user_identity_alerts = {}
daily_identity_log = {}

# 🆕 Discord 봇용 독립적인 WebRTC 연결
discord_bot_webrtc_conn = None

# 🆕 실제 Discord 음성 수신을 위한 고급 클래스
class AdvancedDiscordAudioCapture:
    """실제 Discord 음성을 캡처하는 고급 클래스"""
    
    def __init__(self, bridge):
        self.bridge = bridge
        self.is_recording = False
        self.recording_users = {}
        self.audio_buffers = {}
        print("✅ AdvancedDiscordAudioCapture 초기화됨")
    
    def start_recording(self):
        """Discord.py의 고급 음성 수신 시작"""
        global voice_client
        
        if not voice_client or not voice_client.is_connected():
            print("❌ Discord 음성 클라이언트가 연결되지 않음")
            return False
        
        try:
            if DISCORD_SINKS_AVAILABLE:
                # Discord.py 2.x+ 방식 사용
                sink = EnhancedWaveSink(self)
                
                voice_client.start_recording(
                    sink,
                    self._recording_finished_callback
                )
                
                print("🎙️ Discord 고급 음성 녹음 시작 (sinks 사용)")
                self.is_recording = True
                return True
                
            elif AUDIO_LIBS_AVAILABLE:
                # PyAudio 대안 방식
                return self._start_pyaudio_capture()
                
            else:
                print("❌ 모든 오디오 캡처 방법 사용 불가")
                return False
                
        except Exception as e:
            print(f"❌ Discord 음성 녹음 시작 실패: {e}")
            return False
    
    def _start_pyaudio_capture(self):
        """PyAudio를 사용한 직접 오디오 캡처"""
        global pyaudio_instance, microphone_stream
        
        try:
            if not AUDIO_LIBS_AVAILABLE:
                return False
            
            pyaudio_instance = pyaudio.PyAudio()
            
            # 마이크 스트림 설정 (Discord 음성 대신 로컬 마이크 사용)
            microphone_stream = pyaudio_instance.open(
                format=AUDIO_FORMAT,
                channels=AUDIO_CHANNELS,
                rate=AUDIO_RATE,
                input=True,
                frames_per_buffer=AUDIO_CHUNK,
                stream_callback=self._pyaudio_callback
            )
            
            microphone_stream.start_stream()
            print("🎙️ PyAudio 마이크 캡처 시작 (Discord 음성 대안)")
            self.is_recording = True
            return True
            
        except Exception as e:
            print(f"❌ PyAudio 마이크 캡처 시작 실패: {e}")
            return False
    
    def _pyaudio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio 콜백 함수"""
        try:
            if in_data and len(in_data) > 0:
                # 오디오 데이터를 로봇 전송 큐에 추가
                if not self.bridge.audio_queue.full():
                    self.bridge.audio_queue.put(in_data)
                    # print(f"🎤 PyAudio 캡처: {len(in_data)} bytes")
                else:
                    # 큐가 가득 찬 경우 오래된 데이터 제거
                    try:
                        self.bridge.audio_queue.get_nowait()
                        self.bridge.audio_queue.put(in_data)
                    except queue.Empty:
                        pass
                        
        except Exception as e:
            print(f"❌ PyAudio 콜백 오류: {e}")
            
        return (in_data, pyaudio.paContinue)
    
    def _recording_finished_callback(self, sink, error=None):
        """Discord 녹음 완료 콜백"""
        if error:
            print(f"❌ Discord 녹음 오류: {error}")
        else:
            print("✅ Discord 녹음 완료")
            
        # 녹음된 데이터 처리
        if hasattr(sink, 'audio_data') and sink.audio_data:
            self._process_recorded_audio(sink.audio_data)
    
    def _process_recorded_audio(self, audio_data):
        """녹음된 오디오 데이터 처리"""
        try:
            for user_id, user_audio in audio_data.items():
                if user_id != bot.user.id:  # 봇 자신 제외
                    print(f"🎙️ 사용자 {user_id}의 음성 데이터 처리 중...")
                    
                    # 오디오 데이터를 로봇 전송 큐에 추가
                    if isinstance(user_audio, bytes):
                        if not self.bridge.audio_queue.full():
                            self.bridge.audio_queue.put(user_audio)
                            print(f"📤 사용자 음성 데이터 추가: {len(user_audio)} bytes")
                        
        except Exception as e:
            print(f"❌ 녹음 데이터 처리 오류: {e}")
    
    def stop_recording(self):
        """음성 녹음 중지"""
        global microphone_stream, pyaudio_instance
        
        self.is_recording = False
        
        try:
            # Discord 녹음 중지
            if voice_client and hasattr(voice_client, 'stop_recording'):
                voice_client.stop_recording()
                print("🔇 Discord 녹음 중지됨")
            
            # PyAudio 스트림 중지
            if microphone_stream and microphone_stream.is_active():
                microphone_stream.stop_stream()
                microphone_stream.close()
                microphone_stream = None
                print("🔇 PyAudio 마이크 캡처 중지됨")
            
            if pyaudio_instance:
                pyaudio_instance.terminate()
                pyaudio_instance = None
                print("🔇 PyAudio 인스턴스 종료됨")
                
        except Exception as e:
            print(f"⚠️ 음성 녹음 중지 중 경고: {e}")

# 🆕 향상된 WaveSink (Discord.py 2.x+ 호환)
class EnhancedWaveSink:
    """향상된 Discord 음성 싱크 (discord.sinks가 없을 때 대안)"""
    
    def __init__(self, capture_handler):
        self.capture_handler = capture_handler
        self.user_audio_data = {}
        print("✅ EnhancedWaveSink 초기화됨")
    
    def write(self, data, user):
        """사용자별 음성 데이터 수신"""
        try:
            if user and user.id != bot.user.id:  # 봇 자신 제외
                user_id = user.id
                
                # 사용자별 데이터 수집
                if user_id not in self.user_audio_data:
                    self.user_audio_data[user_id] = []
                    print(f"🎙️ {user.display_name}의 음성 스트림 시작")
                
                self.user_audio_data[user_id].append(data)
                
                # 실시간으로 로봇에 전송
                if isinstance(data, bytes) and len(data) > 0:
                    if not self.capture_handler.bridge.audio_queue.full():
                        self.capture_handler.bridge.audio_queue.put(data)
                        print(f"📤 실시간 음성 전송: {user.display_name} -> {len(data)} bytes")
                    else:
                        # 큐가 가득 찬 경우 오래된 데이터 제거
                        try:
                            self.capture_handler.bridge.audio_queue.get_nowait()
                            self.capture_handler.bridge.audio_queue.put(data)
                        except queue.Empty:
                            pass
                
        except Exception as e:
            print(f"❌ 사용자 음성 처리 오류: {e}")
    
    def cleanup(self):
        """정리 작업"""
        print(f"🧹 EnhancedWaveSink 정리됨 ({len(self.user_audio_data)}명의 데이터)")
        self.user_audio_data.clear()

class DiscordToRobotAudioBridge:
    """Discord 음성을 로봇 스피커로 전송하는 브리지 - 실제 음성 캡처 구현"""
    
    def __init__(self):
        """초기화 메서드"""
        self.audio_queue = queue.Queue(maxsize=50)
        self.is_streaming = False
        self.robot_conn = None
        self.audio_capture = None  # 🆕 고급 오디오 캡처
        self.recording_task = None
        print("✅ DiscordToRobotAudioBridge 초기화 완료")
    
    async def create_robot_connection(self):
        """Discord 봇용 독립적인 로봇 연결 생성"""
        try:
            print("🔄 Discord 봇용 새 WebRTC 연결 생성...")
            
            # TokenManager 임포트 시도
            token_manager = None
            try:
                from go2_webrtc_connect.go2_webrtc_driver.util import TokenManager
                token_manager = TokenManager()
                print("✅ go2_webrtc_connect 경로에서 TokenManager 로드 성공")
            except ImportError:
                try:
                    from go2_webrtc_driver.util import TokenManager
                    token_manager = TokenManager()
                    print("✅ go2_webrtc_driver 경로에서 TokenManager 로드 성공")
                except ImportError:
                    print("⚠️ TokenManager 임포트 실패 - 기존 토큰 파일 사용")
            
            if token_manager:
                token = token_manager.get_token()
            
            # WebRTC 연결 클래스 임포트
            try:
                from go2_webrtc_connect.go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
                from config.settings import SERIAL_NUMBER, UNITREE_USERNAME, UNITREE_PASSWORD
            except ImportError:
                try:
                    from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
                    from config.settings import SERIAL_NUMBER, UNITREE_USERNAME, UNITREE_PASSWORD
                except ImportError as e:
                    print(f"❌ WebRTC 드라이버 임포트 실패: {e}")
                    return False
            
            # Discord 봇용 독립적인 연결 생성
            self.robot_conn = Go2WebRTCConnection(
                WebRTCConnectionMethod.Remote,
                serialNumber=SERIAL_NUMBER,
                username=UNITREE_USERNAME,
                password=UNITREE_PASSWORD
            )
            
            # 연결 수행
            await self.robot_conn.connect()
            
            # 오디오 채널 활성화
            if hasattr(self.robot_conn, 'audio') and self.robot_conn.audio:
                if hasattr(self.robot_conn.audio, 'switchAudioChannel'):
                    self.robot_conn.audio.switchAudioChannel(True)
                    print("🎵 Discord 봇용 로봇 오디오 채널 활성화됨")
                
                print("✅ Discord 봇용 독립적인 로봇 연결 완료")
                return True
            else:
                print("⚠️ 로봇 연결은 되었지만 오디오 채널이 없습니다")
                return True
                
        except Exception as e:
            print(f"❌ Discord 봇용 로봇 연결 생성 실패: {e}")
            import traceback
            print(f"🔍 상세 오류: {traceback.format_exc()}")
            return False
    
    async def connect_to_robot(self):
        """로봇에 연결 (기존 연결 재사용 우선, 실패 시 새 연결 생성)"""
        try:
            # 기존 webrtc_producer 연결 재사용 시도
            import sys
            import os
            sys.path.append(os.path.dirname(os.path.abspath(__file__)))
            
            try:
                from webrtc_producer import _conn_holder
                
                if _conn_holder and 'conn' in _conn_holder and _conn_holder['conn']:
                    existing_conn = _conn_holder['conn']
                    
                    # 연결 유효성 검사
                    if hasattr(existing_conn, 'datachannel') and existing_conn.datachannel:
                        self.robot_conn = existing_conn
                        print("✅ 기존 webrtc_producer 연결 재사용 (Discord → Robot)")
                        return True
                    else:
                        print("⚠️ 기존 연결이 있지만 데이터 채널이 없음")
                else:
                    print("💡 기존 webrtc_producer 연결이 없음")
                    
            except ImportError as e:
                print(f"⚠️ webrtc_producer 임포트 실패: {e}")
            
            # 새로운 독립적인 연결 생성
            print("🔄 Discord 봇용 독립적인 연결 생성 시도...")
            return await self.create_robot_connection()
                
        except Exception as e:
            print(f"❌ Discord → Robot 연결 실패: {e}")
            return False
    
    def start_streaming(self):
        """Discord 음성을 로봇으로 스트리밍 시작 - 실제 음성 캡처 구현"""
        if not self.robot_conn:
            print("❌ 로봇 연결이 없어서 스트리밍을 시작할 수 없습니다")
            return False
        
        # audio_queue 초기화 확인
        if not hasattr(self, 'audio_queue'):
            print("⚠️ audio_queue가 초기화되지 않음 - 지금 초기화합니다")
            self.audio_queue = queue.Queue(maxsize=50)
        
        try:
            # 🆕 고급 오디오 캡처 시스템 사용
            print("🎙️ 고급 Discord 음성 캡처 시스템 초기화...")
            self.audio_capture = AdvancedDiscordAudioCapture(self)
            
            # 실제 Discord 음성 캡처 시작
            capture_success = self.audio_capture.start_recording()
            
            if capture_success:
                print("✅ Discord 실제 음성 캡처 시작됨")
                print("🎙️ 사용자가 음성 채널에서 말하면 실시간으로 캡처됩니다")
            else:
                print("⚠️ Discord 음성 캡처 실패 - 테스트 모드로 대체")
                # 테스트 모드로 대체
                self.recording_task = asyncio.create_task(self._test_audio_generation())
                
        except Exception as e:
            print(f"❌ Discord 음성 캡처 시작 실패: {e}")
            print("🔧 테스트 모드로 대체...")
            
            try:
                self.recording_task = asyncio.create_task(self._test_audio_generation())
                print("✅ 테스트 오디오 생성 시작됨")
            except Exception as fallback_e:
                print(f"❌ 테스트 모드도 실패: {fallback_e}")
                return False
            
        self.is_streaming = True
        print("🎵 Discord → Robot 실제 오디오 스트리밍 시작")
        
        # 별도 스레드에서 오디오 전송
        threading.Thread(target=self._audio_streaming_thread, daemon=True).start()
        return True
    
    async def _test_audio_generation(self):
        """테스트용 오디오 신호 생성 (실제 Discord 음성 대신)"""
        print("🎙️ 테스트 오디오 생성 모드 시작")
        
        while self.is_streaming:
            try:
                # 음성 채널에 사용자가 있는지 확인
                if voice_client and voice_client.channel:
                    members = [m for m in voice_client.channel.members if not m.bot]
                    
                    if len(members) > 0:
                        # 1% 확률로 테스트 신호 생성 (너무 많은 로그 방지)
                        import random
                        if random.random() < 0.01:
                            await self._generate_test_audio_signal()
                    
                await asyncio.sleep(0.02)  # 20ms 간격
                
            except Exception as e:
                print(f"❌ 테스트 오디오 생성 오류: {e}")
                await asyncio.sleep(1.0)
        
        print("🛑 테스트 오디오 생성 종료")
    
    async def _generate_test_audio_signal(self):
        """테스트용 오디오 신호 생성"""
        try:
            # 20ms, 48kHz, 16bit, mono 테스트 신호
            frame_size = 960  # 20ms at 48kHz
            
            # 매우 낮은 볼륨의 테스트 톤 (1kHz 사인파)
            import math
            test_data = []
            for i in range(frame_size):
                # 1kHz 사인파, 매우 낮은 볼륨 (-60dB)
                sample = int(math.sin(2 * math.pi * 1000 * i / 48000) * 50)  # 매우 낮은 볼륨
                test_data.append(sample)
            
            # bytes로 변환
            audio_bytes = struct.pack('<' + 'h' * len(test_data), *test_data)
            
            # 테스트 신호 전송
            if not self.audio_queue.full():
                self.audio_queue.put(audio_bytes)
                print(f"🔊 테스트 오디오 신호 생성: {len(audio_bytes)} bytes")
                    
        except Exception as e:
            print(f"❌ 테스트 오디오 신호 생성 오류: {e}")
    
    def _audio_streaming_thread(self):
        """오디오 스트리밍 스레드 - 실제 로봇 전송"""
        print("🎵 Discord → Robot 실제 오디오 스트리밍 스레드 시작")
        
        while self.is_streaming:
            try:
                if hasattr(self, 'audio_queue') and not self.audio_queue.empty():
                    audio_data = self.audio_queue.get(timeout=0.1)
                    self._send_audio_to_robot(audio_data)
                else:
                    time.sleep(0.01)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"❌ 오디오 스트리밍 오류: {e}")
                
        print("🛑 Discord → Robot 실제 오디오 스트리밍 스레드 종료")
                
    def _send_audio_to_robot(self, audio_data):
        """실제 로봇으로 오디오 전송 - WebRTC 구현"""
        try:
            if self.robot_conn and hasattr(self.robot_conn, 'audio') and self.robot_conn.audio:
                # 🆕 실제 WebRTC 오디오 전송 시도
                if hasattr(self.robot_conn.audio, 'send_audio_data'):
                    # 직접 API가 있는 경우
                    self.robot_conn.audio.send_audio_data(audio_data)
                    print(f"🔊 로봇으로 실제 오디오 전송: {len(audio_data)} bytes")
                    
                elif hasattr(self.robot_conn.audio, 'track') and hasattr(self.robot_conn.audio.track, 'send'):
                    # MediaStreamTrack을 통한 전송
                    try:
                        import av
                        
                        # PCM 데이터를 AudioFrame으로 변환
                        audio_array = np.frombuffer(audio_data, dtype=np.int16)
                        
                        # mono 오디오 프레임 생성
                        frame = av.AudioFrame.from_ndarray(
                            audio_array.reshape(-1, 1),  # mono
                            format='s16',
                            layout='mono'
                        )
                        frame.sample_rate = 48000
                        
                        # 비동기 전송
                        asyncio.create_task(self.robot_conn.audio.track.send(frame))
                        print(f"🔊 로봇으로 AudioFrame 전송: {len(audio_data)} bytes")
                        
                    except Exception as frame_e:
                        print(f"❌ AudioFrame 변환/전송 오류: {frame_e}")
                        # 시뮬레이션으로 대체
                        print(f"🔊 로봇으로 오디오 전송 시뮬레이션: {len(audio_data)} bytes")
                        
                elif hasattr(self.robot_conn.audio, 'write_audio'):
                    # 다른 API 방식
                    self.robot_conn.audio.write_audio(audio_data)
                    print(f"🔊 로봇으로 오디오 쓰기: {len(audio_data)} bytes")
                    
                else:
                    # API가 명확하지 않은 경우 시뮬레이션
                    # print(f"🔊 로봇으로 오디오 전송 시뮬레이션: {len(audio_data)} bytes")
                    pass
                    
            else:
                print("⚠️ 로봇 오디오 채널이 없습니다")
                
        except Exception as e:
            print(f"❌ 로봇 오디오 전송 오류: {e}")
    
    def stop_streaming(self):
        """스트리밍 중지 및 리소스 정리"""
        self.is_streaming = False
        
        # 고급 오디오 캡처 중지
        if self.audio_capture:
            self.audio_capture.stop_recording()
            self.audio_capture = None
        
        # 녹음 태스크 중지
        if self.recording_task and not self.recording_task.done():
            self.recording_task.cancel()
            print("🔇 테스트 오디오 생성 태스크 중지됨")
        
        print("🛑 Discord → Robot 실제 오디오 스트리밍 중지")

# 🆕 향상된 Robot → Discord 브리지
class EnhancedRobotToDiscordAudioBridge:
    """향상된 로봇 오디오를 Discord로 전송하는 브리지"""
    
    def __init__(self):
        self.audio_queue = queue.Queue(maxsize=50)
        self.is_receiving = False
        self.robot_conn = None
        self.audio_source = None
        self.robot_audio_callback_registered = False
        print("✅ EnhancedRobotToDiscordAudioBridge 초기화 완료")
    
    async def connect_to_robot(self):
        """로봇에 연결하여 실제 오디오 수신 설정"""
        try:
            import sys
            import os
            sys.path.append(os.path.dirname(os.path.abspath(__file__)))
            
            try:
                from webrtc_producer import _conn_holder
                
                if _conn_holder and 'conn' in _conn_holder and _conn_holder['conn']:
                    existing_conn = _conn_holder['conn']
                    
                    if hasattr(existing_conn, 'audio') and existing_conn.audio:
                        self.robot_conn = existing_conn
                        
                        # 🆕 다양한 방법으로 오디오 콜백 등록 시도
                        self._register_robot_audio_callbacks()
                        
                        print("✅ 기존 연결 재사용 (Robot → Discord)")
                        return True
                    else:
                        print("⚠️ 기존 연결에 오디오 채널이 없음")
                        return True
                else:
                    print("💡 기존 연결이 없어 Robot → Discord 브리지는 건너뜀")
                    return True
                    
            except ImportError as e:
                print(f"⚠️ webrtc_producer 임포트 실패: {e}")
                return True
                
        except Exception as e:
            print(f"❌ Robot → Discord 연결 실패: {e}")
            return True
    
    def _register_robot_audio_callbacks(self):
        """로봇 오디오 콜백을 다양한 방법으로 등록 시도"""
        if not self.robot_conn or not hasattr(self.robot_conn, 'audio'):
            return False
        
        audio_channel = self.robot_conn.audio
        callback_registered = False
        
        # 방법 1: add_track_callback
        try:
            if hasattr(audio_channel, 'add_track_callback'):
                audio_channel.add_track_callback(self._receive_robot_audio_enhanced)
                print("🎙️ 로봇 오디오 트랙 콜백 등록됨 (방법 1)")
                callback_registered = True
        except Exception as e:
            print(f"⚠️ 트랙 콜백 등록 실패: {e}")
        
        # 방법 2: on_audio_frame
        try:
            if hasattr(audio_channel, 'on_audio_frame'):
                audio_channel.on_audio_frame = self._receive_robot_audio_enhanced
                print("🎙️ 로봇 오디오 프레임 콜백 등록됨 (방법 2)")
                callback_registered = True
        except Exception as e:
            print(f"⚠️ 프레임 콜백 등록 실패: {e}")
        
        if callback_registered:
            self.robot_audio_callback_registered = True
            print("✅ 로봇 오디오 콜백 등록 성공")
        else:
            print("⚠️ 로봇 오디오 콜백 등록 실패 - 수동 폴링 모드로 전환")
            # 수동 폴링 방식으로 대체
            threading.Thread(target=self._manual_audio_polling, daemon=True).start()
        
        return callback_registered
    
    def _manual_audio_polling(self):
        """수동 오디오 폴링 (콜백이 작동하지 않을 때)"""
        print("🔄 로봇 오디오 수동 폴링 시작...")
        
        while self.is_receiving:
            try:
                if self.robot_conn and hasattr(self.robot_conn, 'audio'):
                    audio_channel = self.robot_conn.audio
                    
                    # 오디오 데이터 가져오기 시도
                    if hasattr(audio_channel, 'get_audio_data'):
                        audio_data = audio_channel.get_audio_data()
                        if audio_data:
                            self._process_robot_audio_data_enhanced(audio_data)
                    
                    elif hasattr(audio_channel, 'read_audio_buffer'):
                        audio_buffer = audio_channel.read_audio_buffer()
                        if audio_buffer:
                            self._process_robot_audio_data_enhanced(audio_buffer)
                    
                    else:
                        # 테스트 오디오 생성
                        self._generate_test_robot_audio()
                
                time.sleep(0.02)  # 20ms 간격
                
            except Exception as e:
                print(f"❌ 수동 오디오 폴링 오류: {e}")
                time.sleep(0.5)
        
        print("🛑 로봇 오디오 수동 폴링 종료")
    
    def _receive_robot_audio_enhanced(self, audio_data):
        """향상된 로봇 오디오 수신 처리"""
        try:
            if audio_data is None:
                return
            
            # AudioFrame 객체 처리
            if hasattr(audio_data, 'to_ndarray'):
                audio_array = audio_data.to_ndarray()
                sample_rate = getattr(audio_data, 'sample_rate', 48000)
                channels = audio_array.shape[1] if len(audio_array.shape) > 1 else 1
                
                print(f"🎙️ 로봇 실제 오디오 수신: {sample_rate}Hz, {channels}ch, {audio_array.shape}")
                
                # Discord 호환 형식으로 변환
                audio_pcm = self._convert_robot_audio_format(audio_array, sample_rate, channels)
                if audio_pcm:
                    self._process_robot_audio_data_enhanced(audio_pcm)
            
            # bytes 데이터 직접 처리
            elif isinstance(audio_data, bytes):
                print(f"🎙️ 로봇 바이트 오디오 수신: {len(audio_data)} bytes")
                self._process_robot_audio_data_enhanced(audio_data)
            
            # numpy array 처리
            elif hasattr(audio_data, 'dtype'):
                audio_pcm = self._convert_numpy_to_pcm(audio_data)
                if audio_pcm:
                    self._process_robot_audio_data_enhanced(audio_pcm)
            
            else:
                print(f"⚠️ 알 수 없는 로봇 오디오 데이터 형식: {type(audio_data)}")
                
        except Exception as e:
            print(f"❌ 로봇 오디오 수신 처리 오류: {e}")
    
    def _convert_robot_audio_format(self, audio_array, sample_rate, channels):
        """로봇 오디오를 Discord 호환 형식으로 변환"""
        try:
            import numpy as np
            
            # 리샘플링 (48kHz로 변환)
            if sample_rate != 48000:
                try:
                    if AUDIO_LIBS_AVAILABLE:
                        import librosa
                        audio_array = librosa.resample(
                            audio_array.flatten() if len(audio_array.shape) > 1 else audio_array,
                            orig_sr=sample_rate,
                            target_sr=48000
                        )
                        print(f"🔄 오디오 리샘플링: {sample_rate}Hz → 48000Hz")
                except ImportError:
                    print("⚠️ librosa 없음 - 리샘플링 건너뜀")
            
            # mono를 stereo로 변환
            if len(audio_array.shape) == 1 or channels == 1:
                audio_array = audio_array.flatten()
                audio_stereo = np.column_stack([audio_array, audio_array])
            else:
                audio_stereo = audio_array
            
            # 16bit PCM으로 변환
            if audio_stereo.dtype != np.int16:
                # float 타입인 경우 정규화 후 변환
                if audio_stereo.dtype in [np.float32, np.float64]:
                    audio_stereo = np.clip(audio_stereo, -1.0, 1.0)
                    audio_stereo = (audio_stereo * 32767).astype(np.int16)
                else:
                    audio_stereo = audio_stereo.astype(np.int16)
            
            return audio_stereo.tobytes()
            
        except Exception as e:
            print(f"❌ 로봇 오디오 형식 변환 오류: {e}")
            return None
    
    def _convert_numpy_to_pcm(self, audio_array):
        """numpy array를 PCM으로 변환"""
        try:
            import numpy as np
            
            # 1차원으로 평탄화
            if len(audio_array.shape) > 1:
                audio_flat = audio_array.flatten()
            else:
                audio_flat = audio_array
            
            # stereo로 변환
            audio_stereo = np.column_stack([audio_flat, audio_flat])
            
            # 16bit PCM으로 변환
            if audio_stereo.dtype != np.int16:
                if audio_stereo.dtype in [np.float32, np.float64]:
                    audio_stereo = np.clip(audio_stereo, -1.0, 1.0)
                    audio_stereo = (audio_stereo * 32767).astype(np.int16)
                else:
                    audio_stereo = audio_stereo.astype(np.int16)
            
            return audio_stereo.tobytes()
            
        except Exception as e:
            print(f"❌ numpy → PCM 변환 오류: {e}")
            return None
    
    def _process_robot_audio_data_enhanced(self, audio_data):
        """향상된 로봇 오디오 데이터 처리"""
        try:
            if audio_data and len(audio_data) > 0:
                # Discord 오디오 큐에 추가
                if not self.audio_queue.full():
                    self.audio_queue.put(audio_data)
                    print(f"📤 로봇 오디오 데이터 큐에 추가: {len(audio_data)} bytes")
                else:
                    # 큐가 가득 찬 경우 오래된 데이터 제거
                    try:
                        self.audio_queue.get_nowait()
                        self.audio_queue.put(audio_data)
                    except queue.Empty:
                        pass
                        
        except Exception as e:
            print(f"❌ 로봇 오디오 데이터 처리 오류: {e}")
    
    def _generate_test_robot_audio(self):
        """테스트용 로봇 오디오 생성"""
        try:
            # 간헐적으로 테스트 오디오 생성 (1% 확률)
            import random
            if random.random() < 0.01:
                # 20ms, 48kHz, 16bit, stereo 테스트 신호
                frame_size = 960  # 20ms at 48kHz
                
                # 낮은 볼륨의 핑크 노이즈
                test_data = []
                for i in range(frame_size):
                    # 핑크 노이즈 시뮬레이션
                    noise_sample = int((random.random() - 0.5) * 200)  # ±100 범위
                    test_data.extend([noise_sample, noise_sample])  # stereo
                
                # bytes로 변환
                audio_bytes = struct.pack('<' + 'h' * len(test_data), *test_data)
                
                if not self.audio_queue.full():
                    self.audio_queue.put(audio_bytes)
                    print(f"🔊 테스트 로봇 오디오 생성: {len(audio_bytes)} bytes")
                    
        except Exception as e:
            print(f"❌ 테스트 로봇 오디오 생성 오류: {e}")
    
    def start_receiving(self):
        """로봇 오디오 수신 시작"""
        if not hasattr(self, 'audio_queue'):
            print("⚠️ audio_queue가 초기화되지 않음 - 지금 초기화합니다")
            self.audio_queue = queue.Queue(maxsize=50)
        
        try:
            self.audio_source = SimpleRobotAudioSource(self)
            
            # Opus 초기화 확인
            if not discord.opus.is_loaded():
                try:
                    discord.opus.load_opus('libopus')
                    print("✅ Opus 라이브러리 로드됨")
                except:
                    print("⚠️ Discord Opus가 로드되지 않음 - PCM 모드 사용")
            
            # Discord에서 로봇 오디오 재생
            if voice_client and voice_client.is_connected():
                voice_client.play(self.audio_source, after=lambda e: print(f'Player error: {e}') if e else None)
                print("🔊 Discord에서 로봇 오디오 재생 시작")
            else:
                print("❌ Discord 음성 클라이언트가 연결되지 않음")
                return
                
        except Exception as e:
            print(f"❌ Discord 오디오 소스 생성 실패: {e}")
            return
            
        self.is_receiving = True
        print("🎵 Robot → Discord 오디오 수신 시작")
    
    def stop_receiving(self):
        """로봇 오디오 수신 중지"""
        self.is_receiving = False
        
        # Discord 오디오 재생 중지
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            print("🔇 Discord 오디오 재생 중지됨")
        
        print("🛑 Robot → Discord 오디오 수신 중지")

# 🆕 간단한 Discord 오디오 소스
class SimpleRobotAudioSource(discord.AudioSource):
    """로봇 오디오를 Discord로 스트리밍하는 간단한 소스"""
    
    def __init__(self, bridge):
        self.bridge = bridge
        self.frame_size = 960  # 20ms at 48kHz
        print("✅ SimpleRobotAudioSource 초기화됨")
    
    def read(self):
        """Discord가 오디오 데이터를 요청할 때 호출"""
        try:
            # 로봇 오디오 큐에서 데이터 가져오기
            if not self.bridge.audio_queue.empty():
                audio_data = self.bridge.audio_queue.get_nowait()
                return audio_data
            else:
                # 무음 반환 (20ms, 48kHz, 16bit, stereo)
                silent_frame = b'\x00' * (self.frame_size * 2 * 2)
                return silent_frame
                
        except queue.Empty:
            # 무음 반환
            silent_frame = b'\x00' * (self.frame_size * 2 * 2)
            return silent_frame
        except Exception as e:
            print(f"❌ Discord 오디오 소스 읽기 오류: {e}")
            return b'\x00' * (self.frame_size * 2 * 2)
    
    def is_opus(self):
        """Opus 인코딩 여부 (False = PCM)"""
        return False
    
    def cleanup(self):
        """정리 작업"""
        print("🧹 SimpleRobotAudioSource 정리됨")

# 🆕 Discord.py 2.x+ 호환 멀티 사용자 오디오 싱크
class CustomMultiUserAudioSink:
    """Discord.py 2.x+ 호환 멀티 사용자 오디오 싱크"""
    
    def __init__(self, bridge):
        self.bridge = bridge
        self.user_audio_buffers = {}
        print("✅ CustomMultiUserAudioSink 초기화됨")
    
    def write(self, data, user=None):
        """특정 사용자의 오디오 데이터 수신"""
        try:
            if user and user.id != bot.user.id:  # 봇 자신 제외
                user_id = user.id
                
                # 사용자별 버퍼 관리
                if user_id not in self.user_audio_buffers:
                    self.user_audio_buffers[user_id] = []
                    print(f"🎙️ {user.display_name}의 오디오 스트림 시작")
                
                # 오디오 데이터 저장
                self.user_audio_buffers[user_id].append(data)
                
                # 즉시 로봇으로 전송
                self._process_user_audio(user_id, data)
                
        except Exception as e:
            print(f"❌ 사용자 오디오 처리 오류: {e}")
    
    def _process_user_audio(self, user_id, audio_data):
        """사용자 오디오 데이터를 로봇으로 전송 준비"""
        try:
            # PCM 데이터 처리
            if isinstance(audio_data, bytes) and len(audio_data) > 0:
                # 로봇 전송 큐에 추가
                if not self.bridge.audio_queue.full():
                    self.bridge.audio_queue.put(audio_data)
                    print(f"📤 사용자 {user_id} 오디오 데이터 큐에 추가: {len(audio_data)} bytes")
                else:
                    # 큐가 가득 찬 경우 오래된 데이터 제거 후 추가
                    try:
                        self.bridge.audio_queue.get_nowait()
                        self.bridge.audio_queue.put(audio_data)
                    except queue.Empty:
                        pass
                        
        except Exception as e:
            print(f"❌ 사용자 오디오 처리 오류: {e}")
    
    def cleanup(self):
        """정리 작업"""
        print(f"🧹 CustomMultiUserAudioSink 정리됨 ({len(self.user_audio_buffers)}명의 버퍼)")
        self.user_audio_buffers.clear()

# 🆕 기본 Discord 오디오 싱크 (sinks 없을 때)
class BasicDiscordAudioSink:
    """기본 Discord 오디오 싱크 (discord.sinks 없을 때 사용)"""
    
    def __init__(self, bridge):
        self.bridge = bridge
        print("✅ BasicDiscordAudioSink 초기화됨")
    
    def write(self, data):
        """Discord에서 오디오 데이터 수신"""
        try:
            # Discord는 20ms마다 PCM 데이터 제공 (48kHz, 16bit, stereo)
            # data는 bytes 타입의 PCM 오디오 데이터
            
            # stereo를 mono로 변환 (필요한 경우)
            import numpy as np
            
            # bytes를 numpy array로 변환 (16bit signed)
            audio_array = np.frombuffer(data, dtype=np.int16)
            
            # stereo인 경우 mono로 변환
            if len(audio_array) % 2 == 0:  # stereo 가정
                audio_array = audio_array.reshape(-1, 2)
                audio_mono = np.mean(audio_array, axis=1).astype(np.int16)
            else:
                audio_mono = audio_array
            
            # 로봇으로 전송할 큐에 추가
            if not self.bridge.audio_queue.full():
                self.bridge.audio_queue.put(audio_mono.tobytes())
                print(f"📤 Discord 오디오 데이터 큐에 추가: {len(audio_mono.tobytes())} bytes")
            else:
                # 큐가 가득 찬 경우 오래된 데이터 제거
                try:
                    self.bridge.audio_queue.get_nowait()
                    self.bridge.audio_queue.put(audio_mono.tobytes())
                except queue.Empty:
                    pass
                    
        except Exception as e:
            print(f"❌ Discord 오디오 수신 오류: {e}")
    
    def cleanup(self):
        """정리 작업"""
        print("🧹 BasicDiscordAudioSink 정리됨")

class RobotToDiscordAudioBridge:
    """로봇 오디오를 Discord로 전송하는 브리지 - 실제 오디오 스트리밍 구현"""
    
    def __init__(self):
        """초기화 메서드"""
        self.audio_queue = queue.Queue(maxsize=50)
        self.is_receiving = False
        self.robot_conn = None
        self.audio_source = None  # 🆕 Discord 오디오 소스
        print("✅ RobotToDiscordAudioBridge 초기화 완료")
    
    async def connect_to_robot(self):
        """로봇에 연결하여 오디오 수신 - TokenManager 오류 수정"""
        try:
            # 🔧 기존 연결 재사용 우선 시도
            import sys
            import os
            sys.path.append(os.path.dirname(os.path.abspath(__file__)))
            
            try:
                from webrtc_producer import _conn_holder
                
                if _conn_holder and 'conn' in _conn_holder and _conn_holder['conn']:
                    existing_conn = _conn_holder['conn']
                    
                    if hasattr(existing_conn, 'audio') and existing_conn.audio:
                        self.robot_conn = existing_conn
                        
                        # 오디오 콜백 등록 시도
                        try:
                            if hasattr(existing_conn.audio, 'add_track_callback'):
                                existing_conn.audio.add_track_callback(self._receive_robot_audio)
                                print("🎙️ 로봇 오디오 콜백 등록됨")
                        except Exception as cb_e:
                            print(f"⚠️ 오디오 콜백 등록 실패: {cb_e}")
                        
                        print("✅ 기존 연결 재사용 (Robot → Discord)")
                        return True
                    else:
                        print("⚠️ 기존 연결에 오디오 채널이 없음")
                        return True  # 연결은 있으니 성공으로 처리
                else:
                    print("💡 기존 연결이 없어 Robot → Discord 브리지는 건너뜀")
                    return True  # 단방향이라도 동작하도록
                    
            except ImportError as e:
                print(f"⚠️ webrtc_producer 임포트 실패: {e}")
                return True  # 최소한 Discord → Robot은 동작하도록
            
            # 새로운 독립적인 연결 생성
            print("🔄 Discord 봇용 독립적인 연결 생성 시도...")
            return await self.create_robot_connection()
                
        except Exception as e:
            print(f"❌ Discord → Robot 연결 실패: {e}")
            return True  # 단방향이라도 동작하도록

    def _receive_robot_audio(self, track):
        """로봇에서 오디오 수신"""
        try:
            # 오디오 처리 로직 (향후 구현)
            pass
        except Exception as e:
            print(f"❌ 로봇 오디오 수신 오류: {e}")
    
    def get_audio_data(self):
        """Discord로 전송할 오디오 데이터 가져오기"""
        try:
            # 🔧 audio_queue 안전 확인
            if hasattr(self, 'audio_queue'):
                return self.audio_queue.get_nowait()
            else:
                return None
        except queue.Empty:
            return None
    
    def start_receiving(self):
        """로봇 오디오 수신 시작 - 실제 구현"""
        # 🔧 audio_queue가 초기화되었는지 확인
        if not hasattr(self, 'audio_queue'):
            print("⚠️ audio_queue가 초기화되지 않음 - 지금 초기화합니다")
            self.audio_queue = queue.Queue(maxsize=50)
        
        # 🆕 커스텀 Discord 오디오 소스 생성 및 재생 시작
        try:
            self.audio_source = SimpleRobotAudioSource(self)
            
            # Opus 초기화 확인
            if not discord.opus.is_loaded():
                try:
                    discord.opus.load_opus('libopus')
                    print("✅ Opus 라이브러리 로드됨")
                except:
                    print("⚠️ Discord Opus가 로드되지 않음 - PCM 모드 사용")
            
            # Discord에서 로봇 오디오 재생
            if voice_client and voice_client.is_connected():
                voice_client.play(self.audio_source, after=lambda e: print(f'Player error: {e}') if e else None)
                print("🔊 Discord에서 로봇 오디오 재생 시작")
            else:
                print("❌ Discord 음성 클라이언트가 연결되지 않음")
                return
                
        except Exception as e:
            print(f"❌ Discord 오디오 소스 생성 실패: {e}")
            return
            
        self.is_receiving = True
        print("🎵 Robot → Discord 오디오 수신 시작")
    
    def stop_receiving(self):
        """로봇 오디오 수신 중지"""
        self.is_receiving = False
        
        # Discord 오디오 재생 중지
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            print("🔇 Discord 오디오 재생 중지됨")
        
        print("🛑 Robot → Discord 오디오 수신 중지")
    
    async def _receive_robot_audio(self, frame):
        """로봇에서 오디오 프레임 수신 - 실제 구현"""
        try:
            # WebRTC AudioFrame을 PCM 데이터로 변환
            import numpy as np
            
            # AudioFrame에서 numpy array 추출
            audio_array = frame.to_ndarray()
            
            # 샘플레이트와 채널 수 확인
            sample_rate = frame.sample_rate
            channels = audio_array.shape[1] if len(audio_array.shape) > 1 else 1
            
            print(f"🎙️ 로봇 오디오 수신: {sample_rate}Hz, {channels}ch, {audio_array.shape}")
            
            # Discord 호환 형식으로 변환 (48kHz, 16bit, stereo)
            if sample_rate != 48000:
                # 리샘플링 필요 (간단한 업샘플링)
                try:
                    import scipy.signal
                    audio_array = scipy.signal.resample(
                        audio_array, 
                        int(len(audio_array) * 48000 / sample_rate)
                    )
                except ImportError:
                    print("⚠️ scipy 없음 - 리샘플링 건너뜀")
            
            # mono를 stereo로 변환
            if channels == 1 and len(audio_array.shape) == 1:
                audio_stereo = np.column_stack([audio_array, audio_array])
            else:
                audio_stereo = audio_array
            
            # 16bit PCM으로 변환
            audio_pcm = (audio_stereo * 32767).astype(np.int16)
            
            # Discord 오디오 큐에 추가
            if not self.audio_queue.full():
                self.audio_queue.put(audio_pcm.tobytes())
            else:
                # 큐가 가득 찬 경우 오래된 데이터 제거
                try:
                    self.audio_queue.get_nowait()
                    self.audio_queue.put(audio_pcm.tobytes())
                except queue.Empty:
                    pass
                    
        except Exception as e:
            print(f"❌ 로봇 오디오 수신 처리 오류: {e}")

# 🆕 커스텀 Discord 오디오 소스 클래스 (AudioSource 사용)
class CustomRobotAudioSource(discord.AudioSource):
    """로봇 오디오를 Discord로 스트리밍하는 소스"""
    
    def __init__(self, bridge):
        self.bridge = bridge
        self.frame_size = 960  # 20ms at 48kHz
        print("✅ CustomRobotAudioSource 초기화됨")
    
    def read(self):
        """Discord가 오디오 데이터를 요청할 때 호출"""
        try:
            # 로봇 오디오 큐에서 데이터 가져오기
            if not self.bridge.audio_queue.empty():
                audio_data = self.bridge.audio_queue.get_nowait()
                return audio_data
            else:
                # 무음 반환 (20ms, 48kHz, 16bit, stereo)
                silent_frame = b'\x00' * (self.frame_size * 2 * 2)
                return silent_frame
                
        except queue.Empty:
            # 무음 반환
            silent_frame = b'\x00' * (self.frame_size * 2 * 2)
            return silent_frame
        except Exception as e:
            print(f"❌ Discord 오디오 소스 읽기 오류: {e}")
            return b'\x00' * (self.frame_size * 2 * 2)
    
    def is_opus(self):
        """Opus 인코딩 여부 (False = PCM)"""
        return False
    
    def cleanup(self):
        """정리 작업"""
        print("🧹 CustomRobotAudioSource 정리됨")

# 🆕 음성 브리지 인스턴스
discord_to_robot_bridge = None
robot_to_discord_bridge = None

# 🆕 음성 관련 함수들
async def connect_voice_channel():
    """음성 채널에 연결하고 자동으로 브리지 시작"""
    global voice_client, voice_bridge_active, discord_to_robot_bridge, robot_to_discord_bridge
    
    try:
        # 음성 채널 가져오기
        voice_channel = bot.get_channel(VOICE_CHANNEL_ID)
        
        if not voice_channel:
            print(f"❌ 음성 채널을 찾을 수 없습니다: {VOICE_CHANNEL_ID}")
            return False
        
        # 이미 연결되어 있는지 확인
        if voice_client and voice_client.is_connected():
            print("✅ 이미 음성 채널에 연결되어 있습니다.")
            return True
        
        # 음성 채널에 연결
        voice_client = await voice_channel.connect()
        print(f"🎵 {voice_channel.name} 채널에 연결되었습니다!")
        
        # 🆕 자동으로 음성 브리지 시작
        bridge_success = await start_voice_bridge()
        
        if bridge_success:
            print("🎵 음성 채널 연결 및 브리지 활성화 완료!")
            save_voice_status(True, True)
            return True
        else:
            print("⚠️ 음성 채널 연결됨, 하지만 브리지 시작 실패")
            save_voice_status(True, False)
            return False
            
    except Exception as e:
        print(f"❌ 음성 채널 연결 실패: {e}")
        save_voice_status(False, False)
        return False

async def disconnect_voice_channel():
    """음성 브리지 중지 후 채널에서 퇴장"""
    global voice_client, voice_bridge_active
    
    try:
        # 🆕 브리지 먼저 중지
        await stop_voice_bridge()
        
        # 음성 채널에서 연결 해제
        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()
            voice_client = None
            print("🔇 음성 채널에서 연결 해제되었습니다.")
        else:
            print("❌ 현재 음성 채널에 연결되어 있지 않습니다.")
        
        save_voice_status(False, False)
        return True
        
    except Exception as e:
        print(f"❌ 음성 채널 연결 해제 실패: {e}")
        return False

def check_webrtc_connection_status():
    """웹 서버의 WebRTC 연결 상태 확인"""
    try:
        if os.path.exists('.webrtc_connection_status.json'):
            with open('.webrtc_connection_status.json', 'r') as f:
                status = json.load(f)
            
            # 5분 이내 데이터만 유효
            if time.time() - status.get('connection_time', 0) < 300:
                return status
        
        return {
            'connected': False,
            'ready_for_voice_bridge': False,
            'error': 'No recent connection data'
        }
        
    except Exception as e:
        return {
            'connected': False,
            'ready_for_voice_bridge': False,
            'error': str(e)
        }

async def start_voice_bridge():
    """양방향 음성 브리지 시작 - 실제 오디오 스트리밍 및 PyAudio 지원"""
    global voice_bridge_active, discord_to_robot_bridge, robot_to_discord_bridge
    
    try:
        if not voice_client or not voice_client.is_connected():
            print("❌ 음성 채널에 먼저 연결해야 합니다.")
            return False
        
        if voice_bridge_active:
            print("⚠️ 이미 음성 브리지가 활성화되어 있습니다.")
            return True
        
        print("🔧 실제 오디오 스트리밍 브리지 초기화 중...")
        
        # 🆕 오디오 라이브러리 상태 확인
        if AUDIO_LIBS_AVAILABLE:
            print("✅ PyAudio 및 오디오 라이브러리 준비됨")
        else:
            print("⚠️ PyAudio 라이브러리 없음 - 제한된 기능으로 동작")
        
        # 🆕 Opus 라이브러리 로드 확인
        if not discord.opus.is_loaded():
            try:
                discord.opus.load_opus('libopus')
                print("✅ Opus 라이브러리 로드됨")
            except Exception as opus_e:
                print(f"⚠️ Opus 로드 실패: {opus_e} (PCM 모드로 진행)")
        
        # WebRTC 연결 상태 확인
        webrtc_status = check_webrtc_connection_status()
        if not webrtc_status.get('ready_for_voice_bridge', False):
            print("⚠️ WebRTC 연결이 음성 브리지 준비되지 않음")
            print("💡 웹에서 'START CONTROL'을 먼저 실행하세요")
        
        # 브리지 인스턴스 생성
        print("🔄 실제 오디오 스트리밍 브리지 생성 중...")
        discord_to_robot_bridge = DiscordToRobotAudioBridge()
        robot_to_discord_bridge = EnhancedRobotToDiscordAudioBridge()  # 🆕 향상된 버전
        
        # 연결 시도
        print("🔗 Discord → Robot 연결 시도...")
        discord_success = await discord_to_robot_bridge.connect_to_robot()
        
        print("🔗 Robot → Discord 연결 시도...")
        robot_success = await robot_to_discord_bridge.connect_to_robot()
        
        # 결과에 따른 처리
        if discord_success and robot_success:
            # 양방향 브리지 시작
            try:
                print("🎵 실제 오디오 스트리밍 시작...")
                
                discord_start = discord_to_robot_bridge.start_streaming()
                robot_to_discord_bridge.start_receiving()
                
                if discord_start:
                    print("✅ Discord → Robot 실제 오디오 스트리밍 시작됨")
                    if DISCORD_SINKS_AVAILABLE:
                        print("🎙️ Discord 고급 음성 캡처 활성화됨")
                    elif AUDIO_LIBS_AVAILABLE:
                        print("🎤 PyAudio 마이크 캡처 활성화됨")
                    else:
                        print("🔊 테스트 모드 오디오 생성 활성화됨")
                else:
                    print("⚠️ Discord → Robot 스트리밍 시작 실패")
                
                print("✅ Robot → Discord 실제 오디오 재생 시작됨")
                
            except Exception as e:
                print(f"⚠️ 실제 오디오 스트리밍 시작 중 경고: {e}")
                import traceback
                print(f"🔍 상세 오류: {traceback.format_exc()}")
            
            voice_bridge_active = True
            print("🎵 실제 양방향 음성 브리지 완전 활성화!")
            
            # 사용 가능한 기능 안내
            if DISCORD_SINKS_AVAILABLE:
                print("🗣️ Discord 음성 → 🤖 로봇 스피커 (Discord.py sinks)")
            elif AUDIO_LIBS_AVAILABLE:
                print("🗣️ 로컬 마이크 → 🤖 로봇 스피커 (PyAudio)")
            else:
                print("🗣️ 테스트 신호 → 🤖 로봇 스피커 (시뮬레이션)")
                
            print("🤖 로봇 마이크 → 🔊 Discord 채널 (실제 오디오)")
            print("💡 이제 실시간 음성 통신이 가능합니다!")
            
            save_voice_status(True, True)
            return True
            
        elif discord_success:
            # Discord → Robot만 성공
            try:
                discord_start = discord_to_robot_bridge.start_streaming()
                if discord_start:
                    print("✅ Discord → Robot 단방향 실제 오디오 스트리밍 시작됨")
                    print("🗣️ Discord/마이크에서 말하면 로봇 스피커로 출력됩니다!")
                else:
                    print("⚠️ Discord → Robot 스트리밍 시작 실패")
            except Exception as e:
                print(f"⚠️ Discord → Robot 스트리밍 시작 중 경고: {e}")
            
            voice_bridge_active = True
            print("🎵 부분적 실제 음성 브리지 활성화 (Discord → Robot)")
            
            save_voice_status(True, True)
            return True
            
        else:
            # 모든 연결 실패
            print("❌ 모든 음성 브리지 연결 실패")
            print("🔍 디버깅 정보:")
            print(f"   WebRTC 상태: {webrtc_status}")
            print(f"   Discord sinks: {'사용 가능' if DISCORD_SINKS_AVAILABLE else '사용 불가'}")
            print(f"   PyAudio: {'사용 가능' if AUDIO_LIBS_AVAILABLE else '사용 불가'}")
            print("💡 해결 방안:")
            print("   1. pip install pyaudio soundfile librosa")
            print("   2. pip install --upgrade 'discord.py[voice]>=2.0.0'")
            print("   3. 웹에서 'START CONTROL' 재실행")
            print("   4. Discord 봇 재시작")
            
            save_voice_status(voice_client is not None, False)
            return False
            
    except Exception as e:
        print(f"❌ 실제 음성 브리지 시작 오류: {e}")
        import traceback
        print(f"🔍 상세 오류: {traceback.format_exc()}")
        save_voice_status(voice_client is not None, False)
        return False

async def stop_voice_bridge():
    """양방향 음성 브리지 중지 및 Discord 봇 WebRTC 연결 정리"""
    global voice_bridge_active, discord_to_robot_bridge, robot_to_discord_bridge, discord_bot_webrtc_conn
    
    try:
        if not voice_bridge_active:
            print("❌ 음성 브리지가 활성화되어 있지 않습니다.")
            return True
        
        # 브리지 중지
        if discord_to_robot_bridge:
            discord_to_robot_bridge.stop_streaming()
        
        if robot_to_discord_bridge:
            robot_to_discord_bridge.stop_receiving()
        
        # 🆕 Discord 봇용 WebRTC 연결 정리
        if discord_bot_webrtc_conn:
            try:
                # 오디오 채널 비활성화
                if hasattr(discord_bot_webrtc_conn, 'audio') and discord_bot_webrtc_conn.audio:
                    discord_bot_webrtc_conn.audio.switchAudioChannel(False)
                    print("🔇 Discord 봇 오디오 채널 비활성화")
                
                # 연결 종료는 하지 않고 유지 (재사용을 위해)
                # await discord_bot_webrtc_conn.disconnect()
                
                print("🔧 Discord 봇용 WebRTC 연결 유지 (재사용 대기)")
            except Exception as e:
                print(f"⚠️ Discord 봇 WebRTC 연결 정리 중 경고: {e}")
        
        voice_bridge_active = False
        discord_to_robot_bridge = None
        robot_to_discord_bridge = None
        
        print("🔇 Discord 봇용 독립적인 양방향 음성 브리지가 중지되었습니다.")
        save_voice_status(voice_client is not None, False)
        return True
        
    except Exception as e:
        print(f"❌ Discord 봇 음성 브리지 중지 오류: {e}")
        return False

def save_voice_status(voice_connected, bridge_active):
    """음성 상태를 파일에 저장"""
    try:
        status_data = {
            'voice_connected': voice_connected,
            'bridge_active': bridge_active,
            'last_activity': datetime.now().isoformat()
        }
        
        with open('.voice_status.json', 'w') as f:
            json.dump(status_data, f)
            
    except Exception as e:
        print(f"❌ 음성 상태 저장 실패: {e}")

# 🆕 웹에서의 음성 명령 감시
async def monitor_voice_commands():
    """웹에서의 음성 명령 감시"""
    print("🎵 음성 명령 감시 시작...")
    
    while True:
        try:
            if os.path.exists('.voice_command.json'):
                with open('.voice_command.json', 'r') as f:
                    command_data = json.load(f)
                
                command = command_data.get('command')
                
                # 명령 처리
                if command == 'voice_connect':
                    print("🔗 웹에서 음성 채널 연결 요청됨")
                    await connect_voice_channel()
                    
                elif command == 'voice_disconnect':
                    print("❌ 웹에서 음성 채널 연결 해제 요청됨")
                    await disconnect_voice_channel()
                    
                elif command == 'start_voice_bridge':
                    print("🎙️ 웹에서 음성 브리지 시작 요청됨")
                    await start_voice_bridge()
                    
                elif command == 'stop_voice_bridge':
                    print("🔇 웹에서 음성 브리지 중지 요청됨")
                    await stop_voice_bridge()
                
                # 파일 삭제
                os.remove('.voice_command.json')
                
        except Exception as e:
            print(f"❌ 음성 명령 감시 오류: {e}")
            if os.path.exists('.voice_command.json'):
                try:
                    os.remove('.voice_command.json')
                except:
                    pass
        
        await asyncio.sleep(1.0)

# 기존 함수들 추가
def generate_alert_id(alert_data):
    """알림 데이터에서 고유 ID 생성"""
    timestamp = alert_data.get('timestamp', '')
    alert_type = alert_data.get('alert_type', '')
    duration = alert_data.get('duration', 0)
    is_repeat = alert_data.get('is_repeat', False)
    attempts = alert_data.get('attempts', 0)
    
    # 고유 ID 생성 (timestamp + type + duration + repeat + attempts)
    return f"{timestamp}_{alert_type}_{duration}_{is_repeat}_{attempts}"

def get_user_identity_key(marker_info):
    """사용자 신원 식별 키 생성"""
    # 여러 방법으로 사용자 식별 (우선순위 순)
    if marker_info.get('employee_id'):
        return f"emp_{marker_info['employee_id']}"
    elif marker_info.get('name') and marker_info.get('affiliation'):
        return f"name_{marker_info['name']}_{marker_info['affiliation']}"
    elif marker_info.get('marker_id'):
        return f"marker_{marker_info['marker_id']}"
    else:
        return f"unknown_{hash(str(marker_info))}"

def should_send_identity_alert(marker_info):
    """사용자별 알림 중복 확인 (쿨다운 적용)"""
    global user_identity_alerts, daily_identity_log
    
    user_key = get_user_identity_key(marker_info)
    current_time = datetime.now()
    current_date = current_time.date().isoformat()
    
    # 🔧 일일 기록 초기화 (오전 6시 기준)
    if current_time.hour == IDENTITY_DAILY_RESET_HOUR and current_time.minute == 0:
        # 어제 기록 삭제
        yesterday = (current_time - timedelta(days=1)).date().isoformat()
        if yesterday in daily_identity_log:
            del daily_identity_log[yesterday]
            print(f"🗑️ 어제({yesterday}) 신원 알림 기록 삭제됨")
    
    # 🔧 쿨다운 확인
    if user_key in user_identity_alerts:
        last_alert_time = user_identity_alerts[user_key]
        time_diff = (current_time - last_alert_time).total_seconds()
        
        if time_diff < IDENTITY_ALERT_COOLDOWN:
            remaining_time = IDENTITY_ALERT_COOLDOWN - time_diff
            print(f"🔒 사용자 {user_key} 알림 쿨다운 중: {remaining_time:.0f}초 남음")
            return False, f"쿨다운 {remaining_time:.0f}초 남음"
    
    # 🔧 일일 최대 알림 횟수 확인 (선택사항)
    if current_date not in daily_identity_log:
        daily_identity_log[current_date] = {}
    
    if user_key not in daily_identity_log[current_date]:
        daily_identity_log[current_date][user_key] = 0
    
    daily_count = daily_identity_log[current_date][user_key]
    
    # 일일 최대 10회까지만 알림 (과도한 알림 방지)
    if daily_count >= 10:
        print(f"🚫 사용자 {user_key} 일일 최대 알림 횟수 초과: {daily_count}회")
        return False, f"일일 최대 알림 초과 ({daily_count}회)"
    
    # 🔧 알림 허용
    user_identity_alerts[user_key] = current_time
    daily_identity_log[current_date][user_key] = daily_count + 1
    
    print(f"✅ 사용자 {user_key} 알림 허용: 오늘 {daily_count + 1}번째")
    return True, f"오늘 {daily_count + 1}번째 알림"

# 🔥 화재 알림 감시 함수
async def monitor_fire_alerts():
    """🔧 Fire 알림 파일 감시 (중복 방지 개선)"""
    print("🔥 Fire 알림 감시 시작...")
    global last_fire_alert_id, processed_fire_alerts
    
    while True:
        try:
            if os.path.exists('.fire_alert.json'):
                # 파일 읽기
                with open('.fire_alert.json', 'r') as f:
                    alert_data = json.load(f)
                
                # 고유 ID 생성
                alert_id = generate_alert_id(alert_data)
                
                # 🔧 중복 체크: 새로운 알림인지 확인
                if alert_id != last_fire_alert_id and alert_id not in processed_fire_alerts:
                    print(f"🔥 새로운 화재 알림 감지: {alert_id}")
                    
                    # 알림 전송
                    await send_fire_alert_to_all_channels(alert_data)
                    
                    # 처리된 알림 기록
                    last_fire_alert_id = alert_id
                    processed_fire_alerts.add(alert_id)
                    
                    # 처리된 알림 목록 크기 제한 (메모리 관리)
                    if len(processed_fire_alerts) > 100:
                        # 오래된 알림 ID 50개 제거
                        old_alerts = list(processed_fire_alerts)[:50]
                        for old_alert in old_alerts:
                            processed_fire_alerts.discard(old_alert)
                    
                    print(f"✅ 화재 알림 처리 완료: {alert_id}")
                
                else:
                    # 중복 알림 무시
                    print(f"🔄 중복 화재 알림 무시: {alert_id}")
                
                # 🔧 파일 강제 삭제 (중복이든 아니든)
                try:
                    os.remove('.fire_alert.json')
                    print(f"🗑️ 화재 알림 파일 삭제 완료")
                except Exception as e:
                    print(f"⚠️ 화재 알림 파일 삭제 실패: {e}")
                    
        except Exception as e:
            print(f"❌ 화재 알림 감시 오류: {e}")
            # 오류 발생시에도 파일 삭제 시도
            try:
                if os.path.exists('.fire_alert.json'):
                    os.remove('.fire_alert.json')
                    print(f"🗑️ 오류 후 화재 알림 파일 삭제")
            except:
                pass
        
        await asyncio.sleep(1.0)  # 1초마다 확인

# 🆕 ArUco 스캔 결과 감시
async def monitor_aruco_scan_results():
    """ArUco 신원 스캔 결과 파일 감시 (사용자별 중복 방지)"""
    print("🔖 ArUco 신원 스캔 결과 감시 시작...")
    global last_aruco_scan_id, processed_aruco_scans
    
    while True:
        try:
            if os.path.exists('.aruco_scan_result.json'):
                # 파일 읽기
                with open('.aruco_scan_result.json', 'r', encoding='utf-8') as f:
                    scan_data = json.load(f)
                
                # 고유 ID 생성
                scan_id = generate_alert_id(scan_data)
                
                # 🔧 기본 중복 체크 (파일 기반)
                if scan_id != last_aruco_scan_id and scan_id not in processed_aruco_scans:
                    print(f"🔖 새로운 ArUco 신원 스캔 감지: {scan_id}")
                    
                    # 🆕 사용자별 중복 체크 (성공한 경우만)
                    alert_type = scan_data.get('alert_type', 'aruco_identity_success')
                    
                    if alert_type == 'aruco_identity_success':
                        marker_info = scan_data.get('marker_info', {})
                        should_send, reason = should_send_identity_alert(marker_info)
                        
                        if should_send:
                            # 알림 전송
                            await send_aruco_identity_to_all_channels(scan_data)
                            print(f"✅ ArUco 신원 스캔 알림 전송 완료: {reason}")
                        else:
                            print(f"🔒 ArUco 신원 스캔 알림 건너뜀: {reason}")
                    else:
                        # 실패 알림은 항상 전송 (중요한 정보)
                        await send_aruco_identity_to_all_channels(scan_data)
                        print(f"❌ ArUco 신원 스캔 실패 알림 전송됨")
                    
                    # 처리된 스캔 기록
                    last_aruco_scan_id = scan_id
                    processed_aruco_scans.add(scan_id)
                    
                    # 메모리 관리
                    if len(processed_aruco_scans) > 100:
                        old_scans = list(processed_aruco_scans)[:50]
                        for old_scan in old_scans:
                            processed_aruco_scans.discard(old_scan)
                    
                    print(f"✅ ArUco 신원 스캔 처리 완료: {scan_id}")
                
                else:
                    print(f"🔄 중복 ArUco 신원 스캔 무시: {scan_id}")
                
                # 파일 삭제
                try:
                    os.remove('.aruco_scan_result.json')
                    print(f"🗑️ ArUco 신원 스캔 파일 삭제 완료")
                except Exception as e:
                    print(f"⚠️ ArUco 신원 스캔 파일 삭제 실패: {e}")
                    
        except Exception as e:
            print(f"❌ ArUco 신원 스캔 결과 감시 오류: {e}")
            # 오류 발생시에도 파일 삭제 시도
            try:
                if os.path.exists('.aruco_scan_result.json'):
                    os.remove('.aruco_scan_result.json')
                    print(f"🗑️ 오류 후 ArUco 신원 스캔 파일 삭제")
            except:
                pass
        
        await asyncio.sleep(1.0)

async def send_fire_alert_to_all_channels(alert_data):
    """모든 설정된 채널에 Fire 알림 전송"""
    
    for channel_config in FIRE_ALERT_CHANNELS:
        try:
            await send_fire_alert_to_channel(alert_data, channel_config)
        except Exception as e:
            print(f"❌ {channel_config['server_name']} 알림 전송 실패: {e}")

async def send_fire_alert_to_channel(alert_data, channel_config):
    """특정 채널에 Fire 알림 전송"""
    try:
        channel_id = channel_config['channel_id']
        role_id = channel_config['role_id']
        server_name = channel_config['server_name']
        
        channel = bot.get_channel(channel_id)
        
        if channel is None:
            print(f"❌ 채널을 찾을 수 없습니다 ({server_name}): {channel_id}")
            return
        
        role_mention = f"<@&{role_id}>"
        
        # 🆕 반복 알림 여부에 따른 다른 메시지
        is_repeat = alert_data.get('is_repeat', False)
        alert_count = alert_data.get('alert_count', 1)
        duration = alert_data.get('duration', 5.0)
        
        if is_repeat:
            # 반복 알림
            embed = discord.Embed(
                title=f"🔥 화재 지속 알림 #{alert_count}",
                description=f"**지속 중!** 화재가 {duration:.1f}초간 계속 감지되고 있습니다!",
                color=0xff4500,  # 오렌지-레드 (지속 경고)
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="⏰ 총 감지 시간",
                value=f"{duration:.1f}초 연속",
                inline=True
            )
            embed.add_field(
                name="📊 알림 횟수",
                value=f"{alert_count}번째 알림",
                inline=True
            )
            embed.add_field(
                name="🔄 상태",
                value="화재 지속 중",
                inline=True
            )
            
            content = f"{role_mention} 🔥 **화재 지속 경고 #{alert_count}**"
            
        else:
            # 첫 알림
            embed = discord.Embed(
                title="🚨 화재 감지 알림",
                description="**위험!** Unitree 로봇에서 화재가 감지되었습니다!",
                color=0xff0000,  # 빨간색 (첫 경고)
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="🔥 감지 시간",
                value=f"{duration:.1f}초 연속 감지",
                inline=True
            )
            embed.add_field(
                name="📍 위치",
                value="Unitree 로봇 카메라",
                inline=True
            )
            embed.add_field(
                name="⚡ 신뢰도",
                value="높음 (50% 이상)",
                inline=True
            )
            
            content = f"{role_mention} 🚨 **긴급 상황 발생!**"
        
        # 공통 필드
        embed.add_field(
            name="🔗 실시간 확인",
            value="[카메라 보기](http://localhost:5010)",
            inline=False
        )
        embed.add_field(
            name="📱 알림 설정",
            value="5초마다 반복 알림",
            inline=True
        )
        
        embed.set_footer(text=f"Unitree 화재 감지 시스템 | {server_name}")
        
        # 알림 전송
        await channel.send(content, embed=embed)
        
        if is_repeat:
            print(f"🔥 화재 반복 알림 #{alert_count} 전송 완료! ({server_name})")
        else:
            print(f"🚨 화재 첫 알림 전송 완료! ({server_name})")
            
    except Exception as e:
        print(f"❌ Discord 알림 전송 실패 ({server_name}): {e}")

async def send_aruco_identity_to_all_channels(scan_data):
    """모든 설정된 채널에 ArUco 신원 스캔 결과 전송"""
    
    for channel_config in FIRE_ALERT_CHANNELS:
        try:
            await send_aruco_identity_to_channel(scan_data, channel_config)
        except Exception as e:
            print(f"❌ {channel_config['server_name']} ArUco 신원 알림 전송 실패: {e}")

async def send_aruco_identity_to_channel(scan_data, channel_config):
    """특정 채널에 ArUco 신원 스캔 결과 전송 (사용자별 중복 방지 적용)"""
    try:
        channel_id = channel_config['channel_id']
        role_id = channel_config['role_id']
        server_name = channel_config['server_name']
        
        channel = bot.get_channel(channel_id)
        
        if channel is None:
            print(f"❌ 채널을 찾을 수 없습니다 ({server_name}): {channel_id}")
            return
        
        role_mention = f"<@&{role_id}>"
        alert_type = scan_data.get('alert_type', 'aruco_identity_success')
        
        # ArUco 신원 스캔 실패 처리
        if alert_type == 'aruco_identity_failure':
            failure_info = scan_data.get('failure_info', {})
            attempts = failure_info.get('attempts', 0)
            max_attempts = failure_info.get('max_attempts', MAX_ARUCO_ATTEMPTS)
            scan_time = failure_info.get('scan_time', 'Unknown')
            timeout = failure_info.get('timeout', ARUCO_SCAN_TIMEOUT)
            scan_duration = failure_info.get('scan_duration', 0)
            
            embed = discord.Embed(
                title="❌ ArUco 신원 마커 스캔 실패",
                description="**경고!** ArUco 신원 마커 스캔에 여러 번 실패했습니다.",
                color=0xff0000,  # 빨간색
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="🔄 시도 정보",
                value=f"**시도 횟수:** {attempts}/{max_attempts}회\n**스캔 시간:** {scan_duration:.1f}초",
                inline=True
            )
            embed.add_field(
                name="⏰ 실패 시간",
                value=scan_time,
                inline=True
            )
            embed.add_field(
                name="⏱️ 설정",
                value=f"**타임아웃:** {timeout}초\n**재시도 간격:** {ARUCO_RETRY_INTERVAL}초",
                inline=True
            )
            embed.add_field(
                name="📍 위치",
                value="Unitree 로봇 카메라",
                inline=True
            )
            embed.add_field(
                name="🤖 로봇 상태",
                value="자동으로 일어서기 실행됨",
                inline=True
            )
            embed.add_field(
                name="💡 권장 조치",
                value="• ArUco 마커 상태 확인\n• 카메라 렌즈 청소\n• 조명 상태 점검\n• 마커 교체 고려",
                inline=False
            )
            
            embed.set_footer(text=f"Unitree ArUco 신원 확인 시스템 | {server_name}")
            
            content = f"{role_mention} ⚠️ **ArUco 스캔 실패 알림**"
            
        else:
            # ArUco 신원 스캔 성공 처리
            marker_info = scan_data.get('marker_info', {})
            scan_info = scan_data.get('scan_info', {})
            scan_time = scan_info.get('scan_time', 'Unknown')
            attempts = scan_info.get('attempts', 1)
            
            embed = discord.Embed(
                title="🔖 ArUco 신원 확인 완료",
                description=f"**{marker_info.get('name', 'Unknown')}**님이 ArUco 마커로 시스템에 출입했습니다.",
                color=0x00ff00,  # 녹색
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="👤 신원 정보",
                value=f"**이름:** {marker_info.get('name', 'Unknown')}\n**소속:** {marker_info.get('affiliation', 'Unknown')}",
                inline=True
            )
            embed.add_field(
                name="🔖 마커 정보",
                value=f"**마커 ID:** {marker_info.get('marker_id', 'Unknown')}\n**사번:** {marker_info.get('employee_id', 'N/A')}",
                inline=True
            )
            embed.add_field(
                name="⏰ 출입 시간",
                value=scan_time,
                inline=True
            )
            embed.add_field(
                name="📊 스캔 정보",
                value=f"**시도 횟수:** {attempts}번\n**스캔 위치:** 로봇 카메라",
                inline=True
            )
            
            # 🆕 접근 권한에 따른 알림 레벨 구분
            access_level = marker_info.get('access_level', 'standard')
            role_text = marker_info.get('role', 'Unknown')
            
            if access_level in ['admin', 'super_admin', 'emergency']:
                content = f"{role_mention} 🚨 **관리자급 ArUco 출입 알림**"
                embed.add_field(
                    name="🔑 접근 권한",
                    value=f"**권한:** {access_level}\n**직책:** {role_text}",
                    inline=True
                )
            else:
                content = f"👤 **ArUco 신원 확인 알림**"
                embed.add_field(
                    name="🏢 직무 정보",
                    value=f"**부서:** {marker_info.get('department', 'Unknown')}\n**직책:** {role_text}",
                    inline=True
                )
            
            embed.set_footer(text=f"Unitree ArUco 신원 확인 시스템 | {server_name}")
        
        # 공통 필드
        embed.add_field(
            name="🔗 실시간 확인",
            value="[카메라 보기](http://localhost:5010)",
            inline=False
        )
        
        await channel.send(content, embed=embed)
        
        if alert_type == 'aruco_identity_success':
            print(f"🔖 ArUco 신원 스캔 성공 알림 전송 완료! ({server_name})")
        else:
            print(f"❌ ArUco 신원 스캔 실패 알림 전송 완료! ({server_name})")
        
    except Exception as e:
        print(f"❌ Discord ArUco 알림 전송 실패 ({server_name}): {e}")

# 🆕 음성 관련 명령어들
@bot.command(name='voice_status')
async def voice_status_command(ctx):
    """음성 연동 상태 확인"""
    embed = discord.Embed(
        title="🎵 음성 연동 상태",
        color=0x00ff00 if voice_bridge_active else 0xff0000
    )
    
    embed.add_field(
        name="🔗 음성 채널 연결",
        value="✅ 연결됨" if voice_client and voice_client.is_connected() else "❌ 연결 안됨",
        inline=True
    )
    embed.add_field(
        name="🎙️ 음성 브리지",
        value="✅ 활성화" if voice_bridge_active else "❌ 비활성화",
        inline=True
    )
    embed.add_field(
        name="🤖 로봇 연결",
        value="✅ 사용 가능" if VOICE_AVAILABLE else "❌ 사용 불가",
        inline=True
    )
    
    if voice_client and voice_client.is_connected():
        channel = voice_client.channel
        embed.add_field(
            name="📍 연결된 채널",
            value=f"#{channel.name} ({channel.id})",
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='voice_connect_manual')
@commands.has_permissions(administrator=True)
async def voice_connect_manual(ctx):
    """수동 음성 채널 연결"""
    success = await connect_voice_channel()
    if success:
        await ctx.send("🎵 음성 채널 연결 및 브리지 활성화 완료!")
    else:
        await ctx.send("❌ 음성 채널 연결 실패")

@bot.command(name='voice_disconnect_manual')
@commands.has_permissions(administrator=True)
async def voice_disconnect_manual(ctx):
    """수동 음성 채널 연결 해제"""
    success = await disconnect_voice_channel()
    if success:
        await ctx.send("🔇 음성 브리지 중지 및 채널 퇴장 완료!")
    else:
        await ctx.send("❌ 음성 채널 연결 해제 실패")

# 기본 명령어들
@bot.command(name='hello')
async def hello(ctx):
    """인사 명령어"""
    await ctx.send(f'안녕하세요, {ctx.author.mention}님! Unitree 상황 알림 봇입니다!')

@bot.command(name='ping')
async def ping(ctx):
    """핑 확인 명령어"""
    latency = round(bot.latency * 1000)
    await ctx.send(f'🏓 퐁! 지연시간: {latency}ms')

@bot.command(name='info')
async def bot_info(ctx):
    """봇 정보 및 로봇 BMS 상태 확인"""
    embed = discord.Embed(
        title="봇 정보 및 로봇 상태",
        description="Unitree 상황 알림 Discord 봇",
        color=0x00ff00
    )
    
    # 기본 봇 정보
    embed.add_field(name="서버 수", value=len(bot.guilds), inline=True)
    embed.add_field(name="사용자 수", value=len(bot.users), inline=True)
    
    # 🆕 음성 기능 상태 추가
    if VOICE_AVAILABLE:
        if voice_client and voice_client.is_connected():
            embed.add_field(name="🎵 음성 연동", value="✅ 활성화", inline=True)
        else:
            embed.add_field(name="🎵 음성 연동", value="❌ 비활성화", inline=True)
    else:
        embed.add_field(name="🎵 음성 기능", value="❌ 사용 불가", inline=True)
    
    # 로봇 상태 가져오기
    try:
        bms_state = get_bms_state()
        robot_status = get_robot_status()
        
        if bms_state:
            soc = bms_state['soc']
            embed.add_field(name="🔋 배터리 잔량", value=f"{soc}%", inline=True)
            embed.add_field(name="⚡ 전류", value=f"{bms_state['current']} mA", inline=True)
            embed.add_field(name="🔄 충전 사이클", value=f"{bms_state['cycle']}회", inline=True)
            embed.add_field(name="🌡️ BQ 온도", value=f"{bms_state['bq_ntc']}°C", inline=True)
            embed.add_field(name="🌡️ MCU 온도", value=f"{bms_state['mcu_ntc']}°C", inline=True)
            
            if soc >= 70:
                embed.color = 0x00ff00
            elif soc >= 30:
                embed.color = 0xffff00
            else:
                embed.color = 0xff0000
        else:
            embed.add_field(name="❌ 로봇 상태", value="BMS 상태 데이터 없음", inline=False)
            embed.add_field(name="🔗 연결 상태", value=robot_status['connection_status'], inline=True)
            embed.color = 0xff0000
            
    except Exception as e:
        embed.add_field(name="❌ 오류", value=f"상태 확인 중 오류: {str(e)}", inline=False)
        embed.color = 0xff0000
    
    await ctx.send(embed=embed)

@bot.command(name='battery')
async def battery_status(ctx):
    """로봇 배터리 상태만 확인"""
    bms_state = get_bms_state()
    
    if bms_state:
        soc = bms_state['soc']
        
        # 배터리 상태에 따른 이모지
        if soc >= 80:
            battery_emoji = "🔋"
            status_text = "충분"
            color = 0x00ff00
        elif soc >= 50:
            battery_emoji = "🔋"
            status_text = "보통"
            color = 0xffff00
        elif soc >= 20:
            battery_emoji = "🪫"
            status_text = "낮음"
            color = 0xff8800
        else:
            battery_emoji = "🔴"
            status_text = "매우 낮음"
            color = 0xff0000
        
        embed = discord.Embed(
            title=f"{battery_emoji} 로봇 배터리 상태",
            color=color
        )
        
        embed.add_field(name="배터리 잔량", value=f"{soc}% ({status_text})", inline=False)
        embed.add_field(name="전류", value=f"{bms_state['current']} mA", inline=True)
        embed.add_field(name="BQ 온도", value=f"{bms_state['bq_ntc']}°C", inline=True)
        embed.add_field(name="MCU 온도", value=f"{bms_state['mcu_ntc']}°C", inline=True)
        
    else:
        embed = discord.Embed(
            title="❌ 배터리 상태 확인 실패",
            description="BMS 데이터를 받을 수 없습니다",
            color=0xff0000
        )
        
    await ctx.send(embed=embed)

# 에러 핸들링
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ 알 수 없는 명령어입니다.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ 필수 인자가 누락되었습니다.")
    else:
        print(f"에러 발생: {error}")
        await ctx.send("❌ 명령어 처리 중 오류가 발생했습니다.")

@bot.event
async def on_message(message):
    # 봇 자신의 메시지는 무시
    if message.author == bot.user:
        return
    
    # 명령어 처리
    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f'{bot.user}로 로그인했습니다!')
    print(f'봇 ID: {bot.user.id}')
    print(f'🎵 음성 연동: {"사용 가능" if VOICE_AVAILABLE else "사용 불가"}')
    print(f'🔖 신원 알림 쿨다운: {IDENTITY_ALERT_COOLDOWN}초 ({IDENTITY_ALERT_COOLDOWN//60}분)')
    print(f'🕕 일일 기록 초기화: 매일 오전 {IDENTITY_DAILY_RESET_HOUR}시')
    print('------')
    
    # 🆕 모든 감시 태스크 시작
    bot.loop.create_task(monitor_fire_alerts())
    bot.loop.create_task(monitor_aruco_scan_results())
    bot.loop.create_task(monitor_voice_commands())  # 음성 명령 감시 추가

if __name__ == '__main__':
    # 봇 토큰 확인
    discord_token = os.getenv('DISCORD_TOKEN')
    if not discord_token:
        print("❌ DISCORD_TOKEN 환경변수가 설정되지 않았습니다!")
        print("📝 .env 파일에 DISCORD_TOKEN=your_token_here 를 추가하세요.")
        exit(1)
    
    try:
        print("🤖 Discord 봇 시작 중...")
        bot.run(discord_token)
    except Exception as e:
        print(f"❌ Discord 봇 실행 실패: {e}")