import pyrealsense2 as rs, sys, time
# Hardware-reset the wrist D405 and prove it streams one depth frame.
# Lives at ~/bin/d405_reset.py on omen; this is the copy of record. After a
# cable tug the camera re-enumerates but will not stream (STREAMON I/O error,
# "no frame within 8s") until reset; the campaign script calls this and retries.
serial = "218622274622"
ctx = rs.context()
devs = [d for d in ctx.query_devices() if d.get_info(rs.camera_info.serial_number) == serial]
if not devs:
    print("D405 not on the bus"); sys.exit(2)
devs[0].hardware_reset(); time.sleep(6)
for attempt in range(3):
    try:
        p = rs.pipeline(); cfg = rs.config(); cfg.enable_device(serial)
        cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        p.start(cfg); f = p.wait_for_frames(8000); p.stop()
        print("D405 streams after reset"); sys.exit(0)
    except Exception as e:
        print("attempt", attempt, "failed:", str(e)[:100]); time.sleep(4)
sys.exit(1)
