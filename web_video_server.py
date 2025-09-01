import cv2
import time
from flask import Flask, Response, render_template, request, jsonify
from multiprocessing import Queue
from webrtc_producer import start_webrtc, send_command, ensure_normal_mode_once
import threading
from ultralytics import YOLO
import logging
import json
import os
from datetime import datetime

# 🔧 ArUco 신원 시스템 import (오류 처리 추가)
try:
    from aruco_identity_system import ArUcoIdentitySystem
    ARUCO_AVAILABLE = True
    print("✅ ArUco 신원 시스템 모듈 로드 성공")
except ImportError as e:
    print(f"❌ ArUco 신원 시스템 모듈 로드 실패: {e}")
    ARUCO_AVAILABLE = False

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder='templates')
frame_queue = Queue(maxsize=10)
command_queue = Queue(maxsize=10)

# YOLO 모델 로드
try:
    yolo_model = YOLO('templates/best.pt')
    print("✅ YOLO 모델 로드 성공")
except Exception as e:
    print(f"❌ YOLO 모델 로드 실패: {e}")
    yolo_model = None

# 🔧 ArUco 신원 시스템 초기화 (안전하게)
aruco_identity_system = None
if ARUCO_AVAILABLE:
    try:
        aruco_identity_system = ArUcoIdentitySystem()
        print("✅ ArUco 신원 시스템 초기화 성공")
    except Exception as e:
        print(f"❌ ArUco 신원 시스템 초기화 실패: {e}")
        print(f"🔍 상세 오류: {e}")
        ARUCO_AVAILABLE = False

# WebRTC 프레임 수신 시작
start_webrtc(frame_queue, command_queue)

# 🔥 Fire 감지 추적 변수들
fire_detection_start_time = None
fire_continuous_detection = False
fire_last_alert_time = None
fire_detection_active = True
FIRE_DETECTION_THRESHOLD = 5.0
FIRE_CONFIDENCE_THRESHOLD = 0.5
FIRE_ALERT_INTERVAL = 5.0

# 🆕 YOLO 활성화 상태 변수
yolo_active = True

# 🆕 ArUco 스캔 관련 변수들
aruco_scan_mode = False
aruco_scan_start_time = None
aruco_scan_attempts = 0
aruco_last_retry_time = None
ARUCO_SCAN_TIMEOUT = 30.0
ARUCO_RETRY_INTERVAL = 2.0
MAX_ARUCO_ATTEMPTS = 10
aruco_last_detected_id = None
aruco_last_detection_time = None

def reset_aruco_scan_state():
    """ArUco 스캔 상태 초기화"""
    global aruco_scan_mode, aruco_scan_start_time, aruco_scan_attempts
    global aruco_last_retry_time, aruco_last_detected_id, aruco_last_detection_time
    
    aruco_scan_mode = False
    aruco_scan_start_time = None
    aruco_scan_attempts = 0
    aruco_last_retry_time = None
    aruco_last_detected_id = None
    aruco_last_detection_time = None

def auto_recover_system():
    """ArUco 스캔 완료 후 자동 시스템 복구 - 개선됨"""
    global yolo_active
    
    print("🔄 시스템 자동 복구 시작...")
    
    try:
        # 🆕 현재 로봇 상태 확인
        robot_status = get_robot_current_state()
        print(f"🤖 현재 로봇 상태: {robot_status}")
        
        # 🆕 sit 상태에서 standup으로 복구
        if robot_status in ['sit', 'sitdown', 'unknown']:
            print("🤖 sit 상태에서 standup 자세로 복구 중...")
            send_command(command_queue, 'standup')
            print("✅ standup 명령 전송 완료")
        else:
            print("🤖 이미 적절한 자세입니다.")
        
        # ArUco 스캔 상태 초기화
        reset_aruco_scan_state()
        
        # YOLO 재활성화
        yolo_active = True
        print("🔄 YOLO 모델 재활성화")
        
        print("✅ 시스템 자동 복구 완료")
        
    except Exception as e:
        print(f"❌ 시스템 자동 복구 오류: {e}")

def save_aruco_identity_result(marker_id, identity_info):
    """ArUco 신원 스캔 성공 결과 저장 (JSON 직렬화 오류 수정)"""
    
    # 🔧 numpy 타입을 Python 기본 타입으로 변환
    def convert_numpy_types(obj):
        """numpy 타입을 JSON 직렬화 가능한 타입으로 변환"""
        import numpy as np
        
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, dict):
            return {key: convert_numpy_types(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [convert_numpy_types(item) for item in obj]
        else:
            return obj
    
    # 🔧 안전한 marker_id 변환
    safe_marker_id = int(marker_id) if hasattr(marker_id, 'item') else int(marker_id)
    
    # 🔧 identity_info의 모든 값들을 안전하게 변환
    safe_identity_info = convert_numpy_types(identity_info) if identity_info else {}
    
    scan_data = {
        'timestamp': datetime.now().isoformat(),
        'alert_type': 'aruco_identity_success',
        'marker_info': {
            'marker_id': safe_marker_id,
            'name': safe_identity_info.get('name', 'Unknown'),
            'affiliation': safe_identity_info.get('affiliation', 'Unknown'),
            'employee_id': safe_identity_info.get('employee_id', ''),
            'role': safe_identity_info.get('role', ''),
            'access_level': safe_identity_info.get('access_level', 'standard'),
            'department': safe_identity_info.get('department', ''),
            'created_date': safe_identity_info.get('created_date', ''),
            'marker_type': safe_identity_info.get('marker_type', 'identity')
        },
        'scan_info': {
            'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'location': 'unitree_camera',
            'attempts': int(aruco_scan_attempts),  # 안전한 변환
            'max_attempts': int(MAX_ARUCO_ATTEMPTS),
            'scanner': 'aruco_identity_system',
            'scan_duration': float(time.time() - aruco_scan_start_time) if aruco_scan_start_time else 0.0
        },
        'message': f"🔖 {safe_identity_info.get('name', 'Unknown')}님이 ArUco 마커로 출입했습니다."
    }
    
    try:
        with open('.aruco_scan_result.json', 'w', encoding='utf-8') as f:
            json.dump(scan_data, f, ensure_ascii=False, indent=2)
        
        print(f"✅ ArUco 신원 스캔 결과 저장: {safe_identity_info.get('name', 'Unknown')}님 (마커 ID: {safe_marker_id})")
        print(f"📊 스캔 정보: {aruco_scan_attempts}번째 시도에서 성공")
        return True
        
    except Exception as e:
        print(f"❌ ArUco 신원 스캔 결과 저장 실패: {e}")
        import traceback
        print(f"🔍 오류 상세: {traceback.format_exc()}")
        return False

def save_aruco_scan_failure():
    """ArUco 신원 스캔 실패 결과 저장 (JSON 직렬화 오류 수정)"""
    failure_data = {
        'timestamp': datetime.now().isoformat(),
        'alert_type': 'aruco_identity_failure',
        'failure_info': {
            'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'location': 'unitree_camera',
            'attempts': int(aruco_scan_attempts),  # 안전한 변환
            'max_attempts': int(MAX_ARUCO_ATTEMPTS),
            'timeout': float(ARUCO_SCAN_TIMEOUT),
            'retry_interval': float(ARUCO_RETRY_INTERVAL),
            'scan_duration': float(time.time() - aruco_scan_start_time) if aruco_scan_start_time else 0.0
        },
        'message': f"❌ ArUco 신원 마커 스캔에 실패했습니다. ({aruco_scan_attempts}/{MAX_ARUCO_ATTEMPTS}번 시도)"
    }
    
    try:
        with open('.aruco_scan_result.json', 'w', encoding='utf-8') as f:
            json.dump(failure_data, f, ensure_ascii=False, indent=2)
        
        print(f"📝 ArUco 신원 스캔 실패 기록됨 ({aruco_scan_attempts}/{MAX_ARUCO_ATTEMPTS}번 시도)")
        return True
        
    except Exception as e:
        print(f"❌ ArUco 신원 스캔 실패 기록 저장 실패: {e}")
        return False

def process_aruco_identity_markers(img):
    """ArUco 신원 마커 스캔 및 처리 (JSON 직렬화 오류 수정)"""
    global aruco_scan_mode, aruco_scan_start_time, aruco_scan_attempts
    global aruco_last_retry_time, aruco_last_detected_id, aruco_last_detection_time
    
    if not aruco_scan_mode or not ARUCO_AVAILABLE or aruco_identity_system is None:
        return []
    
    current_time = time.time()
    
    # 2초마다 재시도
    if aruco_last_retry_time is None or current_time - aruco_last_retry_time >= ARUCO_RETRY_INTERVAL:
        aruco_scan_attempts += 1
        aruco_last_retry_time = current_time
        print(f"🔖 ArUco 신원 마커 스캔 시도 #{aruco_scan_attempts}/{MAX_ARUCO_ATTEMPTS}")
    
    detected_markers = []
    
    try:
        # ArUco 신원 마커 감지
        identities = aruco_identity_system.detect_identity_markers(img)
        
        if not identities:
            # 최대 시도 횟수 체크
            if aruco_scan_attempts >= MAX_ARUCO_ATTEMPTS:
                print(f"❌ ArUco 신원 스캔 최대 시도 횟수 초과 ({MAX_ARUCO_ATTEMPTS}번)")
                save_aruco_scan_failure()
                auto_recover_system()
                return []
            
            # 타임아웃 체크
            if aruco_scan_start_time and current_time - aruco_scan_start_time > ARUCO_SCAN_TIMEOUT:
                print(f"⏰ ArUco 신원 스캔 타임아웃 ({ARUCO_SCAN_TIMEOUT}초)")
                save_aruco_scan_failure()
                auto_recover_system()
                return []
            
            return []
        
        for identity_data in identities:
            # 🔧 안전한 타입 변환
            marker_id = int(identity_data['marker_id']) if hasattr(identity_data['marker_id'], 'item') else int(identity_data['marker_id'])
            identity_info = identity_data['identity_info']
            
            print(f"🔖 ArUco 신원 마커 감지: ID {marker_id}")
            
            if identity_info:
                name = identity_info.get('name', 'Unknown')
                affiliation = identity_info.get('affiliation', 'Unknown')
                
                # 중복 감지 방지 (3초 내 같은 마커 무시)
                if (aruco_last_detected_id == marker_id and 
                    aruco_last_detection_time and 
                    current_time - aruco_last_detection_time < 3.0):
                    print(f"   ⚠️ 중복 감지 방지: ID {marker_id} (3초 내 재감지)")
                    continue
                
                print(f"✅ ArUco 신원 마커 스캔 성공!")
                print(f"👤 이름: {name}")
                print(f"🏢 소속: {affiliation}")
                
                # 성공 결과 저장
                if save_aruco_identity_result(marker_id, identity_info):
                    print(f"📝 Discord 알림 파일 생성 완료")
                
                # 마지막 감지 정보 업데이트
                aruco_last_detected_id = marker_id
                aruco_last_detection_time = current_time
                
                # 성공 시 자동 시스템 복구
                auto_recover_system()
                
                # 🔧 안전한 bbox 변환
                bbox = identity_data['bbox']
                if hasattr(bbox[0], 'item'):  # numpy 타입인 경우
                    bbox = tuple(int(x.item()) if hasattr(x, 'item') else int(x) for x in bbox)
                
                # 🔧 안전한 corners 변환
                corners = identity_data['corners']
                if isinstance(corners, list) and len(corners) > 0:
                    try:
                        # numpy array를 일반 list로 변환
                        corners = [[float(point[0]), float(point[1])] for point in corners]
                    except:
                        corners = corners  # 이미 안전한 형태라면 그대로 사용
                
                # 🔧 안전한 center 변환
                center = identity_data['center']
                if hasattr(center[0], 'item'):
                    center = (int(center[0].item()), int(center[1].item()))
                
                detected_markers.append({
                    'marker_id': marker_id,
                    'bbox': bbox,
                    'corners': corners,
                    'center': center,
                    'identity_info': identity_info,
                    'status': 'valid',
                    'attempts': int(aruco_scan_attempts),
                    'scanner': 'aruco_identity',
                    'detection_time': float(current_time)
                })
                
                break
                
            else:
                print(f"❌ 알 수 없는 ArUco 마커: ID {marker_id}")
                
                # 🔧 안전한 bbox 변환
                bbox = identity_data['bbox']
                if hasattr(bbox[0], 'item'):
                    bbox = tuple(int(x.item()) if hasattr(x, 'item') else int(x) for x in bbox)
                
                # 🔧 안전한 corners 변환
                corners = identity_data['corners']
                if isinstance(corners, list) and len(corners) > 0:
                    try:
                        corners = [[float(point[0]), float(point[1])] for point in corners]
                    except:
                        corners = corners
                
                # 🔧 안전한 center 변환
                center = identity_data['center']
                if hasattr(center[0], 'item'):
                    center = (int(center[0].item()), int(center[1].item()))
                
                detected_markers.append({
                    'marker_id': marker_id,
                    'bbox': bbox,
                    'corners': corners,
                    'center': center,
                    'identity_info': None,
                    'status': 'unknown',
                    'attempts': int(aruco_scan_attempts),
                    'scanner': 'aruco_identity',
                    'detection_time': float(current_time)
                })
    
    except Exception as e:
        print(f"❌ ArUco 신원 마커 스캔 오류: {e}")
        import traceback
        print(f"🔍 오류 상세: {traceback.format_exc()}")
    
    return detected_markers

def save_fire_alert(is_repeat=False):
    """Fire 알림 정보를 파일에 저장 (Discord 봇이 읽을 수 있도록)"""
    current_time = time.time()
    detection_duration = current_time - fire_detection_start_time if fire_detection_start_time else 0
    
    alert_data = {
        'timestamp': datetime.now().isoformat(),
        'alert_type': 'fire_detected',
        'duration': detection_duration,
        'confidence': 'high',
        'location': 'unitree_camera',
        'is_repeat': is_repeat,
        'alert_count': int(detection_duration // FIRE_ALERT_INTERVAL) + 1,
        'message': f'🚨 화재가 {detection_duration:.1f}초간 연속 감지 중입니다!' if is_repeat else '🚨 화재가 5초 이상 연속 감지되었습니다!'
    }
    
    try:
        with open('.fire_alert.json', 'w') as f:
            json.dump(alert_data, f)
        
        if is_repeat:
            print(f"🚨 화재 반복 알림 #{alert_data['alert_count']} 저장됨 ({detection_duration:.1f}초)")
        else:
            print("🚨 화재 첫 알림 정보 저장됨 - Discord 봇이 처리할 예정")
            
    except Exception as e:
        print(f"❌ 알림 저장 실패: {e}")

def check_fire_detection(current_boxes):
    """Fire 감지 상태 확인 및 알림 처리 (반복 알림 포함)"""
    global fire_detection_start_time, fire_continuous_detection, fire_last_alert_time
    
    # 현재 프레임에서 고신뢰도 Fire 탐지 여부 확인
    high_confidence_fire = False
    max_confidence = 0.0
    
    for box_info in current_boxes:
        if len(box_info) >= 6:
            _, _, _, _, label, confidence = box_info
            if label == "fire" and confidence >= FIRE_CONFIDENCE_THRESHOLD:
                high_confidence_fire = True
                max_confidence = max(max_confidence, confidence)
    
    current_time = time.time()
    
    if high_confidence_fire:
        if not fire_continuous_detection:
            fire_detection_start_time = current_time
            fire_continuous_detection = True
            fire_last_alert_time = None
            print(f"🔥 Fire 감지 시작! (신뢰도 {max_confidence:.2f})")
        
        detection_duration = current_time - fire_detection_start_time
        
        if detection_duration >= FIRE_DETECTION_THRESHOLD and fire_last_alert_time is None:
            print(f"🚨 화재 첫 알림! ({detection_duration:.1f}초 연속 감지)")
            save_fire_alert(is_repeat=False)
            fire_last_alert_time = current_time
            
        elif (fire_last_alert_time is not None and 
              current_time - fire_last_alert_time >= FIRE_ALERT_INTERVAL):
            print(f"🚨 화재 반복 알림! (총 {detection_duration:.1f}초 연속 감지)")
            save_fire_alert(is_repeat=True)
            fire_last_alert_time = current_time
            
    else:
        # Fire 감지 안됨 - 상태 초기화
        if fire_continuous_detection:
            detection_duration = current_time - fire_detection_start_time
            print(f"🔥 Fire 감지 종료 (총 {detection_duration:.1f}초 감지됨)")
            
        fire_continuous_detection = False
        fire_detection_start_time = None
        fire_last_alert_time = None

# generate() 함수에 추가
def generate():
    last_detect_time = 0
    last_boxes = []
    last_aruco_markers = []  # ArUco 마커 결과 저장용
    
    while True:
        if not frame_queue.empty():
            img = frame_queue.get()
            now = time.time()
            
            # YOLO 감지 (yolo_active가 True일 때만)
            if yolo_active and now - last_detect_time > 1.0:
                results = yolo_model(img)
                last_boxes = []
                
                for result in results:
                    boxes = result.boxes
                    if boxes is not None:
                        for box in boxes:
                            cls = int(box.cls[0])
                            label = yolo_model.names[cls]
                            if label in ["person", "fire"]:
                                x1, y1, x2, y2 = map(int, box.xyxy[0])
                                confidence = float(box.conf[0])
                                last_boxes.append((x1, y1, x2, y2, label, confidence))
                
                check_fire_detection(last_boxes)
                last_detect_time = now
            elif not yolo_active:
                last_boxes = []
            
            # ArUco 신원 마커 스캔
            last_aruco_markers = process_aruco_identity_markers(img)
            
            # YOLO 결과 표시
            if yolo_active:
                for box_info in last_boxes:
                    if len(box_info) == 6:
                        x1, y1, x2, y2, label, confidence = box_info
                    else:
                        x1, y1, x2, y2 = box_info[:4]
                        label = "person"
                        confidence = 0.0
                    
                    if label == "fire":
                        color = (0, 0, 255)
                        display_text = f"FIRE {confidence:.2f}"
                        
                        if confidence >= FIRE_CONFIDENCE_THRESHOLD and fire_continuous_detection:
                            if int(time.time() * 2) % 2:
                                color = (0, 255, 255)
                            display_text = f"FIRE {confidence:.2f}"
                            
                    elif label == "person":
                        color = (0, 255, 0)
                        display_text = f"PERSON {confidence:.2f}"
                    
                    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(img, display_text, (x1, y1 - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            
            # ArUco 신원 마커 표시
            for marker_data in last_aruco_markers:
                marker_id = marker_data['marker_id']
                x1, y1, x2, y2 = marker_data['bbox']
                center = marker_data['center']
                status = marker_data['status']
                
                if status == 'valid':
                    color = (255, 0, 255)  # 마젠타
                    identity_info = marker_data.get('identity_info', {})
                    name = identity_info.get('name', 'Unknown')
                    affiliation = identity_info.get('affiliation', 'Unknown')
                    display_text = f"ID{marker_id}: {name} ({affiliation})"
                else:
                    color = (0, 255, 255)  # 노란색
                    display_text = f"ID{marker_id}: Unknown"
                
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                cv2.circle(img, center, 5, color, -1)
                cv2.putText(img, display_text, (x1, y1 - 10), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
            # Fire 감지 상태 표시
            if yolo_active and fire_continuous_detection and fire_detection_start_time:
                detection_duration = time.time() - fire_detection_start_time
                status_text = f"Fire detecting..: {detection_duration:.1f}s"
                
                if detection_duration >= FIRE_DETECTION_THRESHOLD:
                    status_color = (0, 0, 255)
                    if fire_last_alert_time is not None:
                        elapsed_alert_time = time.time() - fire_last_alert_time
                        if elapsed_alert_time < FIRE_ALERT_INTERVAL:
                            status_text += f" ready for alarm ({elapsed_alert_time:.1f}s)"
                else:
                    status_color = (0, 165, 255)
                
                cv2.putText(img, status_text, (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
            
            # ArUco 스캔 상태 표시
            if aruco_scan_mode and aruco_scan_start_time:
                scan_duration = time.time() - aruco_scan_start_time
                aruco_status_text = f"ArUco scanning: #{aruco_scan_attempts}/{MAX_ARUCO_ATTEMPTS} ({scan_duration:.1f}s)"
                aruco_status_color = (255, 0, 255)  # 마젠타
                
                cv2.putText(img, aruco_status_text, (10, 60), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, aruco_status_color, 2)
            
            # YOLO 상태 표시
            yolo_status_text = f"YOLO: {'ON' if yolo_active else 'OFF'}"
            yolo_status_color = (0, 255, 0) if yolo_active else (0, 0, 255)
            cv2.putText(img, yolo_status_text, (10, 90), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, yolo_status_color, 2)
            
            ret, jpeg = cv2.imencode('.jpg', img)
            if not ret:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        else:
            time.sleep(0.01)

@app.route('/video_feed')
def video_feed():
    return Response(generate(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/move', methods=['POST'])
def move():
    data = request.get_json()
    direction = data.get('direction')
    send_command(command_queue, direction)
    return jsonify({'status': 'ok', 'direction': direction})

@app.route('/joystick', methods=['POST'])
def joystick():
    data = request.get_json()
    x = float(data.get('x', 0))
    z = float(data.get('z', 0))
    send_command(command_queue, ('joystick', x, z))
    return jsonify({'status': 'ok'})

@app.route('/start_control', methods=['POST'])
def start_control():
    ok = ensure_normal_mode_once()
    return jsonify({'status': 'ok' if ok else 'fail'})

# 🆕 ArUco 스캔 관련 엔드포인트들
@app.route('/start_aruco_scan', methods=['POST'])
def start_aruco_scan():
    """sit 버튼 동작: YOLO 비활성화 후 ArUco 스캔 시작"""
    global yolo_active, aruco_scan_mode, aruco_scan_start_time, aruco_scan_attempts
    
    try:
        if aruco_scan_mode:
            return jsonify({
                'status': 'error',
                'message': '이미 ArUco 스캔이 진행 중입니다.'
            })
        
        print("🔖 ArUco 신원 스캔 프로세스 시작!")
        
        # 먼저 앉기 명령
        send_command(command_queue, 'sit')
        print("🤖 앉기 자세 명령 전송")
        
        # YOLO 비활성화
        yolo_active = False
        print("🎯 YOLO 모델 비활성화 (ArUco 스캔 최적화)")
        
        # ArUco 스캔 시작
        aruco_scan_mode = True
        aruco_scan_start_time = time.time()
        aruco_scan_attempts = 0
        
        return jsonify({
            'status': 'success',
            'message': 'ArUco 신원 스캔이 시작되었습니다.',
            'max_attempts': MAX_ARUCO_ATTEMPTS,
            'timeout': ARUCO_SCAN_TIMEOUT,
            'yolo_disabled': True
        })
        
    except Exception as e:
        print(f"❌ ArUco 스캔 시작 오류: {e}")
        return jsonify({
            'status': 'error',
            'message': f'ArUco 스캔 시작 실패: {str(e)}'
        })

@app.route('/stop_aruco_scan', methods=['POST'])
def stop_aruco_scan():
    """ArUco 스캔 중지 및 시스템 복구 - 개선됨"""
    global yolo_active, aruco_scan_mode
    
    try:
        print("🛑 ArUco 스캔 중지 및 시스템 복구")
        
        attempts_made = aruco_scan_attempts
        
        # 🆕 현재 로봇 상태 확인
        robot_status = get_robot_current_state()
        print(f"🤖 현재 로봇 상태: {robot_status}")
        
        # 🆕 sit 또는 sitdown 상태라면 standup으로 복구
        if robot_status in ['sit', 'sitdown', 'unknown']:
            print("🤖 sit 상태에서 standup 자세로 복구 중...")
            send_command(command_queue, 'standup')
            print("✅ standup 명령 전송 완료")
            
            # 1초 대기 후 상태 재확인
            time.sleep(1)
            new_status = get_robot_current_state()
            print(f"🤖 복구 후 로봇 상태: {new_status}")
        else:
            print("🤖 이미 적절한 자세입니다.")
        
        # ArUco 스캔 상태 초기화
        reset_aruco_scan_state()
        
        # YOLO 재활성화
        yolo_active = True
        print("🔄 YOLO 모델 재활성화")
        
        return jsonify({
            'status': 'success',
            'message': 'ArUco 스캔이 중지되고 시스템이 복구되었습니다.',
            'attempts_made': attempts_made,
            'yolo_reactivated': True,
            'robot_recovered': True,
            'previous_robot_state': robot_status
        })
        
    except Exception as e:
        print(f"❌ ArUco 스캔 중지 오류: {e}")
        return jsonify({
            'status': 'error',
            'message': f'ArUco 스캔 중지 실패: {str(e)}'
        })

@app.route('/aruco_scan_status')
def aruco_scan_status():
    """ArUco 스캔 상태 조회"""
    remaining_time = 0
    if aruco_scan_start_time and aruco_scan_mode:
        elapsed = time.time() - aruco_scan_start_time
        remaining_time = max(0, ARUCO_SCAN_TIMEOUT - elapsed)
    
    return jsonify({
        'aruco_scan_mode': aruco_scan_mode,
        'aruco_scan_attempts': aruco_scan_attempts,
        'max_attempts': MAX_ARUCO_ATTEMPTS,
        'remaining_time': remaining_time,
        'yolo_active': yolo_active,
        'scan_timeout': ARUCO_SCAN_TIMEOUT,
        'retry_interval': ARUCO_RETRY_INTERVAL
    })

# 🆕 간소화된 Discord 음성 연동 관련 엔드포인트들

@app.route('/voice_connect', methods=['POST'])
def voice_connect():
    """Discord 음성 채널 연결 & 자동 브리지 시작"""
    try:
        voice_command = {
            'command': 'voice_connect',  # 연결과 동시에 브리지 시작
            'timestamp': datetime.now().isoformat()
        }
        
        with open('.voice_command.json', 'w') as f:
            json.dump(voice_command, f)
        
        return jsonify({
            'status': 'success',
            'message': 'Discord 음성 채널 연결 및 브리지 시작 요청됨'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'음성 채널 연결 실패: {str(e)}'
        })

@app.route('/voice_disconnect', methods=['POST'])
def voice_disconnect():
    """Discord 음성 브리지 중지 & 채널 퇴장"""
    try:
        voice_command = {
            'command': 'voice_disconnect',  # 브리지 중지와 동시에 퇴장
            'timestamp': datetime.now().isoformat()
        }
        
        with open('.voice_command.json', 'w') as f:
            json.dump(voice_command, f)
        
        return jsonify({
            'status': 'success',
            'message': 'Discord 음성 브리지 중지 및 퇴장 요청됨'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'음성 채널 연결 해제 실패: {str(e)}'
        })

# voice_status 엔드포인트는 기존과 동일하게 유지
@app.route('/voice_status')
def voice_status():
    """음성 연동 상태 확인"""
    try:
        if os.path.exists('.voice_status.json'):
            with open('.voice_status.json', 'r') as f:
                status = json.load(f)
            return jsonify(status)
        else:
            return jsonify({
                'voice_connected': False,
                'bridge_active': False,
                'last_activity': None
            })
            
    except Exception as e:
        return jsonify({
            'voice_connected': False,
            'bridge_active': False,
            'error': str(e)
        })

def get_connection_status():
    """현재 WebRTC 연결 상태 반환"""
    try:
        # 🔧 _conn_holder import 추가
        from webrtc_producer import _conn_holder
        
        if _conn_holder and 'conn' in _conn_holder and _conn_holder['conn']:
            conn = _conn_holder['conn']
            
            # 연결 상태 확인
            status = {
                'connected': True,
                'has_datachannel': hasattr(conn, 'datachannel') and conn.datachannel is not None,
                'has_video': hasattr(conn, 'video') and conn.video is not None,
                'has_audio': hasattr(conn, 'audio') and conn.audio is not None,
                'connection_time': getattr(conn, '_connection_time', 'Unknown')
            }
            
            return status
        else:
            return {
                'connected': False,
                'has_datachannel': False,
                'has_video': False, 
                'has_audio': False,
                'connection_time': None
            }
    except Exception as e:
        print(f"❌ 연결 상태 확인 오류: {e}")
        return {
            'connected': False,
            'error': str(e)
        }

def is_connection_ready_for_audio():
    """오디오 브리지를 위한 연결 준비 상태 확인"""
    status = get_connection_status()
    return status.get('connected', False) and status.get('has_datachannel', False)

def get_robot_current_state():
    """현재 로봇 상태를 webrtc_producer에서 가져오기"""
    try:
        from webrtc_producer import get_robot_status
        status = get_robot_status()
        return status.get('robot_state', 'unknown')
    except Exception as e:
        print(f"⚠️ 로봇 상태 조회 실패: {e}")
        return 'unknown'

if __name__ == "__main__":
    print("🚀 Unitree 웹 비디오 서버 시작!")
    
    if ARUCO_AVAILABLE and aruco_identity_system:
        print(f"🔖 ArUco 신원 확인 시스템 준비 완료")
    else:
        print(f"⚠️ ArUco 신원 확인 시스템 비활성화됨")
    
    if yolo_model:
        print(f"🔥 화재 감지 시스템 활성화")
    else:
        print(f"⚠️ YOLO 모델 로드 실패 - 화재 감지 비활성화")
    
    print(f"🕹️ 조이스틱 제어 시스템 준비")
    app.run(host='0.0.0.0', port=5010, debug=False)


'''
@misc{lin2015microsoft,
      title={Microsoft COCO: Common Objects in Context},
      author={Tsung-Yi Lin and Michael Maire and Serge Belongie and Lubomir Bourdev and Ross Girshick and James Hays and Pietro Perona and Deva Ramanan and C. Lawrence Zitnick and Piotr Dollár},
      year={2015},
      eprint={1405.0312},
      archivePrefix={arXiv},
      primaryClass={cs.CV}
}
'''