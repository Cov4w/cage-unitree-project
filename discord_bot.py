import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import json
from datetime import datetime, timedelta
from webrtc_producer import get_robot_bms_status, get_bms_state, get_robot_status

# 환경변수 로드
load_dotenv()

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True  # 메시지 내용 읽기 권한
bot = commands.Bot(command_prefix='!', intents=intents)

# 🆕 ArUco 시스템 상수 정의 (web_video_server.py와 동일하게)
MAX_ARUCO_ATTEMPTS = 10
ARUCO_SCAN_TIMEOUT = 30.0
ARUCO_RETRY_INTERVAL = 2.0

# 🆕 사용자별 알림 중복 방지 설정
IDENTITY_ALERT_COOLDOWN = 300  # 5분 (300초) - 같은 사용자 재알림 간격
IDENTITY_DAILY_RESET_HOUR = 6  # 오전 6시에 일일 알림 기록 초기화

# 🆕 다중 채널/서버 설정
FIRE_ALERT_CHANNELS = [
    {
        'channel_id': 989834859063148564,    # 첫 번째 채널 ID
        'role_id': 1407168354208186478,      # 첫 번째 역할 ID
        'server_name': '메인 서버'
    },
    {
        'channel_id': 1407178115347644456,   # 두 번째 채널 ID (다른 서버)
        'role_id': 1407168743104188517,      # 두 번째 역할 ID
        'server_name': '백업 서버'
    }, 
    # 필요에 따라 더 추가 가능
]

# 🔧 알림 중복 방지를 위한 전역 변수
processed_fire_alerts = set()     # 처리된 화재 알림 ID 저장
processed_aruco_scans = set()     # 처리된 ArUco 스캔 ID 저장
last_fire_alert_id = ""           # 마지막 화재 알림 ID
last_aruco_scan_id = ""           # 마지막 ArUco 스캔 ID

# 🆕 사용자별 알림 기록 (중복 방지)
user_identity_alerts = {}         # {user_key: last_alert_time}
daily_identity_log = {}           # {date: {user_key: alert_count}}

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

@bot.event
async def on_ready():
    print(f'{bot.user}로 로그인했습니다!')
    print(f'봇 ID: {bot.user.id}')
    print(f'🔖 신원 알림 쿨다운: {IDENTITY_ALERT_COOLDOWN}초 ({IDENTITY_ALERT_COOLDOWN//60}분)')
    print(f'🕕 일일 기록 초기화: 매일 오전 {IDENTITY_DAILY_RESET_HOUR}시')
    print('------')
    
    # 🆕 모든 감시 태스크 시작
    bot.loop.create_task(monitor_fire_alerts())
    bot.loop.create_task(monitor_aruco_scan_results())

@bot.event
async def on_message(message):
    # 봇 자신의 메시지는 무시
    if message.author == bot.user:
        return
    
    # 명령어 처리
    await bot.process_commands(message)

def generate_alert_id(alert_data):
    """알림 데이터에서 고유 ID 생성"""
    timestamp = alert_data.get('timestamp', '')
    alert_type = alert_data.get('alert_type', '')
    duration = alert_data.get('duration', 0)
    is_repeat = alert_data.get('is_repeat', False)
    attempts = alert_data.get('attempts', 0)
    
    # 고유 ID 생성 (timestamp + type + duration + repeat + attempts)
    return f"{timestamp}_{alert_type}_{duration}_{is_repeat}_{attempts}"

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
    
    # 로봇 상태 가져오기
    try:
        # 현재 BMS 상태 즉시 확인
        bms_state = get_bms_state()
        robot_status = get_robot_status()
        
        if bms_state:
            # BMS 상태 필드 추가
            soc = bms_state['soc']
            embed.add_field(
                name="🔋 배터리 잔량", 
                value=f"{soc}%", 
                inline=True
            )
            embed.add_field(
                name="⚡ 전류", 
                value=f"{bms_state['current']} mA", 
                inline=True
            )
            embed.add_field(
                name="🔄 충전 사이클", 
                value=f"{bms_state['cycle']}회", 
                inline=True
            )
            embed.add_field(
                name="🌡️ BQ 온도", 
                value=f"{bms_state['bq_ntc']}°C", 
                inline=True
            )
            embed.add_field(
                name="🌡️ MCU 온도", 
                value=f"{bms_state['mcu_ntc']}°C", 
                inline=True
            )
            
            # 배터리 상태에 따른 색상 변경
            if soc >= 70:
                embed.color = 0x00ff00  # 녹색 (좋음)
            elif soc >= 30:
                embed.color = 0xffff00  # 노란색 (보통)
            else:
                embed.color = 0xff0000  # 빨간색 (낮음)
                
        else:
            embed.add_field(
                name="❌ 로봇 상태", 
                value="BMS 상태 데이터 없음", 
                inline=False
            )
            embed.add_field(
                name="🔗 연결 상태", 
                value=robot_status['connection_status'], 
                inline=True
            )
            embed.color = 0xff0000
            
    except Exception as e:
        embed.add_field(
            name="❌ 오류", 
            value=f"상태 확인 중 오류: {str(e)}", 
            inline=False
        )
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
        
        embed.add_field(
            name="배터리 잔량",
            value=f"{soc}% ({status_text})",
            inline=False
        )
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

# 🆕 수동 테스트 명령어들
@bot.command(name='fire_test')
@commands.has_permissions(administrator=True)
async def fire_test(ctx):
    """화재 알림 테스트 (첫 알림)"""
    test_alert = {
        'timestamp': datetime.now().isoformat(),
        'duration': 5.2,
        'confidence': 'high',
        'is_repeat': False,
        'alert_count': 1,
        'message': 'TEST 첫 알림'
    }
    
    await send_fire_alert_to_all_channels(test_alert)
    await ctx.send("🔥 화재 첫 알림 테스트 전송됨!")

@bot.command(name='fire_test_repeat')
@commands.has_permissions(administrator=True)
async def fire_test_repeat(ctx):
    """화재 반복 알림 테스트"""
    test_alert = {
        'timestamp': datetime.now().isoformat(),
        'duration': 23.7,
        'confidence': 'high',
        'is_repeat': True,
        'alert_count': 5,
        'message': 'TEST 반복 알림'
    }
    
    await send_fire_alert_to_all_channels(test_alert)
    await ctx.send("🔥 화재 반복 알림 테스트 전송됨!")

@bot.command(name='aruco_test_success')
@commands.has_permissions(administrator=True)
async def aruco_test_success(ctx):
    """ArUco 스캔 성공 알림 테스트"""
    test_success = {
        'timestamp': datetime.now().isoformat(),
        'alert_type': 'aruco_identity_success',
        'marker_info': {
            'marker_id': 10,
            'name': '최승균',
            'affiliation': '개발팀',
            'employee_id': 'EMP001',
            'role': '팀장',
            'access_level': 'admin',
            'department': '기술개발부'
        },
        'scan_info': {
            'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'location': 'unitree_camera',
            'attempts': 3,
            'scanner': 'aruco_identity_system'
        },
        'message': 'ArUco 신원 스캔 성공 테스트'
    }
    
    await send_aruco_identity_to_all_channels(test_success)
    await ctx.send("🔖 ArUco 스캔 성공 알림 테스트 전송됨!")

@bot.command(name='aruco_test_fail')
@commands.has_permissions(administrator=True)
async def aruco_test_fail(ctx):
    """ArUco 스캔 실패 알림 테스트"""
    test_failure = {
        'timestamp': datetime.now().isoformat(),
        'alert_type': 'aruco_identity_failure',
        'failure_info': {
            'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'location': 'unitree_camera',
            'attempts': MAX_ARUCO_ATTEMPTS,
            'max_attempts': MAX_ARUCO_ATTEMPTS,
            'timeout': ARUCO_SCAN_TIMEOUT,
            'scan_duration': ARUCO_SCAN_TIMEOUT
        },
        'message': f'ArUco 신원 마커 스캔 실패 테스트 ({MAX_ARUCO_ATTEMPTS}번 시도)'
    }
    
    await send_aruco_identity_to_all_channels(test_failure)
    await ctx.send("❌ ArUco 스캔 실패 알림 테스트 전송됨!")

@bot.command(name='fire_channels')
async def fire_channels(ctx):
    """설정된 화재 알림 채널 목록"""
    embed = discord.Embed(
        title="🔥 알림 채널 설정",
        color=0x00ff00
    )
    
    for i, config in enumerate(FIRE_ALERT_CHANNELS, 1):
        channel = bot.get_channel(config['channel_id'])
        channel_name = channel.name if channel else "채널 없음"
        
        embed.add_field(
            name=f"📍 {config['server_name']} #{i}",
            value=f"채널: #{channel_name}\n채널 ID: {config['channel_id']}\n역할 ID: {config['role_id']}",
            inline=False
        )
    
    embed.add_field(
        name="⚙️ 화재 알림 설정",
        value="• 첫 알림: 5초 후\n• 반복 알림: 5초마다\n• 신뢰도: 50% 이상",
        inline=True
    )
    embed.add_field(
        name="⚙️ ArUco 알림 설정",
        value="• 쿨다운: 5분\n• 일일 최대: 10회/사용자\n• 재시도: 2초 간격",
        inline=True
    )
    
    await ctx.send(embed=embed)

# 봇 실행
if __name__ == "__main__":
    # 토큰 확인
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        print("❌ DISCORD_TOKEN이 설정되지 않았습니다!")
        print("📝 .env 파일에 DISCORD_TOKEN을 설정해주세요.")
        exit(1)
    
    try:
        # 봇 실행
        bot.run(token)
    except discord.LoginFailure:
        print("❌ Discord 토큰이 유효하지 않습니다!")
    except Exception as e:
        print(f"❌ 봇 실행 중 오류 발생: {e}")