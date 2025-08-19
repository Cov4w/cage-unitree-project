import cv2
import time
from flask import Flask, Response, render_template, request, jsonify
from multiprocessing import Queue
from webrtc_producer import start_webrtc, send_command, ensure_normal_mode_once
import threading
from ultralytics import YOLO  # YOLO 모델 임포트
import logging
import json
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder='templates')
frame_queue = Queue(maxsize=10)
command_queue = Queue(maxsize=10)

# YOLO 모델 로드
yolo_model = YOLO('templates/best.pt')  # 모델 파일 경로


# WebRTC 프레임 수신 시작 (명령 큐도 전달)
start_webrtc(frame_queue, command_queue)

# 🔥 Fire 감지 추적 변수들 (수정)
fire_detection_start_time = None
fire_continuous_detection = False
fire_last_alert_time = None  # 🆕 마지막 알림 전송 시간
FIRE_DETECTION_THRESHOLD = 5.0
FIRE_CONFIDENCE_THRESHOLD = 0.5
FIRE_ALERT_INTERVAL = 5.0  # 🆕 알림 간격 (5초)

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
        'is_repeat': is_repeat,  # 🆕 반복 알림 여부
        'alert_count': int(detection_duration // FIRE_ALERT_INTERVAL) + 1,  # 🆕 알림 횟수
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
            # 🆕 새로운 감지 시작
            fire_detection_start_time = current_time
            fire_continuous_detection = True
            fire_last_alert_time = None  # 알림 시간 초기화
            print(f"🔥 Fire 감지 시작! (신뢰도 {max_confidence:.2f})")
        
        # 연속 감지 시간 확인
        detection_duration = current_time - fire_detection_start_time
        
        # 🆕 첫 알림 조건 (5초 후)
        if detection_duration >= FIRE_DETECTION_THRESHOLD and fire_last_alert_time is None:
            print(f"🚨 화재 첫 알림! ({detection_duration:.1f}초 연속 감지)")
            save_fire_alert(is_repeat=False)
            fire_last_alert_time = current_time
            
        # 🆕 반복 알림 조건 (5초마다)
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
    while True:
        if not frame_queue.empty():
            img = frame_queue.get()
            now = time.time()
            
            if now - last_detect_time > 1.0:
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
                
                # 🔥 Fire 감지 상태 확인
                check_fire_detection(last_boxes)
                
                last_detect_time = now
            
            # 이전 결과(박스)만 영상에 표시
            for box_info in last_boxes:
                if len(box_info) == 6:  # 새로운 형식
                    x1, y1, x2, y2, label, confidence = box_info
                else:  # 기존 형식 호환
                    x1, y1, x2, y2 = box_info[:4]
                    label = "person"
                    confidence = 0.0
                
                # 클래스별 색상 설정
                if label == "fire":
                    color = (0, 0, 255)      # 빨간색 (BGR)
                    display_text = f"FIRE {confidence:.2f}"
                    
                    # 🚨 고신뢰도 Fire + 연속 감지 중이면 깜빡임
                    if confidence >= FIRE_CONFIDENCE_THRESHOLD and fire_continuous_detection:
                        if int(time.time() * 2) % 2:  # 깜빡임 효과
                            color = (0, 255, 255)  # 노란색
                        display_text = f"🚨 FIRE {confidence:.2f}"
                        
                elif label == "person":
                    color = (0, 255, 0)      # 초록색 (BGR)
                    display_text = f"PERSON {confidence:.2f}"
                
                # 박스와 텍스트 그리기
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                cv2.putText(img, display_text, (x1, y1 - 10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            
            # 🔥 연속 감지 상태 표시
            if fire_continuous_detection and fire_detection_start_time:
                detection_duration = time.time() - fire_detection_start_time
                status_text = f"🔥 Fire 감지중: {detection_duration:.1f}s"
                
                if detection_duration >= FIRE_DETECTION_THRESHOLD:
                    status_color = (0, 0, 255)  # 빨간색
                    if fire_last_alert_time is not None:
                        # 알림 준비 중일 때 상태 표시
                        elapsed_alert_time = time.time() - fire_last_alert_time
                        if elapsed_alert_time < FIRE_ALERT_INTERVAL:
                            status_text += f" - 알림 준비중 ({elapsed_alert_time:.1f}s)"
                else:
                    status_color = (0, 165, 255)  # 주황색
                
                cv2.putText(img, status_text, (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
            
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
    

if __name__ == "__main__":
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