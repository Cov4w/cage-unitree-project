import asyncio
import logging
import json
import sys
import os
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration
from aiortc.contrib.media import MediaPlayer
from .unitree_auth import send_sdp_to_local_peer, send_sdp_to_remote_peer
from .webrtc_datachannel import WebRTCDataChannel
from .webrtc_audio import WebRTCAudioChannel
from .webrtc_video import WebRTCVideoChannel
from .constants import DATA_CHANNEL_TYPE, WebRTCConnectionMethod
from .util import fetch_public_key, fetch_token, fetch_turn_server_info, print_status, TokenManager
from .multicast_scanner import discover_ip_sn

# # Enable logging for debugging
# logging.basicConfig(level=logging.INFO)

class Go2WebRTCConnection:
    def __init__(self, connectionMethod: WebRTCConnectionMethod, serialNumber=None, ip=None, username=None, password=None) -> None:
        self.pc = None
        self.sn = serialNumber
        self.ip = ip
        self.connectionMethod = connectionMethod
        self.isConnected = False

        # TokenManager를 사용하여 토큰을 불러오고, 없거나 만료되었을 때만 새로 발급
        self.token_manager = TokenManager()
        self.token = self.token_manager.get_token()

    async def connect(self):
        print_status("WebRTC connection", "🟡 started")
        if self.connectionMethod == WebRTCConnectionMethod.Remote:
            self.public_key = fetch_public_key()
            turn_server_info = fetch_turn_server_info(self.sn, self.token, self.public_key)
            await self.init_webrtc(turn_server_info)
        elif self.connectionMethod == WebRTCConnectionMethod.LocalSTA:
            if not self.ip and self.sn:
                discovered_ip_sn_addresses = discover_ip_sn()
                
                if discovered_ip_sn_addresses:
                    if self.sn in discovered_ip_sn_addresses:
                        self.ip = discovered_ip_sn_addresses[self.sn]
                    else:
                        raise ValueError("The provided serial number wasn't found on the network. Provide an IP address instead.")
                else:
                    raise ValueError("No devices found on the network. Provide an IP address instead.")

            await self.init_webrtc(ip=self.ip)
        elif self.connectionMethod == WebRTCConnectionMethod.LocalAP:
            self.ip = "192.168.12.1"
            await self.init_webrtc(ip=self.ip)
    
    async def disconnect(self):
        if self.pc:
            await self.pc.close()
            self.pc = None
        self.isConnected = False
        print_status("WebRTC connection", "🔴 disconnected")

    async def reconnect(self):
        await self.disconnect()
        await self.connect()
        print_status("WebRTC connection", "🟢 reconnected")

    def create_webrtc_configuration(self, turn_server_info, stunEnable=True, turnEnable=True):
        ice_servers = []

        # 환경변수에서 STUN 서버 읽기
        stun_servers = [
            os.getenv('STUN_SERVER_1', 'stun:stun.l.google.com:19302'),
            os.getenv('STUN_SERVER_2', 'stun:stun1.l.google.com:19302')
        ]
        
        for stun_url in stun_servers:
            if stun_url and stunEnable:
                ice_servers.append(RTCIceServer(urls=[stun_url]))
        
        # TURN 서버 설정
        if turn_server_info and turnEnable:
            username = turn_server_info.get("user")
            credential = turn_server_info.get("passwd")
            turn_url = turn_server_info.get("realm")
            
            if username and credential and turn_url:
                ice_servers.append(
                    RTCIceServer(
                        urls=[turn_url],
                        username=username,
                        credential=credential
                    )
                )
            else:
                raise ValueError("Invalid TURN server information")
        
        # Azure 환경 감지
        is_azure = os.getenv('DEPLOYMENT_ENV') == 'server'
        if is_azure:
            print("🌐 Azure 환경용 WebRTC 설정 적용")
            print(f"🔗 사용할 ICE 서버 개수: {len(ice_servers)}개")
            
            # Azure 환경에서 더 많은 STUN 서버 추가 (선택사항)
            additional_stun = [
                "stun:stun2.l.google.com:19302",
                "stun:stun3.l.google.com:19302"
            ]
            for stun_url in additional_stun:
                ice_servers.append(RTCIceServer(urls=[stun_url]))
            
            print(f"🔗 Azure 최적화 후 ICE 서버 개수: {len(ice_servers)}개")
        
        # aiortc에서 지원하는 매개변수만 사용
        configuration = RTCConfiguration(iceServers=ice_servers)
        
        return configuration

    async def init_webrtc(self, turn_server_info=None, ip=None):
        configuration = self.create_webrtc_configuration(turn_server_info)
        self.pc = RTCPeerConnection(configuration)


        self.datachannel = WebRTCDataChannel(self, self.pc)

        self.audio = WebRTCAudioChannel(self.pc, self.datachannel)
        self.video = WebRTCVideoChannel(self.pc, self.datachannel)

        @self.pc.on("icegatheringstatechange")
        async def on_ice_gathering_state_change():
            state = self.pc.iceGatheringState
            if state == "new":
                print_status("ICE Gathering State", "🔵 new")
            elif state == "gathering":
                print_status("ICE Gathering State", "🟡 gathering")
            elif state == "complete":
                print_status("ICE Gathering State", "🟢 complete")


        @self.pc.on("iceconnectionstatechange")
        async def on_ice_connection_state_change():
            state = self.pc.iceConnectionState
            if state == "checking":
                print_status("ICE Connection State", "🔵 checking")
            elif state == "completed":
                print_status("ICE Connection State", "🟢 completed")
            elif state == "failed":
                print_status("ICE Connection State", "🔴 failed")
            elif state == "closed":
                print_status("ICE Connection State", "⚫ closed")


        @self.pc.on("connectionstatechange")
        async def on_connection_state_change():
            state = self.pc.connectionState
            if state == "connecting":
                print_status("Peer Connection State", "🔵 connecting")
            elif state == "connected":
                self.isConnected= True
                print_status("Peer Connection State", "🟢 connected")
            elif state == "closed":
                self.isConnected= False
                print_status("Peer Connection State", "⚫ closed")
            elif state == "failed":
                print_status("Peer Connection State", "🔴 failed")
        
        @self.pc.on("signalingstatechange")
        async def on_signaling_state_change():
            state = self.pc.signalingState
            if state == "stable":
                print_status("Signaling State", "🟢 stable")
            elif state == "have-local-offer":
                print_status("Signaling State", "🟡 have-local-offer")
            elif state == "have-remote-offer":
                print_status("Signaling State", "🟡 have-remote-offer")
            elif state == "closed":
                print_status("Signaling State", "⚫ closed")
        
        @self.pc.on("track")
        async def on_track(track):
            logging.info("Track recieved: %s", track.kind)

            if track.kind == "video":
                #await for the first frame, #ToDo make the code more nicer
                frame = await track.recv()
                await self.video.track_handler(track)
                
            if track.kind == "audio":
                frame = await track.recv()
                while True:
                    frame = await track.recv()
                    await self.audio.frame_handler(frame)

        logging.info("Creating offer...")
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        if self.connectionMethod == WebRTCConnectionMethod.Remote:
            peer_answer_json = await self.get_answer_from_remote_peer(self.pc, turn_server_info)
        elif self.connectionMethod == WebRTCConnectionMethod.LocalSTA or self.connectionMethod == WebRTCConnectionMethod.LocalAP:
            peer_answer_json = await self.get_answer_from_local_peer(self.pc, self.ip)

        if peer_answer_json is not None:
            peer_answer = json.loads(peer_answer_json)
        else:
            error_msg = "Could not get SDP from the peer. Check if the Go2 is switched on"
            print(f"❌ {error_msg}")
            raise ConnectionError(error_msg)  # ✅ Exception 발생

        if peer_answer['sdp'] == "reject":
            error_msg = "Go2 is connected by another WebRTC client"
            print(f"⚠️ {error_msg}")
            
            # 서버 환경에서는 대기열 또는 재시도 로직
            is_azure = os.getenv('DEPLOYMENT_ENV') == 'server'
            if is_azure:
                retry_count = int(os.getenv('CONNECTION_RETRY_COUNT', '3'))
                print(f"🔄 서버 환경: {retry_count}번 재시도 예정")
                raise ConnectionError(f"{error_msg} - 재시도 가능")
            else:
                raise ConnectionError(f"{error_msg} - Close mobile APP and try again")

        remote_sdp = RTCSessionDescription(sdp=peer_answer['sdp'], type=peer_answer['type']) 
        await self.pc.setRemoteDescription(remote_sdp)
   
        # Azure 환경 감지 및 타임아웃 설정
        import os
        is_azure = os.getenv('DEPLOYMENT_ENV') == 'server'
        datachannel_timeout = float(os.getenv('DATACHANNEL_TIMEOUT', '30' if not is_azure else '60'))
        
        print(f"🌐 환경: {'Azure 서버' if is_azure else '로컬'}")
        print(f"📡 DataChannel 타임아웃: {datachannel_timeout}초")
        
        try:
            await self.datachannel.wait_datachannel_open(timeout=datachannel_timeout)
            print("✅ DataChannel 연결 성공")
        except Exception as e:
            print(f"❌ DataChannel 연결 실패: {e}")
            raise ConnectionError(f"DataChannel connection failed: {e}")
    
    async def get_answer_from_remote_peer(self, pc, turn_server_info):
        sdp_offer = pc.localDescription

        sdp_offer_json = {
            "id": "",
            "turnserver": turn_server_info,
            "sdp": sdp_offer.sdp,
            "type": sdp_offer.type,
            "token": self.token
        }

        logging.debug("Local SDP created: %s", sdp_offer_json)

        peer_answer_json = send_sdp_to_remote_peer(self.sn, json.dumps(sdp_offer_json), self.token, self.public_key)

        return peer_answer_json

    async def get_answer_from_local_peer(self, pc, ip):
        sdp_offer = pc.localDescription

        sdp_offer_json = {
            "id": "STA_localNetwork" if self.connectionMethod == WebRTCConnectionMethod.LocalSTA else "",
            "sdp": sdp_offer.sdp,
            "type": sdp_offer.type,
            "token": self.token
        }

        peer_answer_json = send_sdp_to_local_peer(ip, json.dumps(sdp_offer_json))

        return peer_answer_json


