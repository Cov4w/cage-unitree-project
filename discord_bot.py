import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import json
from datetime import datetime
from webrtc_producer import get_robot_bms_status, get_bms_state, get_robot_status

# 환경변수 로드
load_dotenv()

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True  # 메시지 내용 읽기 권한
bot = commands.Bot(command_prefix='!', intents=intents)

# 🆕 다중 채널/서버 설정
FIRE_ALERT_CHANNELS = [
    {
        'channel_id': 989834859063148564,    # 첫 번째 채널 ID
        'role_id': 1407168354208186478,      # 첫 번째 역할 ID
        'server_name': '메인 서버'
    }
]
''',
    {
        'channel_id': 1234567890123456789,   # 두 번째 채널 ID (다른 서버)
        'role_id': 9876543210987654321,      # 두 번째 역할 ID
        'server_name': '백업 서버'
    }, 
    # 필요에 따라 더 추가 가능
]
'''
@bot.event
async def on_ready():
    print(f'{bot.user}로 로그인했습니다!')
    print(f'봇 ID: {bot.user.id}')
    print('------')
    
    # 🚨 Fire 알림 감시 태스크 시작
    bot.loop.create_task(monitor_fire_alerts())

@bot.event
async def on_message(message):
    # 봇 자신의 메시지는 무시
    if message.author == bot.user:
        return
    
    # 명령어 처리
    await bot.process_commands(message)

# 기본 명령어들
@bot.command(name='hello')
async def hello(ctx):
    """인사 명령어"""
    await ctx.send(f'안녕하세요, {ctx.author.mention}님!')

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
            embed.add_field(
                name="🤖 로봇 상태", 
                value=robot_status['robot_state'], 
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
        embed.add_field(name="사이클", value=f"{bms_state['cycle']}회", inline=True)
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
    """Fire 알림 파일 감시 (반복 알림 지원)"""
    print("🔥 Fire 알림 감시 시작...")
    last_alert_time = 0
    
    while True:
        try:
            if os.path.exists('.fire_alert.json'):
                # 파일 수정 시간 확인
                file_time = os.path.getmtime('.fire_alert.json')
                
                if file_time > last_alert_time:
                    # 새로운 알림 파일 발견
                    with open('.fire_alert.json', 'r') as f:
                        alert_data = json.load(f)
                    
                    # 🆕 모든 설정된 채널에 알림 전송
                    await send_fire_alert_to_all_channels(alert_data)
                    last_alert_time = file_time
                    
                    # 처리된 알림 파일 삭제
                    os.remove('.fire_alert.json')
                    
        except Exception as e:
            print(f"❌ 알림 감시 오류: {e}")
        
        await asyncio.sleep(0.5)  # 0.5초마다 확인 (더 빠른 반응)

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

@bot.command(name='fire_channels')
async def fire_channels(ctx):
    """설정된 화재 알림 채널 목록"""
    embed = discord.Embed(
        title="🔥 화재 알림 채널 설정",
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
        name="⚙️ 알림 설정",
        value="• 첫 알림: 5초 후\n• 반복 알림: 5초마다\n• 신뢰도: 50% 이상",
        inline=False
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