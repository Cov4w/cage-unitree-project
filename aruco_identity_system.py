import cv2
import numpy as np
import json
import os
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.patches as patches

class ArUcoIdentitySystem:
    def __init__(self):
        """ArUco 신원 확인 시스템 초기화"""
        
        # 🔧 OpenCV 4.x 호환성 수정
        try:
            # OpenCV 4.7+ 방식
            self.aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250)
            self.aruco_params = cv2.aruco.DetectorParameters()
            self.detector = cv2.aruco.ArucoDetector(self.aruco_dict, self.aruco_params)
            self.opencv_version = "4.7+"
            print(f"✅ ArUco 시스템 초기화 완료 (OpenCV {self.opencv_version})")
        except AttributeError:
            try:
                # OpenCV 4.0-4.6 방식
                self.aruco_dict = cv2.aruco.Dictionary_get(cv2.aruco.DICT_6X6_250)
                self.aruco_params = cv2.aruco.DetectorParameters_create()
                self.detector = None
                self.opencv_version = "4.0-4.6"
                print(f"✅ ArUco 시스템 초기화 완료 (OpenCV {self.opencv_version})")
            except AttributeError:
                # OpenCV 3.x 방식 (fallback)
                self.aruco_dict = cv2.aruco.Dictionary_get(cv2.aruco.DICT_6X6_250)
                self.aruco_params = cv2.aruco.DetectorParameters_create()
                self.detector = None
                self.opencv_version = "3.x"
                print(f"✅ ArUco 시스템 초기화 완료 (OpenCV {self.opencv_version})")
        
        # 신원 정보 로드
        self.identities_file = "aruco_identities.json"
        self.identities = self.load_identities()
        
        # 🆕 마커 저장 폴더 생성
        self.markers_folder = "aruco_identity_markers"
        if not os.path.exists(self.markers_folder):
            os.makedirs(self.markers_folder)
            print(f"📁 ArUco 마커 저장 폴더 생성: {self.markers_folder}")
        
        print(f"🔖 등록된 ArUco 신원: {len(self.identities)}명")
        for marker_id, info in self.identities.items():
            print(f"   ID {marker_id}: {info.get('name', 'Unknown')} ({info.get('affiliation', 'Unknown')})")
    
    def load_identities(self):
        """JSON 파일에서 ArUco 신원 정보 로드"""
        try:
            if os.path.exists(self.identities_file):
                with open(self.identities_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('markers', {})
            else:
                print(f"⚠️ {self.identities_file} 파일이 없습니다. 빈 신원 목록으로 시작합니다.")
                return {}
        except Exception as e:
            print(f"❌ ArUco 신원 파일 로드 오류: {e}")
            return {}
    
    def detect_identity_markers(self, img):
        """이미지에서 ArUco 신원 마커 감지 및 신원 정보 반환 (JSON 안전성 개선)"""
        
        if img is None:
            return []
        
        # 그레이스케일 변환
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img
        
        try:
            # OpenCV 버전에 따른 감지 방식
            if self.opencv_version == "4.7+" and self.detector is not None:
                corners, ids, rejected = self.detector.detectMarkers(gray)
            else:
                corners, ids, rejected = cv2.aruco.detectMarkers(
                    gray, 
                    self.aruco_dict, 
                    parameters=self.aruco_params
                )
            
            detected_identities = []
            
            if ids is not None and len(ids) > 0:
                for i, marker_id in enumerate(ids.flatten()):
                    corner = corners[i][0]  # 첫 번째 마커의 모든 코너
                    
                    # 🔧 안전한 타입 변환
                    marker_id = int(marker_id.item()) if hasattr(marker_id, 'item') else int(marker_id)
                    
                    # 바운딩 박스 계산 (안전한 변환)
                    x_coords = corner[:, 0]
                    y_coords = corner[:, 1]
                    x1, y1 = int(x_coords.min()), int(y_coords.min())
                    x2, y2 = int(x_coords.max()), int(y_coords.max())
                    
                    # 마진 추가
                    margin = 20
                    x1 = max(0, x1 - margin)
                    y1 = max(0, y1 - margin)
                    x2 = min(img.shape[1], x2 + margin)
                    y2 = min(img.shape[0], y2 + margin)
                    
                    # 중심점 계산
                    center_x = int((x1 + x2) / 2)
                    center_y = int((y1 + y2) / 2)
                    
                    # 신원 정보 조회
                    identity_info = self.identities.get(str(marker_id))
                    
                    # 🔧 corners를 JSON 안전한 형태로 변환
                    safe_corners = [[float(point[0]), float(point[1])] for point in corner]
                    
                    detected_identities.append({
                        'marker_id': marker_id,  # 이미 int로 변환됨
                        'corners': safe_corners,  # JSON 안전한 형태
                        'bbox': (x1, y1, x2, y2),  # 이미 int 튜플
                        'center': (center_x, center_y),  # 이미 int 튜플
                        'identity_info': identity_info
                    })
                    
                    if identity_info:
                        print(f"🔖 ArUco 마커 감지: ID {marker_id} -> {identity_info.get('name', 'Unknown')}")
                    else:
                        print(f"❓ 알 수 없는 ArUco 마커: ID {marker_id}")
            
            return detected_identities
            
        except Exception as e:
            print(f"❌ ArUco 마커 감지 오류: {e}")
            import traceback
            print(f"🔍 상세 오류: {traceback.format_exc()}")
            return []
    
    # 🆕 ArUco 마커 생성 기능
    def generate_aruco_marker(self, marker_id, marker_size=300, total_size=400, border_size=50):
        """ArUco 마커 이미지 생성"""
        try:
            # 마커 생성
            if self.opencv_version == "4.7+":
                marker_img = cv2.aruco.generateImageMarker(self.aruco_dict, marker_id, marker_size)
            else:
                marker_img = cv2.aruco.drawMarker(self.aruco_dict, marker_id, marker_size)
            
            # 테두리 추가
            if border_size > 0:
                bordered_img = np.ones((total_size, total_size), dtype=np.uint8) * 255
                start_pos = (total_size - marker_size) // 2
                bordered_img[start_pos:start_pos+marker_size, start_pos:start_pos+marker_size] = marker_img
                marker_img = bordered_img
            
            print(f"✅ ArUco 마커 생성 완료: ID {marker_id}, 크기 {marker_size}x{marker_size}")
            return marker_img
            
        except Exception as e:
            print(f"❌ ArUco 마커 생성 실패: {e}")
            return None
    
    def save_marker_image(self, marker_img, marker_id, name, affiliation, marker_size, total_size, border_size):
        """마커 이미지를 파일로 저장"""
        try:
            # 파일명 생성 (안전한 문자만 사용)
            safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_affiliation = "".join(c for c in affiliation if c.isalnum() or c in (' ', '-', '_')).rstrip()
            
            filename = f"aruco_id_{marker_id}_{safe_name}_{safe_affiliation}.png"
            filepath = os.path.join(self.markers_folder, filename)
            
            # 이미지 저장
            cv2.imwrite(filepath, marker_img)
            
            print(f"💾 마커 이미지 저장: {filepath}")
            
            return {
                'filename': filename,
                'filepath': filepath,
                'marker_size': marker_size,
                'total_size': total_size,
                'border_size': border_size,
                'opencv_version': self.opencv_version
            }
            
        except Exception as e:
            print(f"❌ 마커 이미지 저장 실패: {e}")
            return None
    
    def create_identity_marker(self, marker_id, name, affiliation, employee_id="", role="", access_level="standard", department="", marker_size=300, total_size=400, border_size=50):
        """신원 정보와 함께 ArUco 마커 생성 및 저장"""
        try:
            # 중복 ID 확인
            if str(marker_id) in self.identities:
                print(f"⚠️ 마커 ID {marker_id}가 이미 존재합니다: {self.identities[str(marker_id)].get('name', 'Unknown')}")
                return False
            
            print(f"🔖 ArUco 신원 마커 생성 시작...")
            print(f"   ID: {marker_id}")
            print(f"   이름: {name}")
            print(f"   소속: {affiliation}")
            print(f"   직책: {role}")
            print(f"   권한: {access_level}")
            
            # 1. ArUco 마커 이미지 생성
            marker_img = self.generate_aruco_marker(marker_id, marker_size, total_size, border_size)
            if marker_img is None:
                return False
            
            # 2. 마커 이미지 저장
            file_info = self.save_marker_image(marker_img, marker_id, name, affiliation, marker_size, total_size, border_size)
            if file_info is None:
                return False
            
            # 3. 신원 데이터 생성
            identity_data = {
                'marker_id': marker_id,
                'name': name,
                'affiliation': affiliation,
                'created_date': datetime.now().isoformat(),
                'status': 'active',
                'marker_type': 'identity',
                'employee_id': employee_id,
                'role': role,
                'access_level': access_level,
                'department': department,
                **file_info  # 파일 정보 병합
            }
            
            # 4. 메모리에 추가
            self.identities[str(marker_id)] = identity_data
            
            # 5. JSON 파일 업데이트
            if self.save_identities_to_file():
                print(f"✅ ArUco 신원 마커 생성 완료!")
                print(f"📁 파일 위치: {file_info['filepath']}")
                return True
            else:
                return False
                
        except Exception as e:
            print(f"❌ ArUco 신원 마커 생성 실패: {e}")
            import traceback
            print(f"🔍 상세 오류: {traceback.format_exc()}")
            return False
    
    def save_identities_to_file(self):
        """신원 정보를 JSON 파일에 저장"""
        try:
            if os.path.exists(self.identities_file):
                with open(self.identities_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {
                    'created': datetime.now().isoformat(),
                    'version': '1.0',
                    'description': 'ArUco 마커 기반 신원 확인 시스템',
                    'opencv_version': self.opencv_version,
                    'aruco_api_version': self.opencv_version,
                    'markers': {}
                }
            
            data['markers'] = self.identities
            data['last_updated'] = datetime.now().isoformat()
            
            with open(self.identities_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
            print(f"📝 신원 정보 파일 업데이트 완료: {self.identities_file}")
            return True
            
        except Exception as e:
            print(f"❌ 신원 정보 파일 저장 실패: {e}")
            return False
    
    def add_identity(self, marker_id, name, affiliation, employee_id="", role="", access_level="standard", department=""):
        """새로운 ArUco 신원 정보 추가 (기존 함수 유지)"""
        
        identity_data = {
            'marker_id': marker_id,
            'name': name,
            'affiliation': affiliation,
            'employee_id': employee_id,
            'role': role,
            'access_level': access_level,
            'department': department,
            'created_date': datetime.now().isoformat(),
            'status': 'active',
            'marker_type': 'identity'
        }
        
        # 메모리에 추가
        self.identities[str(marker_id)] = identity_data
        
        # JSON 파일 업데이트
        return self.save_identities_to_file()
    
    def get_identity_info(self, marker_id):
        """특정 마커 ID의 신원 정보 조회"""
        return self.identities.get(str(marker_id))
    
    def list_all_identities(self):
        """등록된 모든 신원 정보 목록 반환"""
        return self.identities.copy()
    
    def remove_identity(self, marker_id):
        """ArUco 신원 정보 제거"""
        try:
            if str(marker_id) in self.identities:
                removed_info = self.identities.pop(str(marker_id))
                
                # 연관된 이미지 파일 삭제
                if 'filepath' in removed_info and os.path.exists(removed_info['filepath']):
                    os.remove(removed_info['filepath'])
                    print(f"🗑️ 마커 이미지 파일 삭제: {removed_info['filepath']}")
                
                # JSON 파일 업데이트
                if self.save_identities_to_file():
                    print(f"✅ ArUco 신원 제거 완료: ID {marker_id} -> {removed_info.get('name', 'Unknown')}")
                    return True
                else:
                    return False
            else:
                print(f"❌ 존재하지 않는 마커 ID: {marker_id}")
                return False
                
        except Exception as e:
            print(f"❌ ArUco 신원 제거 실패: {e}")
            return False
    
    # 🆕 마커 정보 표시 기능
    def display_marker_info(self, marker_id):
        """특정 마커의 상세 정보 표시"""
        identity = self.get_identity_info(marker_id)
        
        if identity:
            print(f"\n🔖 ArUco 마커 ID {marker_id} 정보:")
            print(f"   👤 이름: {identity.get('name', 'Unknown')}")
            print(f"   🏢 소속: {identity.get('affiliation', 'Unknown')}")
            print(f"   💼 직책: {identity.get('role', 'N/A')}")
            print(f"   🏛️ 부서: {identity.get('department', 'N/A')}")
            print(f"   🆔 사번: {identity.get('employee_id', 'N/A')}")
            print(f"   🔑 권한: {identity.get('access_level', 'standard')}")
            print(f"   📅 생성일: {identity.get('created_date', 'Unknown')}")
            print(f"   📁 파일: {identity.get('filename', 'N/A')}")
            print(f"   🖼️ 크기: {identity.get('marker_size', 'Unknown')}px")
            return True
        else:
            print(f"❌ 마커 ID {marker_id}를 찾을 수 없습니다.")
            return False
    
    # 🆕 사용 가능한 ID 검색
    def get_next_available_id(self, start_id=1):
        """사용 가능한 다음 마커 ID 찾기"""
        current_id = start_id
        while str(current_id) in self.identities:
            current_id += 1
        return current_id

# 🆕 마커 생성 도우미 함수들
def create_sample_markers():
    """샘플 ArUco 마커들 생성"""
    print("🔖 샘플 ArUco 마커 생성 시작...")
    
    aruco_system = ArUcoIdentitySystem()
    
    # 샘플 사용자 데이터
    sample_users = [
        {
            'marker_id': 20,
            'name': '박지민',
            'affiliation': '연구팀',
            'employee_id': 'EMP020',
            'role': '연구원',
            'access_level': 'standard',
            'department': '기술연구소'
        },
        {
            'marker_id': 21,
            'name': '이하늘',
            'affiliation': '보안팀',
            'employee_id': 'EMP021',
            'role': '보안 관리자',
            'access_level': 'admin',
            'department': '보안관리부'
        },
        {
            'marker_id': 22,
            'name': '김현우',
            'affiliation': '개발팀',
            'employee_id': 'EMP022',
            'role': '시니어 개발자',
            'access_level': 'standard',
            'department': '소프트웨어개발부'
        }
    ]
    
    success_count = 0
    for user in sample_users:
        if aruco_system.create_identity_marker(**user):
            success_count += 1
        print()  # 줄바꿈
    
    print(f"🎉 샘플 마커 생성 완료: {success_count}/{len(sample_users)}개 성공")

# 테스트 및 초기화 함수
def test_aruco_system():
    """ArUco 시스템 테스트"""
    print("🧪 ArUco 신원 시스템 테스트 시작...")
    
    try:
        # 시스템 초기화
        aruco_system = ArUcoIdentitySystem()
        
        # 기본 신원 정보 확인
        identities = aruco_system.list_all_identities()
        print(f"📋 등록된 신원 수: {len(identities)}")
        
        for marker_id, info in identities.items():
            print(f"   🔖 ID {marker_id}: {info.get('name', 'Unknown')} ({info.get('affiliation', 'Unknown')})")
        
        print("✅ ArUco 신원 시스템 테스트 완료!")
        return True
        
    except Exception as e:
        print(f"❌ ArUco 시스템 테스트 실패: {e}")
        import traceback
        print(f"🔍 상세 오류: {traceback.format_exc()}")
        return False

def interactive_marker_creator():
    """대화형 ArUco 마커 생성기"""
    print("🔖 ArUco 신원 마커 생성기")
    print("=" * 50)
    
    try:
        aruco_system = ArUcoIdentitySystem()
        
        # 사용자 입력 받기
        print("\n📝 신원 정보를 입력해주세요:")
        
        # 사용 가능한 ID 제안
        next_id = aruco_system.get_next_available_id(1)
        marker_id_input = input(f"마커 ID (추천: {next_id}): ").strip()
        marker_id = int(marker_id_input) if marker_id_input else next_id
        
        name = input("이름: ").strip()
        affiliation = input("소속: ").strip()
        employee_id = input("사번 (선택): ").strip()
        role = input("직책 (선택): ").strip()
        department = input("부서 (선택): ").strip()
        
        # 권한 레벨 선택
        print("\n🔑 접근 권한 레벨:")
        print("1. standard (일반)")
        print("2. admin (관리자)")
        print("3. super_admin (최고 관리자)")
        print("4. emergency (응급 권한)")
        
        access_choice = input("권한 선택 (1-4, 기본값: 1): ").strip()
        access_levels = {
            '1': 'standard',
            '2': 'admin', 
            '3': 'super_admin',
            '4': 'emergency'
        }
        access_level = access_levels.get(access_choice, 'standard')
        
        print(f"\n🔖 생성할 마커 정보:")
        print(f"   ID: {marker_id}")
        print(f"   이름: {name}")
        print(f"   소속: {affiliation}")
        print(f"   권한: {access_level}")
        
        confirm = input("\n생성하시겠습니까? (y/N): ").strip().lower()
        if confirm == 'y':
            if aruco_system.create_identity_marker(
                marker_id=marker_id,
                name=name,
                affiliation=affiliation,
                employee_id=employee_id,
                role=role,
                access_level=access_level,
                department=department
            ):
                print(f"🎉 ArUco 마커 생성 성공!")
                aruco_system.display_marker_info(marker_id)
            else:
                print(f"❌ ArUco 마커 생성 실패")
        else:
            print("🚫 마커 생성이 취소되었습니다.")
            
    except Exception as e:
        print(f"❌ 마커 생성기 오류: {e}")
        import traceback
        print(f"🔍 상세 오류: {traceback.format_exc()}")

if __name__ == "__main__":
    print("🔖 ArUco 신원 확인 시스템")
    print("=" * 50)
    print("1. 시스템 테스트")
    print("2. 대화형 마커 생성")
    print("3. 샘플 마커 생성")
    print("4. 종료")
    
    choice = input("\n선택하세요 (1-4): ").strip()
    
    if choice == '1':
        test_aruco_system()
    elif choice == '2':
        interactive_marker_creator()
    elif choice == '3':
        create_sample_markers()
    elif choice == '4':
        print("👋 프로그램을 종료합니다.")
    else:
        print("❌ 잘못된 선택입니다.")