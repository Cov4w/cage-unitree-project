import asyncio
import threading
import time
import logging
import json
import av
from multiprocessing import Queue
from go2_webrtc_connect.go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_connect.go2_webrtc_driver.constants import RTC_TOPIC, SPORT_CMD
from aiortc import MediaStreamTrack
from go2_webrtc_connect.go2_webrtc_driver.util import TokenManager


# 디버깅 
import os
from pathlib import Path
print("=== 환경 디버깅 ===")
print(f"현재 작업 디렉터리: {os.getcwd()}")
print(f"스크립트 위치: {__file__}")
print(f"프로젝트 루트: {Path(__file__).parent}")
# 디버깅


from config.settings import SERIAL_NUMBER, UNITREE_USERNAME, UNITREE_PASSWORD

# 디버깅
print("=== 환경변수 확인 ===")
print(f"SERIAL_NUMBER: {SERIAL_NUMBER}")
print(f"UNITREE_USERNAME: {UNITREE_USERNAME}")
print(f"UNITREE_PASSWORD: {'설정됨' if UNITREE_PASSWORD else 'None'}")
# 디버깅

logging.basicConfig(level=logging.FATAL)
logging.basicConfig(level=logging.INFO)

_conn_holder = {}

# 최신 조이스틱 값만 저장 (sitdown/situp 등은 큐 사용)
latest_joystick = None
robot_state = "unknown"  # 초기 상태는 unknown

def start_webrtc(frame_queue, command_queue):
    # av.logging.set_level(av.logging.ERROR) 
    av.logging.set_level(av.logging.DEBUG) # 로깅을 디버그 레벨로 변경

    async def recv_camera_stream(track: MediaStreamTrack):
        while True:
            try:
                frame = await track.recv()
                img = frame.to_ndarray(format="bgr24")
                frame_queue.put(img)
            except Exception as e:
                logging.error(f"Frame decode error: {e}")

    async def _ensure_normal_mode(conn):
        try:
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001}
            )
            if response['data']['header']['status']['code'] == 0:
                data = json.loads(response['data']['data'])
                current_motion_switcher_mode = data['name']
                print(f"[모드 확인] 현재 모드: {current_motion_switcher_mode}")
            else:
                print("[모드 확인] 현재 모드 조회 실패")

            if current_motion_switcher_mode != "normal":
                print(f"[모드 전환] {current_motion_switcher_mode} → normal 모드로 변경 시도")
                response2 = await conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["MOTION_SWITCHER"],
                    {"api_id": 1002, "parameter": {"name": "normal"}}
                )
                await asyncio.sleep(5)
                response3 = await conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001}
                )
                if response3['data']['header']['status']['code'] == 0:
                    data = json.loads(response3['data']['data'])
                    print(f"[모드 확인] 변경 후 모드: {data['name']}")
                else:
                    print("[모드 확인] 변경 후 모드 조회 실패")
        except Exception as e:
            print(f"[모드 확인] 에러 발생: {e}")

    async def handle_command(conn):
        global latest_joystick, robot_state
        while True:
            # sitdown/situp 등은 큐에서 처리
            if not command_queue.empty():
                direction = command_queue.get()
                if direction == "sitdown":
                    print("Performing 'StandDown' movement...")
                    await conn.datachannel.pub_sub.publish_request_new(
                        RTC_TOPIC["SPORT_MOD"],
                        {"api_id": SPORT_CMD["StandDown"]}
                    )
                    robot_state = "sitdown"
                elif direction == "situp":
                    print("Performing 'StandUp' movement...")
                    await conn.datachannel.pub_sub.publish_request_new(
                        RTC_TOPIC["SPORT_MOD"],
                        {"api_id": SPORT_CMD["StandUp"]}
                    )
                    print("Performing 'BalanceStand' movement...")
                    await conn.datachannel.pub_sub.publish_request_new(
                        RTC_TOPIC["SPORT_MOD"],
                        {"api_id": SPORT_CMD["BalanceStand"]}
                    )
                    robot_state = "situp"
                elif direction == "sit":
                    if robot_state == "situp":
                        print("Performing 'Sit' movement...")
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["Sit"]}
                        )
                        robot_state = "sit"
                    else:
                        print("Not situp, switching to situp first...")
                        # StandUp → BalanceStand → SitUp → Sit
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["StandUp"]}
                        )
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["BalanceStand"]}
                        )
                        robot_state = "situp"
                        print("Performing 'Sit' movement...")
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["Sit"]}
                        )
                        robot_state = "sit"
                elif direction == "hello":
                    if robot_state == "hello":
                        print("Performing 'Hello' movement...")
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["Hello"]}
                        )
                        robot_state = "hello"
                    else:
                        print("Not situp, switching to situp first...")
                        # StandUp → BalanceStand → SitUp → Sit
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["StandUp"]}
                        )
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["BalanceStand"]}
                        )
                        robot_state = "hello"
                        print("Performing 'Hello' movement...")
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["Hello"]}
                        )
                        robot_state = "hello"
                        
                # 기타 명령은 필요시 추가
            # 최신 조이스틱 값만 사용
            if latest_joystick is not None:
                _, x, z = latest_joystick
                print(f"Joystick command (latest): x={x}, z={z}")
                response = await conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["SPORT_MOD"],
                    {"api_id": SPORT_CMD["Move"], "parameter": {"x": float(x), "y": 0, "z": float(z)}}
                )
                print("Move response:", response)
            await asyncio.sleep(0.1)  # 50ms마다 최신 값 전송

    async def main_webrtc():
        token_manager = TokenManager()
        token = token_manager.get_token()
        conn = Go2WebRTCConnection(
            WebRTCConnectionMethod.Remote,
            serialNumber=SERIAL_NUMBER,
            username=UNITREE_USERNAME,
            password=UNITREE_PASSWORD
        )
        # 이후에도 토큰이 만료될 수 있으니, 필요시 token_manager.get_token()으로 갱신
        await conn.connect()
        conn.video.switchVideoChannel(True)
        conn.video.add_track_callback(recv_camera_stream)
        await _ensure_normal_mode(conn)
        asyncio.create_task(handle_command(conn))
        # 루프가 살아있도록 대기
        while True:
            await asyncio.sleep(1)

    # 메인 루프를 별도 스레드에서 실행
    def run_loop():
        asyncio.run(main_webrtc())

    threading.Thread(target=run_loop, daemon=True).start()

# 외부에서 명령을 큐에 넣는 함수
def send_command(command_queue, direction):
    global latest_joystick
    if isinstance(direction, tuple) and direction[0] == 'joystick':
        latest_joystick = direction  # 최신 값으로 덮어쓰기
    else:
        command_queue.put(direction)  # sitdown, situp 등은 기존 큐 사용

# 외부에서 normal 모드 전환을 요청할 때 호출
def ensure_normal_mode_once():
    import asyncio
    conn = _conn_holder.get('conn')
    if conn is None:
        print("No connection yet.")
        return False
    async def switch():
        await asyncio.sleep(1)  # 연결이 완전히 될 때까지 잠깐 대기
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001}
        )
        response = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001}
        )
        current_motion_switcher_mode = "normal"
        if response['data']['header']['status']['code'] == 0:
            data = json.loads(response['data']['data'])
            current_motion_switcher_mode = data['name']
        if current_motion_switcher_mode != "normal":
            await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"],
                {"api_id": 1002, "parameter": {"name": "normal"}}
            )
            await asyncio.sleep(10)
    threading.Thread(target=lambda: asyncio.run(switch()), daemon=True).start()
    return True

if __name__ == "__main__":
    frame_queue = Queue(maxsize=10)
    command_queue = Queue(maxsize=10)
    start_webrtc(frame_queue, command_queue)

    # 예시: 키보드 입력으로 명령 전달
    while True:
        if not frame_queue.empty():
            img = frame_queue.get()
            print(img.shape)
        else:
            time.sleep(0.01)
        direction = input("Enter direction (sitdown/situp): ")
        send_command(command_queue, direction)
