import time

import psutil


def get_system_stats():
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_usage": cpu_percent,
            "ram_usage": memory.percent,
            "disk_usage": disk.percent,
            "uptime": int(time.time() - psutil.boot_time()),
        }
    except Exception:
        return {"cpu_usage": 0.0, "ram_usage": 0.0, "disk_usage": 0.0, "uptime": 0}
