from datetime import datetime, timedelta
import email.utils
import time

def simulate_alignment(interval, time_offset):
    now_local = datetime.now()
    now_net = now_local + timedelta(seconds=time_offset)
    
    print(f"Local Time: {now_local.strftime('%H:%M:%S')}")
    print(f"Net Time  : {now_net.strftime('%H:%M:%S')} (Offset: {time_offset}s)")
    
    if interval < 60:
        next_min = ((now_net.minute // interval) + 1) * interval
        next_net = now_net.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=next_min)
    else:
        hours = interval // 60
        next_hour = ((now_net.hour // hours) + 1) * hours
        next_net = now_net.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=next_hour)
    
    start_date_local = next_net - timedelta(seconds=time_offset)
    
    if (start_date_local - now_local).total_seconds() < 5:
        start_date_local += timedelta(minutes=interval)
        next_net += timedelta(minutes=interval)

    print(f"Target Net Time: {next_net.strftime('%H:%M:%S')}")
    print(f"Start Local Time: {start_date_local.strftime('%H:%M:%S')}")
    print("-" * 30)

print("Test Case 1: Interval 10, Network 30s ahead")
simulate_alignment(10, 30)

print("Test Case 2: Interval 10, Network 30s behind")
simulate_alignment(10, -30)

print("Test Case 3: Interval 60, Network 5m ahead")
simulate_alignment(60, 300)
