import cv2
import time
import numpy as np
from flask import Flask, Response, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from multiprocessing import Queue
from webrtc_producer import start_webrtc, send_command, ensure_normal_mode_once
import threading
from ultralytics import YOLO
import logging
import json
import os
from datetime import datetime
import asyncio
import traceback

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
socketio = SocketIO(app, cors_allowed_origins="*")
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

# 🆕 LIDAR 모드 전환 변수들
lidar_view_mode = False  # False: 비디오 모드, True: LIDAR 모드
lidar_enabled = False
lidar_task = None
lidar_connection = None
message_count = 0

# 🆕 LIDAR 상수들 (plot_lidar_stream.py와 동일)
ROTATE_X_ANGLE = np.pi / 2  # 90 degrees
ROTATE_Z_ANGLE = np.pi      # 180 degrees
minYValue = 0
maxYValue = 100

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

# generate() 함수에서 YOLO 로직 완전 복원
def generate():
    """비디오 스트림 생성 - 해상도 제어 + 완전한 YOLO 로직"""
    last_detect_time = 0
    last_boxes = []
    last_aruco_markers = []
    
    # 🆕 목표 해상도 설정
    TARGET_WIDTH = 640
    TARGET_HEIGHT = 360
    JPEG_QUALITY = 85  # JPEG 품질 (1-100)
    
    while True:
        if not frame_queue.empty():
            img = frame_queue.get()
            now = time.time()
            
            # 🆕 이미지 해상도 확인 및 조정
            original_height, original_width = img.shape[:2]
            
            if original_width != TARGET_WIDTH or original_height != TARGET_HEIGHT:
                # 비율 유지하면서 리사이즈
                aspect_ratio = original_width / original_height
                target_aspect_ratio = TARGET_WIDTH / TARGET_HEIGHT
                
                if aspect_ratio > target_aspect_ratio:
                    # 가로가 더 긴 경우
                    new_width = TARGET_WIDTH
                    new_height = int(TARGET_WIDTH / aspect_ratio)
                else:
                    # 세로가 더 긴 경우
                    new_height = TARGET_HEIGHT
                    new_width = int(TARGET_HEIGHT * aspect_ratio)
                
                # 이미지 리사이즈
                img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_LANCZOS4)
                
                # 중앙 정렬을 위한 패딩 (필요한 경우)
                if new_width != TARGET_WIDTH or new_height != TARGET_HEIGHT:
                    # 검은색 배경에 중앙 정렬
                    pad_img = np.zeros((TARGET_HEIGHT, TARGET_WIDTH, 3), dtype=np.uint8)
                    start_y = (TARGET_HEIGHT - new_height) // 2
                    start_x = (TARGET_WIDTH - new_width) // 2
                    pad_img[start_y:start_y+new_height, start_x:start_x+new_width] = img
                    img = pad_img
                
                print(f"📺 해상도 조정: {original_width}x{original_height} → {TARGET_WIDTH}x{TARGET_HEIGHT}")
            
            # 🔧 YOLO 감지 (완전한 기존 로직 복원)
            if yolo_active and yolo_model and now - last_detect_time > 1.0:
                try:
                    # YOLO 추론 실행
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
                    
                    # 🔥 Fire 감지 체크 (기존 로직 완전 유지)
                    check_fire_detection(last_boxes)
                    last_detect_time = now
                    
                except Exception as e:
                    print(f"❌ YOLO 처리 오류: {e}")
                    last_boxes = []
                    
            elif not yolo_active:
                # YOLO 비활성화 시 빈 배열
                last_boxes = []
            
            # 🔖 ArUco 신원 마커 스캔 (기존 로직 완전 유지)
            last_aruco_markers = process_aruco_identity_markers(img)
            
            # 🎯 YOLO 결과 표시 (기존 로직 완전 복원)
            if yolo_active and last_boxes:
                for box_info in last_boxes:
                    if len(box_info) == 6:
                        x1, y1, x2, y2, label, confidence = box_info
                    else:
                        x1, y1, x2, y2 = box_info[:4]
                        label = "person"
                        confidence = 0.0
                    
                    # 🔥 Fire 감지 시 색상 및 텍스트
                    if label == "fire":
                        color = (0, 0, 255)  # 빨간색
                        display_text = f"FIRE {confidence:.2f}"
                        
                        # 🔥 Fire 연속 감지 시 깜빡임 효과
                        if confidence >= FIRE_CONFIDENCE_THRESHOLD and fire_continuous_detection:
                            if int(time.time() * 2) % 2:  # 0.5초마다 깜빡임
                                color = (0, 255, 255)  # 노란색으로 깜빡임
                            display_text = f"🚨 FIRE {confidence:.2f} 🚨"
                            
                    elif label == "person":
                        color = (0, 255, 0)  # 초록색
                        display_text = f"PERSON {confidence:.2f}"
                    
                    # 바운딩 박스 및 텍스트 그리기
                    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(img, display_text, (x1, y1 - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            
            # 🔖 ArUco 신원 마커 표시 (기존 로직 완전 유지)
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
                
                # ArUco 마커 표시
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                cv2.circle(img, center, 5, color, -1)
                cv2.putText(img, display_text, (x1, y1 - 10), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
            # 🔥 Fire 감지 상태 표시 (기존 로직 완전 복원)
            if yolo_active and fire_continuous_detection and fire_detection_start_time:
                detection_duration = time.time() - fire_detection_start_time
                status_text = f"Fire detecting: {detection_duration:.1f}s"
                
                if detection_duration >= FIRE_DETECTION_THRESHOLD:
                    status_color = (0, 0, 255)  # 빨간색
                    if fire_last_alert_time is not None:
                        elapsed_alert_time = time.time() - fire_last_alert_time
                        if elapsed_alert_time < FIRE_ALERT_INTERVAL:
                            status_text += f" (next alarm in {FIRE_ALERT_INTERVAL - elapsed_alert_time:.1f}s)"
                else:
                    status_color = (0, 165, 255)  # 주황색
                
                cv2.putText(img, status_text, (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
            
            # 🔖 ArUco 스캔 상태 표시 (기존 로직 완전 유지)
            if aruco_scan_mode and aruco_scan_start_time:
                scan_duration = time.time() - aruco_scan_start_time
                aruco_status_text = f"ArUco scanning: #{aruco_scan_attempts}/{MAX_ARUCO_ATTEMPTS} ({scan_duration:.1f}s)"
                aruco_status_color = (255, 0, 255)  # 마젠타
                
                cv2.putText(img, aruco_status_text, (10, 60), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, aruco_status_color, 2)
            
            # 🎯 YOLO 상태 표시 (기존 로직 완전 유지)
            yolo_status_text = f"YOLO: {'ON' if yolo_active else 'OFF'}"
            yolo_status_color = (0, 255, 0) if yolo_active else (0, 0, 255)
            cv2.putText(img, yolo_status_text, (10, 90), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, yolo_status_color, 2)
            
            # 🆕 JPEG 인코딩 품질 제어
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
            ret, jpeg = cv2.imencode('.jpg', img, encode_params)
            
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

# 🆕 YOLO 토글 엔드포인트 (ArUco 스캔과 동일한 단순 방식)
@app.route('/toggle_yolo', methods=['POST'])
def toggle_yolo():
    """YOLO 활성화/비활성화 토글 - ArUco 스캔과 동일한 단순 방식"""
    global yolo_active
    
    try:
        # 🔧 현재 상태 저장
        previous_state = yolo_active
        
        # 🔧 상태 토글 (단순하게)
        yolo_active = not yolo_active
        
        status_text = "활성화" if yolo_active else "비활성화"
        print(f"🎯 YOLO 상태 변경: {previous_state} → {yolo_active} ({status_text})")
        
        # 🔧 ArUco 스캔과 동일한 방식의 응답
        response_data = {
            'status': 'success',
            'yolo_active': yolo_active,
            'message': f'YOLO가 {status_text}되었습니다'
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"❌ YOLO 토글 처리 오류: {e}")
        
        return jsonify({
            'status': 'error',
            'message': f'YOLO 토글 처리 실패: {str(e)}',
            'yolo_active': yolo_active
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

# 🆕 LIDAR 관련 함수들 추가

def rotate_points(points, x_angle, z_angle):
    """Rotate points around the x and z axes by given angles."""
    rotation_matrix_x = np.array([
        [1, 0, 0],
        [0, np.cos(x_angle), -np.sin(x_angle)],
        [0, np.sin(x_angle), np.cos(x_angle)]
    ])
    
    rotation_matrix_z = np.array([
        [np.cos(z_angle), -np.sin(z_angle), 0],
        [np.sin(z_angle), np.cos(z_angle), 0],
        [0, 0, 1]
    ])
    
    points = points @ rotation_matrix_x.T
    points = points @ rotation_matrix_z.T
    return points

async def lidar_callback_task(message):
    """Task to process incoming LIDAR data - plot_lidar_stream.py와 거의 동일"""
    global message_count, minYValue, maxYValue
    
    try:
        # 🔧 LIDAR 활성화 상태만 체크 (뷰 모드와 무관하게 데이터 처리)
        if not lidar_enabled:
            return
            
        # 🔧 첫 번째 메시지 수신 시 알림
        if message_count == 0:
            print("🎉 첫 번째 LIDAR 메시지 수신!")
            
        # 🔧 plot_lidar_stream.py와 동일한 skip 로직 (현재는 모든 메시지 처리)
        if message_count % 1 != 0:  # args.skip_mod 대신 1 사용
            message_count += 1
            return

        # 🔧 데이터 추출 (plot_lidar_stream.py와 동일)
        positions = message["data"]["data"].get("positions", [])
        origin = message["data"].get("origin", [])
        
        # 🔧 positions가 numpy 배열인지 확인하고 안전하게 처리
        positions_length = 0
        has_positions = False
        
        if positions is not None:
            if hasattr(positions, '__len__'):
                positions_length = len(positions)
                has_positions = positions_length > 0
            else:
                has_positions = False
        
        print(f"🔍 LIDAR 데이터 구조 확인: positions 길이={positions_length}, origin={origin}")
        
        if not has_positions:
            message_count += 1
            print(f"⚠️ 빈 LIDAR 데이터 (메시지 #{message_count})")
            return
            
        # 🔧 포인트 변환 (plot_lidar_stream.py와 동일)
        points = np.array([positions[i:i+3] for i in range(0, len(positions), 3)], dtype=np.float32)
        total_points = len(points)
        unique_points = np.unique(points, axis=0)
        
        if len(unique_points) == 0:
            message_count += 1
            print(f"⚠️ unique_points가 0개 (메시지 #{message_count})")
            return

        # 🔧 회전 및 필터링 (plot_lidar_stream.py와 동일)
        rotated_points = rotate_points(unique_points, ROTATE_X_ANGLE, ROTATE_Z_ANGLE)
        filtered_points = rotated_points[(rotated_points[:, 1] >= minYValue) & (rotated_points[:, 1] <= maxYValue)]
        
        if len(filtered_points) == 0:
            message_count += 1
            print(f"⚠️ filtered_points가 0개 (메시지 #{message_count})")
            return

        # 🔧 중심점 계산 (plot_lidar_stream.py와 동일)
        center_x = float(np.mean(filtered_points[:, 0]))
        center_y = float(np.mean(filtered_points[:, 1]))
        center_z = float(np.mean(filtered_points[:, 2]))

        # 🔧 중심점으로 오프셋 (plot_lidar_stream.py와 동일)
        offset_points = filtered_points - np.array([center_x, center_y, center_z])

        # 🔧 로그 메시지 (plot_lidar_stream.py와 동일)
        message_count += 1
        print(f"📡 LIDAR Message {message_count}: Total points={total_points}, Unique points={len(unique_points)}, Filtered={len(filtered_points)}")

        # 🔧 거리 기반 색상 스칼라 (plot_lidar_stream.py와 동일)
        scalars = np.linalg.norm(offset_points, axis=1)

        # 🔧 SocketIO로 LIDAR 데이터 전송 (plot_lidar_stream.py와 동일)
        socketio.emit("lidar_data", {
            "points": offset_points.tolist(),
            "scalars": scalars.tolist(),
            "center": {"x": center_x, "y": center_y, "z": center_z}
        })
        print(f"📤 SocketIO로 {len(offset_points)}개 포인트 전송됨")

    except Exception as e:
        print(f"❌ LIDAR 콜백 오류: {e}")
        print(f"🔍 상세 오류: {traceback.format_exc()}")

async def lidar_webrtc_connection():
    """LIDAR WebRTC 연결 및 데이터 처리 - 자동 재연결 기능 포함"""
    global lidar_connection, message_count
    
    max_retries = 3
    retry_delay = 5
    last_message_count = 0
    connection_health_check_interval = 10  # 10초마다 연결 상태 확인
    
    while lidar_enabled:
        try:
            # � 기존 연결 재사용 시도
            from webrtc_producer import _conn_holder
            
            conn = None
            use_existing_connection = False
            
            if _conn_holder and 'conn' in _conn_holder and _conn_holder['conn']:
                potential_conn = _conn_holder['conn']
                print("🔗 기존 WebRTC 연결 상태 확인 중...")
                
                # 🆕 연결 상태 확인
                connection_state = 'unknown'
                if hasattr(potential_conn, '_peer_connection'):
                    connection_state = getattr(potential_conn._peer_connection, 'connectionState', 'unknown')
                    print(f"📡 WebRTC 연결 상태: {connection_state}")
                
                if connection_state in ['connected', 'connecting'] and hasattr(potential_conn, 'datachannel') and potential_conn.datachannel:
                    print("✅ 기존 WebRTC 연결을 LIDAR용으로 재사용")
                    conn = potential_conn
                    use_existing_connection = True
                else:
                    print(f"⚠️ 기존 연결 상태 불량 ({connection_state}) - 새 연결 생성 필요")
            
            # 새 연결 생성 (기존 연결이 없거나 상태가 불량한 경우)
            if not use_existing_connection:
                print("🔄 새로운 LIDAR 전용 WebRTC 연결 생성 중...")
                
                from go2_webrtc_connect.go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
                from config.settings import SERIAL_NUMBER, UNITREE_USERNAME, UNITREE_PASSWORD
                
                conn = Go2WebRTCConnection(
                    WebRTCConnectionMethod.Remote,
                    serialNumber=SERIAL_NUMBER,
                    username=UNITREE_USERNAME,
                    password=UNITREE_PASSWORD
                )
                
                print("� LIDAR WebRTC 연결 시도...")
                await conn.connect()
                print("✅ LIDAR WebRTC 연결 성공")
            
            # 트래픽 저장 모드 비활성화
            print("🔄 트래픽 저장 모드 비활성화 시도...")
            try:
                if asyncio.iscoroutinefunction(conn.datachannel.disableTrafficSaving):
                    await asyncio.wait_for(
                        conn.datachannel.disableTrafficSaving(True),
                        timeout=5.0  # 5초 타임아웃
                    )
                    print("✅ 트래픽 저장 모드 비활성화됨 (비동기)")
                else:
                    conn.datachannel.disableTrafficSaving(True)
                    print("✅ 트래픽 저장 모드 비활성화됨 (동기)")
            except asyncio.TimeoutError:
                print("⚠️ 트래픽 저장 모드 설정 타임아웃 - 계속 진행")
            except Exception as traffic_err:
                print(f"⚠️ 트래픽 저장 모드 설정 건너뜀: {traffic_err}")
            
            # LIDAR 센서 ON 명령
            print("🔄 LIDAR 센서 활성화 시도...")
            conn.datachannel.pub_sub.publish_without_callback("rt/utlidar/switch", "on")
            print("✅ LIDAR 센서 'ON' 명령 전송됨")
            
            # 센서 초기화 대기
            print("⏳ LIDAR 센서 초기화 대기 중...")
            await asyncio.sleep(3)
            
            # LIDAR 데이터 구독
            print("🔄 LIDAR 데이터 구독 시도...")
            conn.datachannel.pub_sub.subscribe(
                "rt/utlidar/voxel_map_compressed",
                lambda message: asyncio.create_task(lidar_callback_task(message))
            )
            print("📡 LIDAR 데이터 구독 시작")
            print(f"📊 구독 토픽: rt/utlidar/voxel_map_compressed")
            
            lidar_connection = conn
            last_message_count = message_count
            
            # 🆕 연결 상태 모니터링 루프
            print("🔄 LIDAR 연결 상태 모니터링 시작...")
            health_check_counter = 0
            
            while lidar_enabled:
                await asyncio.sleep(2)
                health_check_counter += 1
                
                # 주기적으로 연결 상태 확인
                if health_check_counter >= (connection_health_check_interval // 2):
                    health_check_counter = 0
                    
                    # 메시지 수신 확인
                    if message_count == last_message_count:
                        print(f"⚠️ LIDAR 데이터 수신 중단 감지 (메시지 카운트: {message_count})")
                        print("🔄 연결 재시작 중...")
                        break
                    else:
                        print(f"✅ LIDAR 데이터 정상 수신 중 (메시지: {message_count})")
                        last_message_count = message_count
                    
                    # WebRTC 연결 상태 확인
                    if hasattr(conn, '_peer_connection'):
                        connection_state = getattr(conn._peer_connection, 'connectionState', 'unknown')
                        if connection_state in ['closed', 'failed', 'disconnected']:
                            print(f"❌ WebRTC 연결 끊어짐 감지: {connection_state}")
                            print("🔄 연결 재시작 중...")
                            break
            
            # while 루프가 정상 종료된 경우 (lidar_enabled가 False)
            if not lidar_enabled:
                print("🛑 LIDAR 모니터링 종료")
                break
                
        except Exception as e:
            print(f"❌ LIDAR WebRTC 연결 오류: {e}")
            print(f"🔍 상세 오류: {traceback.format_exc()}")
            
            # 연결 실패 시 재시도 대기
            if lidar_enabled:
                print(f"⏳ {retry_delay}초 후 LIDAR 연결 재시도...")
                await asyncio.sleep(retry_delay)
                continue
            else:
                break
    
    print("🏁 LIDAR WebRTC 연결 함수 종료")

def start_lidar_stream():
    """LIDAR 스트림 시작"""
    global lidar_enabled, lidar_task
    
    if lidar_enabled:
        print("⚠️ LIDAR 스트림이 이미 실행 중입니다")
        return False
    
    lidar_enabled = True
    
    def run_lidar():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(lidar_webrtc_connection())
    
    lidar_task = threading.Thread(target=run_lidar, daemon=True)
    lidar_task.start()
    print("🚀 LIDAR 스트림 시작됨")
    return True

def stop_lidar_stream():
    """LIDAR 스트림 중지"""
    global lidar_enabled, lidar_connection
    
    if not lidar_enabled:
        return False
        
    lidar_enabled = False
    
    try:
        if lidar_connection and hasattr(lidar_connection, 'datachannel'):
            lidar_connection.datachannel.pub_sub.publish_without_callback("rt/utlidar/switch", "off")
            print("📡 LIDAR 센서 비활성화됨")
    except Exception as e:
        print(f"⚠️ LIDAR 센서 비활성화 오류: {e}")
    
    print("🛑 LIDAR 스트림 중지됨")
    return True

# 🔄 LIDAR 뷰 토글 라우트
@app.route('/toggle_lidar_view', methods=['POST'])
def toggle_lidar_view():
    """비디오와 LIDAR 뷰 간 전환 - 자동 재연결 기능 포함"""
    global lidar_view_mode
    
    try:
        lidar_view_mode = not lidar_view_mode
        print(f"🔄 뷰 모드 전환: {'LIDAR' if lidar_view_mode else '비디오'}")
        
        if lidar_view_mode:
            # LIDAR 뷰로 전환 시 LIDAR 스트림 시작/재시작
            if not lidar_enabled:
                print("🚀 LIDAR 뷰 전환: LIDAR 스트림 시작")
                start_lidar_stream()
            else:
                # 이미 실행 중이지만 연결 상태 확인
                print("🔍 LIDAR 스트림 상태 확인 중...")
                
                # 최근 메시지 수신 여부 확인
                global message_count
                old_count = message_count
                import time
                time.sleep(2)
                
                if message_count == old_count:
                    print("⚠️ LIDAR 데이터 수신 중단 감지 - 재시작 중...")
                    stop_lidar_stream()
                    time.sleep(1)
                    start_lidar_stream()
                    return jsonify({
                        'success': True,
                        'lidar_view_mode': lidar_view_mode,
                        'lidar_enabled': True,
                        'message': 'LIDAR 뷰로 전환 (연결 재시작됨)'
                    })
                else:
                    print("✅ LIDAR 스트림이 정상 작동 중")
        else:
            # 비디오 뷰로 전환 시에도 LIDAR 스트림 유지
            print("📹 비디오 뷰로 전환 (LIDAR 백그라운드 유지)")
            
        return jsonify({
            'success': True,
            'lidar_view_mode': lidar_view_mode,
            'lidar_enabled': lidar_enabled,
            'message': f"{'LIDAR' if lidar_view_mode else '비디오'} 뷰로 전환되었습니다"
        })
        
    except Exception as e:
        print(f"❌ 뷰 토글 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})

# 🆕 LIDAR 제어 라우트들
@app.route('/start_lidar', methods=['POST'])
def start_lidar():
    """LIDAR 스트림 시작"""
    try:
        print("🚀 LIDAR 스트림 수동 시작 요청")
        if start_lidar_stream():
            return jsonify({'success': True, 'message': 'LIDAR 스트림이 시작되었습니다'})
        else:
            # 이미 실행 중이라면 상태 확인 후 재시작
            print("⚠️ LIDAR 스트림이 이미 실행 중 - 상태 확인 중...")
            global lidar_enabled, message_count
            
            # 메시지 카운트가 증가하지 않으면 재시작
            old_count = message_count
            import time
            time.sleep(3)
            
            if message_count == old_count:
                print("🔄 LIDAR 데이터 수신이 중단됨 - 재시작 중...")
                stop_lidar_stream()
                time.sleep(1)
                start_lidar_stream()
                return jsonify({'success': True, 'message': 'LIDAR 스트림이 재시작되었습니다'})
            else:
                return jsonify({'success': True, 'message': 'LIDAR 스트림이 정상 작동 중입니다'})
    except Exception as e:
        print(f"❌ LIDAR 시작 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/stop_lidar', methods=['POST'])
def stop_lidar():
    """LIDAR 스트림 중지"""
    try:
        if stop_lidar_stream():
            return jsonify({'success': True, 'message': 'LIDAR 스트림이 중지되었습니다'})
        else:
            return jsonify({'success': False, 'message': 'LIDAR 스트림이 실행되지 않았습니다'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/lidar_status', methods=['GET'])
def lidar_status():
    """LIDAR 상태 확인"""
    global message_count, lidar_connection
    
    # 연결 상태 확인
    connection_state = 'unknown'
    if lidar_connection and hasattr(lidar_connection, '_peer_connection'):
        connection_state = getattr(lidar_connection._peer_connection, 'connectionState', 'unknown')
    
    return jsonify({
        'lidar_enabled': lidar_enabled,
        'lidar_view_mode': lidar_view_mode,
        'message_count': message_count,
        'connection_state': connection_state,
        'connection_healthy': connection_state in ['connected', 'connecting']
    })

@app.route('/restart_lidar', methods=['POST'])
def restart_lidar():
    """LIDAR 연결 강제 재시작"""
    try:
        print("🔄 LIDAR 연결 강제 재시작 요청")
        
        # 기존 연결 중지
        if lidar_enabled:
            print("🛑 기존 LIDAR 스트림 중지 중...")
            stop_lidar_stream()
            import time
            time.sleep(2)  # 완전히 중지되도록 대기
        
        # 새로운 연결 시작
        print("🚀 새로운 LIDAR 스트림 시작...")
        if start_lidar_stream():
            return jsonify({
                'success': True, 
                'message': 'LIDAR 연결이 재시작되었습니다',
                'restart_time': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False, 
                'message': 'LIDAR 재시작 실패'
            })
            
    except Exception as e:
        print(f"❌ LIDAR 재시작 오류: {e}")
        return jsonify({
            'success': False, 
            'error': str(e)
        })

# 🆕 SocketIO 이벤트 핸들러들
@socketio.on('connect')
def handle_connect():
    print('🔌 클라이언트 연결됨')
    emit('status', {'message': '서버에 연결되었습니다'})

@socketio.on('disconnect')
def handle_disconnect():
    print('🔌 클라이언트 연결 해제됨')

@socketio.on('check_args')
def handle_check_args():
    """LIDAR 뷰어 설정 전송"""
    typeFlag = 0b0101  # point cloud + iso camera
    typeFlagBinary = format(typeFlag, "04b")
    emit("check_args_ack", {"type": typeFlagBinary})

if __name__ == '__main__':
    print("🚀 웹 비디오 서버 시작")
    print("📊 LIDAR 3D 시각화 포함")
    print("🎮 조이스틱 제어 활성화")
    print("🔥 YOLO 화재/인물 탐지 활성화")
    if ARUCO_AVAILABLE:
        print("🆔 ArUco 신원 인증 활성화")
    else:
        print("⚠️ ArUco 신원 인증 비활성화")
    
    try:
        # SocketIO 서버 실행 (기존 Flask 대신)
        socketio.run(app, host='0.0.0.0', port=5010, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\n🛑 서버 종료")
        if lidar_enabled:
            stop_lidar_stream()
    except Exception as e:
        print(f"❌ 서버 실행 오류: {e}")
        if lidar_enabled:
            stop_lidar_stream()