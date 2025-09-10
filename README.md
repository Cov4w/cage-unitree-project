# TEAM - CAGE
## 🛠️ Unitree Go2 Patrol Project
## 📎 팀원
- [김태민 (나사렛대학교)](https://github.com/gomtam/) - 팀장, ROS2 환경 구축, SLAM/NAVI 연구
- ~[이수 (우송대학교)](https://github.com/2siuuuu/) - 개발환경 구축, webRTC 기반 구축~
- ~[김한솔 (우송대학교)](https://github.com/one1212/) - UI/UX 디자인 담당~
- [최승균 (우송대학교)](https://github.com/Cov4w/) - webRTC 기반 구축, 웹 <-> 로봇 제어 기능 추가, 디스코드 봇 개발 및 기능 추가
- [김상혁 (우송대학교)](https://github.com/DevHyeok01/) - ROS2 환경 구축, SLAM/NAVI 연구, unitree 운영체제 유지보수

## 의존성 설치
[설치 방법](https://github.com/Cov4w/cage-unitree-project/blob/main/readme2.md)
## webRTC 오픈소스
[오픈소스 출처](https://github.com/legion1581/go2_webrtc_connect)

<div>
  <h2>
    ✅ 주요 기능
  </h2>
  <ul>
    <li><a href="#slamnavi">SLAM/NAVI ROS2 패키지를 이용한 자율 주행 순찰</a></li>
    <li><a href="#fire">yolov8, 열 센서를 이용한 화재 감지</a></li>
    <li><a href="#idc">순찰 중 신원 확인</a></li>
    <li><a href="#loc">Aruco 마커를 이용한 현재 지역 확인</a></li>
    <li><a href="#alarm">화재 감지 및 신원 확인 문제 발생시 디스코드 봇을 통해 알림 전송</a></li>
    <li><a href="#audio">실시간 양방향 음성 대화 기능</a></li>    
  </ul>
  <h2>
    📝 버전 기록
  </h2>
  <ul>
    <li><a href="#vhis_0.1.0">버전 0.1.0</a></li>
  </ul>
</div>

<div id = "slamnavi">
  <h2>📌 SLAM/NAVI ROS2 패키지를 이용한 자율 주행 순찰</h2>
</div>

### SLAM을 활용한 맵핑
#### - ROS2 SlamToolbox plugin을 활용해 맵 저장 기능 연구중

<div id = "fire">
  <h2>📌 yolov8, 열 센서를 이용한 화재 감지</h2>
</div>

### 맞춤 데이터 셋을 활용해 yolov8n 기반 객체 판별 모델 간단 적용
#### person, fire 각 약 8000,5000 장의 roboflow 오픈소스 데이터 셋을 활용해 학습

<div id = "idc">
  <h2>📌 순찰 중 신원 확인</h2>
</div>

### Aruco 마커를 활용한 신원 확인 테스트 모델 제작
#### aruco_identity_system.py 를 활용해 데이터 추가 및 새로운 Aruco 마커 제작

1. 멀티 OpenCV 지원
  - OpenCV 3.x, 4.0-4.6, 4.7+ 모든 버전 호환
  - 자동 버전 감지 및 적절한 API 사용
2. JSON 안전성
  - NumPy 배열 자동 변환
  - 타입 안전성 보장
  - 인코딩 문제 해결
3. 실시간 처리
  - 비디오 스트림에서 실시간 감지
  - 웹 인터페이스 실시간 업데이트
  - 스캔 상태 모니터링
4. 확장성
  - 새로운 마커 쉽게 추가
  - 권한 시스템 확장 가능
  - 다양한 정보 필드 지원


<div id = "loc">
  <h2>📌 Aruco 마커를 이용한 현재 지역 확인</h2>
</div>

<div id = "alarm">
  <h2>📌 화재 감지 및 신원 확인 문제 발생시 디스코드 봇을 통해 알림 전송</h2>
</div>

1. 문제 발생 시 웹 서버에서 JSON 형태로 데이터 파일 제작
2. 디스코드 봇이 JSON 파일 감지 후 데이터 파일 활용해 알림 전송
3. 알림 전송 후 사용된 JSON 파일 삭제

<div id = "audio">
  <h2>📌 실시간 양방향 음성 대화 기능</h2>
</div>

## 📝 버전 기록
<div id = "vhis_0.1.0">
  <h3>
    v 0.1.0 <br> 제작 기간 2025/06/13 ~ 2025/8/11 <br> dmc 코넷 아이디어 콘테스트
  </h3>
</div>

- webRTC를 이용해 로봇 원격 제어 구현
- 웹 페이지 상 조이스틱을 이용해 움직임 제어
- 로봇 동작 id를 사용해 특정 행동 제어
- coco 데이터셋 기반 yolov11n을 이용해 실시간 객체 판별 기능 테스트
